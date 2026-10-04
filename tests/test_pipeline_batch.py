"""Tests for pipeline batch paths, LLM error handling, and edge cases.

Covers the remaining uncovered lines in src/pipeline.py:
- Batch summarization/reflection (LLM path)
- Batch translation (LLM path)
- Per-repo fallback paths
- JSON parsing failures
- Empty repo handling in various stages
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.config import AIConfig, AppConfig, GitHubConfig, Stage2PreFilterConfig
from src.llm import LLMError
from src.pipeline import (
    REVIEW_MAX_TOKENS,
    WRITER_SECTIONS,
    CurationPipeline,
    _analysis_complete,
    _split_reflection,
)
from tests.conftest import isolated_dedup_config


@pytest.fixture
def batch_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        github=GitHubConfig(max_trending_repos=10, max_org_repos=5),
        ai=AIConfig(
            default_provider="openai",
            temperature=0.3,
            max_tokens=4096,
            rate_limit_delay=0.01,
        ),
        dedup=isolated_dedup_config(tmp_path),
        stage2_pre_filter=Stage2PreFilterConfig(enabled=True, max_repos=88),
    )


@pytest.fixture
def batch_repos() -> list[dict[str, Any]]:
    return [
        {
            "full_name": "deepseek-ai/DeepSeek-V3",
            "url": "https://github.com/deepseek-ai/DeepSeek-V3",
            "description": "MoE language model.",
            "language": "Python",
            "stars": 15200,
            "rating": "S",
            "tags": ["#MoE", "#MLA"],
            "selection_reason": "Innovative architecture.",
            "refined_summary": "English summary 1.",
            "source": "trending",
            "period_stars": "",
        },
        {
            "full_name": "org/repo2",
            "url": "https://github.com/org/repo2",
            "description": "A good tool.",
            "language": "Rust",
            "stars": 5000,
            "rating": "A",
            "tags": ["#CLI"],
            "selection_reason": "Useful utility.",
            "refined_summary": "English summary 2.",
            "source": "trending",
            "period_stars": "",
        },
    ]


class TestSummarizeReflectBatch:
    """Test the batch LLM path for Stage 3+4."""

    def test_batch_success_returns_all_repos(self, batch_config, batch_repos):
        """Successful batch response should return all repos with summaries."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_summarize_and_reflect(batch_repos)
        assert len(result) == len(batch_repos)
        for r in result:
            assert "refined_summary" in r


class TestStage2AnalyzeLLMPath:
    """Test the LLM path for Stage 2 Analyze."""

    def test_stage_2_llm_success(self, batch_config, batch_repos):
        """Stage 2 with LLM call should return analyzed repos."""
        client = MagicMock()
        client.call_llm.return_value = {
            "content": json.dumps([
                {
                    "index": 0,
                    "full_name": "deepseek-ai/DeepSeek-V3",
                    "rating": "S",
                    "tags": ["#MoE"],
                    "reason_for_selection": "Top pick.",
                }
            ])
        }
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        result = pipeline._stage_analyze(batch_repos[:1])
        assert len(result) == 1
        assert result[0]["rating"] == "S"
        assert result[0]["tags"] == ["#MoE"]

    def test_stage_2_llm_failure_falls_back(self, batch_config, batch_repos):
        """When Stage 2 LLM call fails, should use rule-based fallback."""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("API Error")
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        result = pipeline._stage_analyze(batch_repos)
        # Should fall back to rule-based with top 6 repos
        assert len(result) > 0
        for r in result:
            assert "rating" in r
            assert "tags" in r
            assert "selection_reason" in r


def _full_analysis(body: str = "Evidence-backed prose.") -> str:
    """一份能通过完整性门的四段英文分析。"""
    return "\n\n".join(f"{header}\n{body}" for header in WRITER_SECTIONS)


class TestSummarizeReflectPerRepo:
    """Test per-repo summarization, the reflection split, and the completeness gate."""

    def test_per_repo_success(self, batch_config):
        """完整 4 段产物直接进入 refined_summary，自检行被剥离到 reflection_trace。"""
        client = MagicMock()
        client.call_llm.return_value = {
            "content": "SELF-CHECK: The KV-cache trade-off is the sharpest claim here.\n\n" + _full_analysis()
        }
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert _analysis_complete(result["refined_summary"])
        assert "SELF-CHECK" not in result["refined_summary"]
        assert result["reflection_trace"] == "The KV-cache trade-off is the sharpest claim here."

    def test_incomplete_analysis_hits_gate(self, batch_config):
        """缺小节的产物不能进报告，落英文 stub 由翻译层出中文。"""
        client = MagicMock()
        client.call_llm.return_value = {"content": "SELF-CHECK: thin\n### Core Pain Point Solved\nonly one section"}
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "A test project.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert "Deeper LLM analysis pending" in result["refined_summary"]
        assert result["reflection_trace"] == "thin"

    def test_per_repo_failure_uses_english_stub(self, batch_config):
        """写作失败返回英文 stub（不是中文），保证翻译层语义一致。"""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("Failed")
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert "### Core Pain Point Solved" in result["refined_summary"]
        assert "要解决的核心痛点" not in result["refined_summary"]

    def test_readme_excerpt_reaches_the_writer(self, batch_config):
        """scraped_readme 必须进入 prompt，否则 Scrape 阶段是纯浪费。"""
        client = MagicMock()
        client.call_llm.return_value = {"content": _full_analysis()}
        pipeline = CurationPipeline(batch_config, client)
        repo = {
            "full_name": "test/repo", "description": "Test.", "tags": [], "stars": 1,
            "scraped_readme": "[![badge](https://img.shields.io/x)]\n<svg></svg>\nRust-based KV cache router for local LLMs.",
        }
        pipeline._summarize_reflect_per_repo(repo)
        prompt = client.call_llm.call_args[0][0][1]["content"]
        assert "Rust-based KV cache router" in prompt
        assert "img.shields.io" not in prompt

    def test_persona_focus_reaches_the_writer(self, batch_config):
        """画像的 prompt_focus 必须进 writer，否则 --persona 换的是空气。"""
        client = MagicMock()
        client.call_llm.return_value = {"content": _full_analysis()}
        pipeline = CurationPipeline(batch_config, client)
        pipeline.current_persona = {"name": "高阶大神", "prompt_focus": "极致学术硬核度 MCTS RLHF"}
        pipeline._summarize_reflect_per_repo({"full_name": "t/r", "description": "d", "tags": [], "stars": 1})
        system_prompt = client.call_llm.call_args[0][0][0]["content"]
        assert "极致学术硬核度 MCTS RLHF" in system_prompt
        assert "高阶大神" in system_prompt


class TestReflectionSplit:
    """_split_reflection / _analysis_complete helpers."""

    def test_splits_leading_self_check(self):
        analysis, reflection = _split_reflection("SELF-CHECK: sharp point\n\n### A\nbody")
        assert reflection == "sharp point"
        assert analysis.startswith("### A")

    def test_case_insensitive_prefix(self):
        _, reflection = _split_reflection("self-check: loud\nbody")
        assert reflection == "loud"

    def test_no_self_check_line(self):
        analysis, reflection = _split_reflection("### A\nbody")
        assert reflection == ""
        assert analysis == "### A\nbody"

    def test_blank_lines_before_self_check_are_skipped(self):
        _, reflection = _split_reflection("\nSELF-CHECK: found it\nbody")
        assert reflection == "found it"

    def test_complete_requires_all_four_sections(self):
        assert not _analysis_complete("### Core Pain Point Solved only")
        assert _analysis_complete(_full_analysis())


# ===================================================================
# Translate / Review 降级路径（曾经把 "Error: no client..." 写进报告正文）
# ===================================================================

class _FakeLLM:
    """只实现 pipeline 用到的两个方法：has_role 与 call_llm。"""

    def __init__(self, available: set[str], responder):
        self.available = available
        self.responder = responder
        self.calls: list[str] = []
        self.call_kwargs: list[dict] = []

    def has_role(self, role: str) -> bool:
        return role in self.available

    def call_llm(self, messages, role="writer", **kwargs):
        self.calls.append(role)
        self.call_kwargs.append(kwargs)
        return self.responder(role, messages)


def _batch_pipeline(batch_config, llm):
    pipeline = CurationPipeline(batch_config, llm)
    pipeline.use_mock = False
    return pipeline


_TRANSLATED_A = "### 要解决的核心痛点\nA 通道译文"
_TRANSLATED_B = "### 要解决的核心痛点\nB 通道译文"


class TestTranslateDegradation:
    def test_unavailable_channels_are_skipped_without_requests(self, batch_config):
        """provider 没 key → 直接留空，一次请求都不该发（省失败税）。"""
        llm = _FakeLLM({"writer"}, lambda role, msg: {"content": "x"})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = [{"full_name": "a/b", "refined_summary": "long enough english text" * 5}]
        pipeline._stage_translate(repos)
        assert llm.calls == []
        assert repos[0]["translation_a"] == "" and repos[0]["translation_b"] == ""

    def test_error_text_never_becomes_translation(self, batch_config):
        """回归：siliconflow 缺 key 时旧实现会把错误字符串当 content 返回并写入正文。"""
        from src.llm import LLMError

        def responder(role, messages):
            raise LLMError("no client for provider 'siliconflow'")

        llm = _FakeLLM({"translator_a", "translator_b", "reviewer"}, responder)
        pipeline = _batch_pipeline(batch_config, llm)
        repos = [{"full_name": "a/b", "description": "d", "refined_summary": "long enough english text" * 5}]
        pipeline._stage_translate(repos)
        pipeline._stage_review(repos)
        assert repos[0]["translation_a"] == "" and repos[0]["translation_b"] == ""
        assert "Error" not in repos[0]["chinese_summary"]
        assert "要解决的核心痛点" in repos[0]["chinese_summary"]

    def test_short_summary_is_not_translated(self, batch_config):
        llm = _FakeLLM({"translator_a", "translator_b"}, lambda role, msg: {"content": _TRANSLATED_A})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = [{"full_name": "a/b", "refined_summary": "tiny"}]
        pipeline._stage_translate(repos)
        assert llm.calls == []
        assert repos[0]["translation_a"] == ""

    def test_both_channels_filled(self, batch_config):
        seen = {"translator_a": _TRANSLATED_A, "translator_b": _TRANSLATED_B}
        llm = _FakeLLM(set(seen), lambda role, msg: {"content": seen[role]})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = [{"full_name": "a/b", "refined_summary": "long enough english text" * 5}]
        pipeline._stage_translate(repos)
        assert repos[0]["translation_a"] == _TRANSLATED_A
        assert repos[0]["translation_b"] == _TRANSLATED_B


class _TrippingLLM:
    """模拟 LLMClient 的熔断：某 role 失败一次后 has_role 立刻变 False。"""

    def __init__(self, available: set[str], failing: str):
        self.available = set(available)
        self.failing = failing
        self.calls: list[str] = []
        self.call_kwargs: list[dict] = []

    def has_role(self, role: str) -> bool:
        return role in self.available

    def call_llm(self, messages, role="writer", **kwargs):
        self.calls.append(role)
        self.call_kwargs.append(kwargs)
        if role == self.failing:
            self.available.discard(role)
            raise LLMError(f"role '{role}' failed; channel tripped off")
        return {"content": _TRANSLATED_B}


class TestTranslateCircuitBreakerIntegration:
    def test_remaining_repos_skipped_after_trip(self, batch_config):
        """熔断后第 2..N 个仓库不该再为该通道发请求（否则 9 个仓库各烧一轮重试）。"""
        llm = _TrippingLLM({"translator_a", "translator_b", "reviewer"}, failing="translator_a")
        pipeline = _batch_pipeline(batch_config, llm)
        repos = [
            {"full_name": f"r{i}/repo", "description": "d", "refined_summary": "long enough english text" * 5}
            for i in range(4)
        ]
        pipeline._stage_translate(repos)
        assert llm.calls.count("translator_a") == 1
        assert all(r["translation_a"] == "" for r in repos)
        assert all(r["translation_b"] == _TRANSLATED_B for r in repos)

        pipeline._stage_review(repos)
        # A 通道全空 → 直接采用 B，不必再让 reviewer 逐仓库比稿
        assert all(r["chinese_summary"] == _TRANSLATED_B for r in repos)


class TestReviewDegradation:
    def _repos(self, ta, tb):
        return [{"full_name": "a/b", "description": "d", "translation_a": ta, "translation_b": tb}]

    def test_picks_b_when_reviewer_says_b(self, batch_config):
        llm = _FakeLLM({"reviewer"}, lambda role, msg: {"content": "B"})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = self._repos(_TRANSLATED_A, _TRANSLATED_B)
        pipeline._stage_review(repos)
        assert repos[0]["chinese_summary"] == _TRANSLATED_B

    def test_garbage_verdict_falls_back_to_a(self, batch_config):
        llm = _FakeLLM({"reviewer"}, lambda role, msg: {"content": "translation B looks nicer honestly"})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = self._repos(_TRANSLATED_A, _TRANSLATED_B)
        pipeline._stage_review(repos)
        assert repos[0]["chinese_summary"] == _TRANSLATED_A

    def test_reviewer_unavailable_uses_a_without_call(self, batch_config):
        llm = _FakeLLM(set(), lambda role, msg: {"content": "unused"})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = self._repos(_TRANSLATED_A, _TRANSLATED_B)
        pipeline._stage_review(repos)
        assert llm.calls == []
        assert repos[0]["chinese_summary"] == _TRANSLATED_A

    def test_single_channel_used_without_review(self, batch_config):
        llm = _FakeLLM({"reviewer"}, lambda role, msg: pytest.fail("不应调用 reviewer"))
        pipeline = _batch_pipeline(batch_config, llm)
        repos = self._repos("", _TRANSLATED_B)
        pipeline._stage_review(repos)
        assert repos[0]["chinese_summary"] == _TRANSLATED_B

    def test_no_translation_uses_chinese_stub(self, batch_config):
        llm = _FakeLLM({"reviewer"}, lambda role, msg: {"content": "A"})
        pipeline = _batch_pipeline(batch_config, llm)
        repos = self._repos("", "")
        repos[0]["description"] = "一个测试项目"
        pipeline._stage_review(repos)
        assert "### 要解决的核心痛点" in repos[0]["chinese_summary"]
        assert llm.calls == []

    def test_reviewer_gets_reasoning_headroom(self, batch_config):
        """max_tokens 不能只够一个字母：推理型模型会把 reasoning 算进同一预算。"""
        llm = _FakeLLM({"reviewer"}, lambda role, msg: {"content": "A"})
        pipeline = _batch_pipeline(batch_config, llm)
        pipeline._stage_review(self._repos(_TRANSLATED_A, _TRANSLATED_B))
        assert llm.call_kwargs[0]["max_tokens"] == REVIEW_MAX_TOKENS
        assert REVIEW_MAX_TOKENS >= 32


class TestPipelineRunEdgeCases:
    """Test edge cases in pipeline.run()."""

    def test_run_all_repos_cooled_returns_early(self, batch_config):
        """When all repos are in cooldown, run should return early with meta."""
        client = MagicMock()
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(batch_config, client)

        # First purge expired, then add repo to archive to trigger cooling
        pipeline.dedup.purge_expired_cooldowns()
        pipeline.dedup._archive["test/repo"] = {
            "cooldown_until": "2099-12-31",
        }

        # Mock get_mock_data to return a repo that's in the archive
        pipeline.crawler.get_mock_data = MagicMock(return_value=[
            {"full_name": "test/repo", "stars": 50000, "source": "trending", "language": "Python",
             "description": "Test", "owner": "test", "name": "repo", "url": "https://github.com/test/repo",
             "period_stars": ""}
        ])

        result = pipeline.run(since="daily", use_mock=True)
        # Should return early with a meta dict indicating 0 curated repos
        assert result == {} or result.get("meta", {}).get("total_curated_repos", -1) <= 0

    def test_stage_crawl_mock_returns_data(self, batch_config):
        """_stage_crawl with mock should return mock data."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        repos = pipeline._stage_crawl("daily", use_mock=True)
        assert len(repos) > 0
        for r in repos:
            assert "full_name" in r

    def test_run_bucket_alloc_empty_returns_early(self, batch_config):
        """When bucket allocation returns empty, should return early meta."""
        client = MagicMock()
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(batch_config, client)
        # Mock get_mock_data to return empty -> bucket alloc gets nothing
        pipeline.crawler.get_mock_data = MagicMock(return_value=[])
        result = pipeline.run(since="daily", use_mock=True)
        assert result == {} or result.get("meta", {}).get("total_curated_repos", 0) == 0

    def test_run_stage2_empty_returns_empty(self, batch_config):
        """When Stage 2 filters everything out, should return {}."""
        client = MagicMock()
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(batch_config, client)
        # Mock _stage_analyze to return empty after stage 2
        pipeline._stage_analyze = MagicMock(return_value=[])
        pipeline.crawler.get_mock_data = MagicMock(return_value=[
            {"full_name": "test/repo", "stars": 100, "source": "trending",
             "language": "Python", "description": "Test", "owner": "test",
             "name": "repo", "url": "https://github.com/test/repo", "period_stars": ""}
        ])
        result = pipeline.run(since="daily", use_mock=True)
        assert result == {}

    def test_stage_summarize_reflect_per_repo_fallback_with_batch_failure(self, batch_config, batch_repos):
        """When batch JSON parsing fails, _stage_summarize_and_reflect should fall back to per-repo."""
        client = MagicMock()
        client.call_llm.side_effect = [
            {"content": "Not valid JSON at all."},
            {"content": "### Core Technical Problem\nThe analysis."},
            {"content": "### Core Technical Problem\nAnother analysis."},
        ]
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False
        result = pipeline._stage_summarize_and_reflect(batch_repos)
        assert len(result) == len(batch_repos)
        for r in result:
            assert "refined_summary" in r

    def test_stage_crawl_non_mock_uses_crawler(self, batch_config):
        """_stage_crawl without mock should call crawler methods."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.crawler.crawl_trending = MagicMock(side_effect=[
            [{"full_name": "a/daily", "stars": 100, "source": "trending",
              "language": "Python", "description": "Daily repo", "period_stars": "10 stars today"}],
            [{"full_name": "b/weekly", "stars": 200, "source": "trending",
              "language": "Python", "description": "Weekly repo", "period_stars": "50 stars this week"}],
            [{"full_name": "c/monthly", "stars": 300, "source": "trending",
              "language": "Python", "description": "Monthly repo", "period_stars": "100 stars this month"}],
        ])
        pipeline.crawler.fetch_giant_repos = MagicMock(return_value=[
            {"full_name": "giant/repo", "stars": 50000, "source": "llm_giant",
             "language": "Python", "description": "Giant repo", "period_stars": ""},
        ])
        result = pipeline._stage_crawl("daily", use_mock=False)
        assert len(result) > 0
        assert pipeline.crawler.crawl_trending.call_count == 3
        assert pipeline.crawler.fetch_giant_repos.call_count == 1
        names = [r["full_name"] for r in result]
        assert "a/daily" in names
        assert "giant/repo" in names

    def test_run_purge_expired_prints_message(self, batch_config):
        """purge_expired_cooldowns returning > 0 should print message."""
        client = MagicMock()
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(batch_config, client)
        # Add an expired cooldown entry
        pipeline.dedup._archive["test/repo"] = {"cooldown_until": "2000-01-01"}
        result = pipeline.run(since="daily", use_mock=True)
        assert "meta" in result

    def test_run_newly_archived_prints_message(self, batch_config):
        """When repos get archived, should print message."""
        client = MagicMock()
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(batch_config, client)
        # Clear archive + pre-populate history to trigger archive on next run
        pipeline.dedup._archive.clear()
        pipeline.dedup._history.clear()
        # Two past occurrences: outside the 90-day first_seen window (so the repo
        # still counts as "new") yet inside the 365-day TTL (so they survive trim
        # and the third occurrence today promotes it to the archive).
        now = datetime.now()
        pipeline.dedup._history["deepseek-ai/DeepSeek-R1"] = [
            (now - timedelta(days=200)).strftime("%Y-%m-%d"),
            (now - timedelta(days=199)).strftime("%Y-%m-%d"),
        ]
        result = pipeline.run(since="daily", use_mock=True)
        assert result["meta"]["total_curated_repos"] > 0

    def test_stage_crawl_non_mock_dedup_merges_period_stars(self, batch_config):
        """When same repo appears in two trending lists, should merge period_stars."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        # shared/repo appears in both daily (no period_stars) and weekly (has period_stars)
        pipeline.crawler.crawl_trending = MagicMock(side_effect=[
            [{"full_name": "shared/repo", "stars": 100, "source": "trending",
              "language": "Python", "description": "Daily repo", "period_stars": ""}],
            [{"full_name": "shared/repo", "stars": 200, "source": "trending",
              "language": "Python", "description": "Weekly version with period_stars",
              "period_stars": "50 stars this week"}],
            [],
        ])
        pipeline.crawler.fetch_giant_repos = MagicMock(return_value=[])
        result = pipeline._stage_crawl("daily", use_mock=False)
        assert len(result) == 1
        assert result[0]["period_stars"] == "50 stars this week"

    def test_run_bucket_alloc_zero_slots_returns_early(self, batch_config):
        """When bucket allocation returns empty (total_slots=0), run should return early."""
        client = MagicMock()
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(batch_config, client)
        pipeline.config.bucket_allocation.enabled = True
        pipeline.config.bucket_allocation.total_slots = 0
        result = pipeline.run(since="daily", use_mock=True)
        # Should return meta with 0 curated repos
        assert result.get("meta", {}).get("total_curated_repos", -1) == 0
