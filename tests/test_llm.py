"""Tests for src/llm.py.

Covers:
- LLMClient initialization with/without API key
- call_llm with hub and fallback paths
- Stats tracking (get_stats, reset_stats)
- Rate limiting / retry behavior (via mock)
- Edge cases: missing API key, hub import failure
"""

from typing import Any, Dict, Optional
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from src.config import AppConfig, AIConfig

from unittest.mock import MagicMock, patch

from src.llm import LLMClient


@pytest.fixture
def llm_config() -> AppConfig:
    return AppConfig(
        ai=AIConfig(
            default_provider="openai",
            model_v3="gpt-4o-mini",
            model_r1="o1-mini",
            temperature=0.3,
            max_tokens=4096,
            rate_limit_delay=0.01,
            api_key="sk-test-key",
            base_url="https://api.openai.com/v1",
        )
    )


@pytest.fixture
def no_key_config() -> AppConfig:
    """Config with no API key set."""
    return AppConfig(
        ai=AIConfig(api_key=None)
    )


class TestLLMClientInit:
    """Test initialization behavior."""

    def test_init_with_api_key(self, llm_config):
        """With API key, hub should be attempted, and fallback should exist."""
        client = LLMClient(llm_config)
        assert client.provider == "openai"
        assert client.model_v3 == "gpt-4o-mini"
        assert client.model_r1 == "o1-mini"
        # Hub may or may not be importable; fallback should be set since key exists
        if client._hub is None:
            assert client._fallback is not None

    def test_init_without_api_key_warns(self, no_key_config):
        """Without API key, both hub and fallback should be None."""
        client = LLMClient(no_key_config)
        assert client._hub is None
        assert client._fallback is None

    def test_init_stats_start_at_zero(self, llm_config):
        client = LLMClient(llm_config)
        stats = client.get_stats()
        assert stats["call_count"] == 0
        assert stats["failed_attempt_count"] == 0
        assert stats["total_prompt_tokens"] == 0
        assert stats["total_completion_tokens"] == 0
        assert stats["total_tokens"] == 0

    def test_client_property_truthy_when_hub(self, llm_config):
        client = LLMClient(llm_config)
        # The client property should be truthy if hub or fallback exists
        if client._hub is not None:
            assert client.client is not None

    def test_client_property_none_without_key(self, no_key_config):
        client = LLMClient(no_key_config)
        assert client.client is None


class TestCallLLMNoKey:
    """Test behavior when no API key is configured."""

    def test_call_llm_no_key_returns_error_dict(self, no_key_config):
        """Without API key, call_llm should return an error dict, not crash."""
        client = LLMClient(no_key_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}])
        assert isinstance(result, dict)
        assert "Error:" in result["content"]
        assert result["reasoning"] is None


@patch("openai.OpenAI")
class TestCallLLMFallback:
    """Test the fallback OpenAI SDK path (when hub is not available)."""

    def test_successful_call(self, mock_openai_cls, llm_config):
        """A successful call returns content and updates stats."""
        # Mock the OpenAI client
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai

        # Mock the chat completion response
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello, world!"
        mock_choice.message.reasoning_content = None
        mock_choice.message.model_extra = None

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 20

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_openai.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}])

        assert result["content"] == "Hello, world!"
        assert result["model"] == llm_config.ai.model_v3

        stats = client.get_stats()
        assert stats["call_count"] == 1
        assert stats["total_prompt_tokens"] == 50
        assert stats["total_completion_tokens"] == 20
        assert stats["total_tokens"] == 70

    def test_use_reasoning_model(self, mock_openai_cls, llm_config):
        """When use_reasoning=True, the r1 model should be used."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai

        mock_choice = MagicMock()
        mock_choice.message.content = "Thinking..."
        mock_choice.message.reasoning_content = None
        mock_choice.message.model_extra = None

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 20

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_openai.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Reason"}], use_reasoning=True)

        # The model should be the R1 model
        call_kwargs = mock_openai.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == llm_config.ai.model_r1

    def test_reasoning_content_extracted(self, mock_openai_cls, llm_config):
        """reasoning_content from response should be extracted."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai

        mock_choice = MagicMock()
        mock_choice.message.content = "Final answer"
        mock_choice.message.reasoning_content = "Step 1: ..."
        mock_choice.message.model_extra = None

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 20

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_openai.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Think"}], use_reasoning=True)
        assert result["reasoning"] == "Step 1: ..."

    def test_model_extra_reasoning_extracted(self, mock_openai_cls, llm_config):
        """When reasoning_content is in model_extra, it should be extracted."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai

        mock_choice = MagicMock()
        mock_choice.message.content = "Final answer"
        # Remove reasoning_content so hasattr falls through to model_extra
        del mock_choice.message.reasoning_content
        # Use configure_mock to set model_extra properly
        mock_choice.message.configure_mock(model_extra={"reasoning_content": "Extra thinking..."})

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 50
        mock_usage.completion_tokens = 20

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_openai.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Think"}], use_reasoning=True)
        assert result["reasoning"] == "Extra thinking..."


@patch("openai.OpenAI")
class TestCallLLMFallbackErrors:
    """Test error handling in the fallback path."""

    def test_retry_on_rate_limit(self, mock_openai_cls, llm_config):
        """Rate limit errors should trigger retries."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai

        # First two calls fail with rate limit, third succeeds
        mock_openai.chat.completions.create.side_effect = [
            Exception("429 Too Many Requests"),
            Exception("429 rate limit exceeded"),
            MagicMock(
                choices=[MagicMock(
                    message=MagicMock(content="Success", reasoning_content=None, model_extra=None)
                )],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5),
            ),
        ]

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], retries=3, backoff_factor=1.0)

        assert result["content"] == "Success"
        assert mock_openai.chat.completions.create.call_count == 3

    def test_all_retries_fail_raises(self, mock_openai_cls, llm_config):
        """When all retries fail, should raise RuntimeError."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai
        mock_openai.chat.completions.create.side_effect = Exception("429 rate limit")

        client = LLMClient(llm_config)
        with pytest.raises(RuntimeError, match="maximum retries"):
            client.call_llm([{"role": "user", "content": "Hi"}], retries=2, backoff_factor=1.0)

    def test_non_rate_limit_errors_retry(self, mock_openai_cls, llm_config):
        """Non-rate-limit errors should also trigger retries with shorter delay."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai
        mock_openai.chat.completions.create.side_effect = [
            Exception("Internal server error"),
            MagicMock(
                choices=[MagicMock(
                    message=MagicMock(content="Success", reasoning_content=None, model_extra=None)
                )],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5),
            ),
        ]

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], retries=2, backoff_factor=1.0)
        assert result["content"] == "Success"

    def test_failed_attempt_tracking(self, mock_openai_cls, llm_config):
        """Failed attempts should be tracked in stats."""
        mock_openai = MagicMock()
        mock_openai_cls.return_value = mock_openai
        mock_openai.chat.completions.create.side_effect = [
            Exception("429 rate limit"),
            Exception("429 rate limit"),
            MagicMock(
                choices=[MagicMock(
                    message=MagicMock(content="OK", reasoning_content=None, model_extra=None)
                )],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5),
            ),
        ]

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], retries=3, backoff_factor=1.0)

        stats = client.get_stats()
        assert stats["failed_attempt_count"] == 2


class TestGetStatsAndReset:
    """Test stats tracking."""

    def test_get_stats_summary_field(self, llm_config):
        """get_stats should include a total_tokens field."""
        client = LLMClient(llm_config)
        # Manually set some stats
        client.call_count = 5
        client.failed_attempt_count = 2
        client.total_prompt_tokens = 1000
        client.total_completion_tokens = 500

        stats = client.get_stats()
        assert stats["call_count"] == 5
        assert stats["failed_attempt_count"] == 2
        assert stats["total_prompt_tokens"] == 1000
        assert stats["total_completion_tokens"] == 500
        assert stats["total_tokens"] == 1500

    def test_reset_stats_clears_counters(self, llm_config):
        """reset_stats should zero out all counters."""
        client = LLMClient(llm_config)
        client.call_count = 5
        client.failed_attempt_count = 2
        client.total_prompt_tokens = 1000
        client.total_completion_tokens = 500

        client.reset_stats()
        stats = client.get_stats()
        assert stats["call_count"] == 0
        assert stats["failed_attempt_count"] == 0
        assert stats["total_prompt_tokens"] == 0
        assert stats["total_completion_tokens"] == 0
