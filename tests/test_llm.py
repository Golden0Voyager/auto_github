"""Tests for src/llm.py.

Covers:
- LLMClient initialization with/without API key
- call_llm with role-based model selection
- Stats tracking (get_stats, reset_stats)
- Rate limiting / retry behavior (via mock)
- Edge cases: missing API key, unknown role
"""

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.config import AppConfig, AIConfig, RoleConfig
from src.llm import LLMClient


@pytest.fixture
def llm_config(monkeypatch) -> AppConfig:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    return AppConfig(
        ai=AIConfig(
            default_provider="openai",
            roles={
                "classifier": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "writer": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "translator_a": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "translator_b": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "reviewer": RoleConfig(model="gpt-4o-mini", provider="openai"),
            },
            temperature=0.3,
            max_tokens=4096,
            rate_limit_delay=0.01,
            api_key="sk-test-key",
            base_url="https://api.openai.com/v1",
        )
    )


@pytest.fixture
def no_key_config(monkeypatch) -> AppConfig:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return AppConfig()


@pytest.fixture
def mock_openai():
    """Mock OpenAI client so _init_clients gets a mock."""
    with patch("openai.OpenAI") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_cls


class TestLLMClientInit:
    """Test initialization behavior."""

    def test_init_with_api_key(self, llm_config, mock_openai):
        """With API key, a client should be created."""
        client = LLMClient(llm_config)
        assert client.client is not None
        stats = client.get_stats()
        assert stats["call_count"] == 0

    def test_init_without_api_key_has_no_client(self, no_key_config):
        """Without API key, client should be None."""
        client = LLMClient(no_key_config)
        assert client.client is None

    def test_init_stats_start_at_zero(self, llm_config, mock_openai):
        client = LLMClient(llm_config)
        stats = client.get_stats()
        assert stats["call_count"] == 0
        assert stats["failed_attempt_count"] == 0
        assert stats["total_prompt_tokens"] == 0
        assert stats["total_completion_tokens"] == 0

    def test_client_property_none_without_key(self, no_key_config):
        client = LLMClient(no_key_config)
        assert client.client is None


class TestCallLLMNoKey:
    """Test behavior when no API key is configured."""

    def test_call_llm_no_key_returns_error_dict(self, no_key_config):
        """Without API key, call_llm should return an error dict."""
        client = LLMClient(no_key_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}])
        assert isinstance(result, dict)
        assert "Error:" in result.get("content", "")

    def test_call_llm_unknown_role_returns_error(self, llm_config):
        """An unknown role should return an error dict."""
        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], role="nonexistent")
        assert "Error:" in result.get("content", "")


class TestCallLLM:
    """Test the fallback OpenAI SDK path."""

    def test_successful_call(self, llm_config, mock_openai):
        """A successful call returns content and updates stats."""
        mock_client = mock_openai.return_value
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

        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], role="writer")

        assert result["content"] == "Hello, world!"
        assert result["model"] == "gpt-4o-mini"

        stats = client.get_stats()
        assert stats["call_count"] == 1
        assert stats["total_prompt_tokens"] == 50
        assert stats["total_completion_tokens"] == 20
        assert stats["total_tokens"] == 70

    def test_role_maps_to_correct_model(self, llm_config, mock_openai):
        """Each role should use its configured model."""
        mock_client = mock_openai.return_value
        mock_choice = MagicMock()
        mock_choice.message.content = "result"
        mock_choice.message.reasoning_content = None
        mock_choice.message.model_extra = None
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[mock_choice],
            usage=MagicMock(prompt_tokens=1, completion_tokens=1),
        )

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Classify"}], role="classifier")
        assert result["content"] == "result"

    def test_reasoning_content_extracted(self, llm_config, mock_openai):
        """reasoning_content from response should be extracted."""
        mock_client = mock_openai.return_value
        mock_choice = MagicMock()
        mock_choice.message.content = "Final answer"
        mock_choice.message.reasoning_content = "Step 1: ..."
        mock_choice.message.model_extra = None

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Think"}], role="writer")
        assert result["reasoning"] == "Step 1: ..."

    def test_model_extra_reasoning_extracted(self, llm_config, mock_openai):
        """When reasoning_content is in model_extra, it should be extracted."""
        mock_client = mock_openai.return_value
        mock_choice = MagicMock()
        mock_choice.message.content = "Final answer"
        mock_choice.message.reasoning_content = None
        mock_choice.message.model_extra = {"reasoning_content": "Extra thinking..."}

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)

        mock_client.chat.completions.create.return_value = mock_response

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Think"}], role="writer")
        assert result["reasoning"] == "Extra thinking..."


class TestCallLLMErrors:
    """Test error handling."""

    def test_retry_on_rate_limit(self, llm_config, mock_openai):
        """Rate limit errors should trigger retries."""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = [
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
        result = client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=3, backoff_factor=1.0)
        assert result["content"] == "Success"
        assert mock_client.chat.completions.create.call_count == 3

    def test_all_retries_fail_raises(self, llm_config, mock_openai):
        """When all retries fail, should raise RuntimeError."""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("429 rate limit")

        client = LLMClient(llm_config)
        with pytest.raises(RuntimeError, match="maximum retries"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=2, backoff_factor=1.0)

    def test_non_rate_limit_errors_retry(self, llm_config, mock_openai):
        """Non-rate-limit errors should also trigger retries."""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = [
            Exception("Internal server error"),
            MagicMock(
                choices=[MagicMock(
                    message=MagicMock(content="Success", reasoning_content=None, model_extra=None)
                )],
                usage=MagicMock(prompt_tokens=10, completion_tokens=5),
            ),
        ]

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=2, backoff_factor=1.0)
        assert result["content"] == "Success"

    def test_failed_attempt_tracking(self, llm_config, mock_openai):
        """Failed attempts should be tracked in stats."""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = [
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
        result = client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=3, backoff_factor=1.0)
        stats = client.get_stats()
        assert stats["failed_attempt_count"] == 2


class TestGetStatsAndReset:
    """Test stats tracking."""

    def test_get_stats_summary_field(self, llm_config):
        client = LLMClient(llm_config)
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
