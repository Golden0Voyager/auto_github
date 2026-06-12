"""Tests for src/llm.py hub path, stats sync, and edge cases.

This module tests the hub-based LLM client code paths that are
not covered by the basic fallback tests in test_llm.py.
"""

from typing import Any, Dict
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.config import AppConfig, AIConfig
from src.llm import LLMClient


@pytest.fixture
def llm_config() -> AppConfig:
    return AppConfig(
        ai=AIConfig(
            default_provider="sensenova",
            model_v3="sensenova-6.7-flash-lite",
            model_r1="sensenova-6.7-flash-lite",
            temperature=0.3,
            max_tokens=4096,
            rate_limit_delay=0.01,
            api_key="sk-test-key",
            base_url="https://token.sensenova.cn/v1",
        )
    )


class TestHubSyncStats:
    """Test the _sync_stats_from_hub method."""

    def test_sync_stats_updates_counters(self, llm_config):
        """_sync_stats_from_hub should mirror hub stats into local counters."""
        client = LLMClient(llm_config)

        # Simulate a hub with stats
        mock_hub = MagicMock()
        mock_hub.stats.snapshot.return_value = {
            "call_count": 5,
            "failed_attempt_count": 2,
            "total_prompt_tokens": 10000,
            "total_completion_tokens": 5000,
        }
        client._hub = mock_hub
        # Ensure fallback is None so hub path is used
        client._fallback = None

        client._sync_stats_from_hub()

        assert client.call_count == 5
        assert client.failed_attempt_count == 2
        assert client.total_prompt_tokens == 10000
        assert client.total_completion_tokens == 5000

    def test_sync_stats_no_hub_no_crash(self, llm_config):
        """When hub is None, _sync_stats_from_hub should not crash."""
        client = LLMClient(llm_config)
        client._hub = None
        # Should not raise
        client._sync_stats_from_hub()

    def test_sync_stats_hub_no_stats_attr(self, llm_config):
        """When hub lacks a stats attribute, should not crash."""
        client = LLMClient(llm_config)
        client._hub = MagicMock(spec=[])  # No stats attribute
        # Should not raise
        client._sync_stats_from_hub()


class TestGetStatsAfterHubActivity:
    """Test get_stats after simulated hub activity."""

    def test_get_stats_with_hub_activity(self, llm_config):
        """get_stats should return accumulated values from hub calls."""
        client = LLMClient(llm_config)

        # Manually set counters to simulate hub activity
        client.call_count = 10
        client.failed_attempt_count = 3
        client.total_prompt_tokens = 20000
        client.total_completion_tokens = 8000

        stats = client.get_stats()
        assert stats["call_count"] == 10
        assert stats["failed_attempt_count"] == 3
        assert stats["total_prompt_tokens"] == 20000
        assert stats["total_completion_tokens"] == 8000
        assert stats["total_tokens"] == 28000


class TestResetStatsWithHub:
    """Test reset_stats with hub attached."""

    def test_reset_stats_calls_hub_reset(self, llm_config):
        """reset_stats should also reset hub stats if available."""
        client = LLMClient(llm_config)

        mock_hub = MagicMock()
        mock_hub.stats = MagicMock()
        client._hub = mock_hub
        client.call_count = 5
        client.failed_attempt_count = 2

        client.reset_stats()

        assert client.call_count == 0
        assert client.failed_attempt_count == 0
        mock_hub.stats.reset.assert_called_once()

    def test_reset_stats_no_hub(self, llm_config):
        """reset_stats without hub should still clear counters."""
        client = LLMClient(llm_config)
        client._hub = None
        client.call_count = 5
        client.failed_attempt_count = 2

        client.reset_stats()

        assert client.call_count == 0
        assert client.failed_attempt_count == 0


class TestCallLLMHubFailureFallback:
    """Test that hub failure triggers fallback to direct OpenAI SDK."""

    def test_hub_failure_falls_back_to_direct(self, llm_config):
        """When hub fails, should call direct OpenAI SDK."""
        client = LLMClient(llm_config)

        # Mock hub to fail
        mock_hub = MagicMock()
        mock_hub.chat.side_effect = RuntimeError("Hub API error")
        client._hub = mock_hub

        # Mock fallback (OpenAI client)
        mock_fallback = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Fallback response"
        mock_choice.message.reasoning_content = None
        mock_fallback.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        )
        client._fallback = mock_fallback

        result = client.call_llm([{"role": "user", "content": "Hi"}], retries=1, backoff_factor=1.0)
        assert result["content"] == "Fallback response"
        mock_fallback.chat.completions.create.assert_called_once()

    def test_hub_and_fallback_both_none_returns_error(self, llm_config):
        """When both hub and fallback are None, should return error dict."""
        client = LLMClient(llm_config)
        client._hub = None
        client._fallback = None

        result = client.call_llm([{"role": "user", "content": "Hi"}])
        assert "Error:" in result["content"]
