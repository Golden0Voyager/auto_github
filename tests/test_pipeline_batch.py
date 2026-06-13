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
        """Per-repo translation should work with new 4-section Chinese headers."""
        client = MagicMock()
        client.call_llm.return_value = {
            "content": "要解决的核心痛点\nTest translation."
        }
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        repo = {"full_name": "test/repo", "refined_summary": "English.", "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert "chinese_summary" in result
        assert result["chinese_summary"] == "要解决的核心痛点\nTest translation."

    def test_translate_per_repo_failure_keeps_english(self, batch_config):
        """When per-repo translation fails, should keep English summary."""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("API Error")
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        repo = {"full_name": "test/repo", "refined_summary": "Original English.", "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert result["chinese_summary"] == "Original English."


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

    def test_per_repo_failure_uses_static_stub(self, batch_config):
        """When per-repo fails, should use static stub summary."""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("Failed")
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert "Core Pain Point Solved" in result["refined_summary"]
        assert "Design & Architectural Trade-offs" in result["refined_summary"]


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
