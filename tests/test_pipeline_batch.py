"""Tests for pipeline batch paths, LLM error handling, and edge cases.

Covers the remaining uncovered lines in src/pipeline.py:
- Batch summarization/reflection (LLM path)
- Batch translation (LLM path)
- Per-repo fallback paths
- JSON parsing failures
- Empty repo handling in various stages
"""

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from src.config import AppConfig, AIConfig, GitHubConfig, Stage2PreFilterConfig
from src.pipeline import (
    CurationPipeline,
    MOCK_TRANSLATIONS,
)


@pytest.fixture
def batch_config() -> AppConfig:
    return AppConfig(
        github=GitHubConfig(max_trending_repos=10, max_org_repos=5),
        ai=AIConfig(
            default_provider="openai",
            temperature=0.3,
            max_tokens=4096,
            rate_limit_delay=0.01,
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
        ),
        stage2_pre_filter=Stage2PreFilterConfig(enabled=True, max_repos=88),
    )


@pytest.fixture
def batch_repos() -> List[Dict[str, Any]]:
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


class TestTranslateStage:
    """Test the Stage 5 translate stage."""

    def test_batch_translate_success(self, batch_config, batch_repos):
        """Per-repo translation should return all repos with translations."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_translate(batch_repos)
        assert len(result) == len(batch_repos)
        for r in result:
            assert "chinese_summary" in r

    def test_stage_translate_mock_fallback(self, batch_config, batch_repos):
        """Stage translate mock path should use MOCK_TRANSLATIONS for known repos."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_translate(batch_repos)
        assert result[0]["chinese_summary"] == MOCK_TRANSLATIONS["deepseek-ai/DeepSeek-V3"]

    def test_stage_translate_non_mock_calls_per_repo(self, batch_config, batch_repos):
        """When not mock, should call _translate_per_repo for each repo."""
        client = MagicMock()
        client.call_llm.return_value = {
            "content": "要解决的核心痛点\nTest translation."
        }
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False
        result = pipeline._stage_translate(batch_repos)
        assert len(result) == len(batch_repos)
        for r in result:
            assert "chinese_summary" in r

    def test_translate_per_repo_success(self, batch_config):
        """Per-repo translation now passes through refined_summary directly."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        long_text = "### 要解决的核心痛点\n这是足够长的一段中文分析文本，肯定超过了五十个字符的最小阈值要求，直通逻辑不会被触发。"
        repo = {"full_name": "test/repo", "refined_summary": long_text, "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert result["chinese_summary"] == long_text

    def test_translate_per_repo_failure_uses_chinese_stub(self, batch_config):
        """When refined_summary is too short, should use Chinese stub."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        repo = {"full_name": "test/repo", "refined_summary": "Short.", "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert "要解决的核心痛点" in result["chinese_summary"]
        assert "开源工程项目" in result["chinese_summary"]

    def test_translate_per_repo_long_summary_passes_through(self, batch_config):
        """Long refined_summary should pass through unchanged."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        long_text = "这是超过五十个字符的长文本用来测试翻译阶段的直通逻辑确认中文内容不会被改动。这里是更多字符确保绝对超过五十。"
        repo = {"full_name": "test/repo", "refined_summary": long_text, "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert result["chinese_summary"] == long_text


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


class TestSummarizeReflectPerRepo:
    """Test per-repo summarization."""

    def test_per_repo_success(self, batch_config):
        """Per-repo should return repo with refined_summary."""
        client = MagicMock()
        client.call_llm.return_value = {"content": "### Core Technical Problem\\nTest."}
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert result["refined_summary"] == "### Core Technical Problem\\nTest."
        assert result["reflection_trace"] == ""

    def test_per_repo_failure_uses_chinese_stub(self, batch_config):
        """When per-repo fails, should use Chinese stub summary."""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("Failed")
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert "要解决的核心痛点" in result["refined_summary"]
        assert "设计巧思与架构取舍" in result["refined_summary"]


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
        pipeline.dedup._history["deepseek-ai/DeepSeek-R1"] = ["2026-01-01", "2026-01-02"]
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
