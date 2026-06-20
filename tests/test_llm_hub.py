"""Tests for src/llm.py (multi-provider, stats sync, edge cases)."""

from unittest.mock import MagicMock, patch

import pytest

from src.config import AIConfig, AppConfig, RoleConfig
from src.llm import LLMClient


@pytest.fixture
def llm_config(monkeypatch) -> AppConfig:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    return AppConfig(
        ai=AIConfig(
            roles={
                "classifier": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "writer": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "translator_a": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "translator_b": RoleConfig(model="gpt-4o-mini", provider="openai"),
                "reviewer": RoleConfig(model="gpt-4o-mini", provider="openai"),
            },
            rate_limit_delay=0.01,
            api_key="sk-test-key",
        )
    )


@pytest.fixture
def mock_openai():
    with patch("openai.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        yield mock_cls


class TestInitEdgeCases:
    def test_init_no_env_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client = LLMClient(AppConfig())
        assert client.client is None

    def test_init_stats_zero(self, llm_config, mock_openai):
        s = LLMClient(llm_config).get_stats()
        assert s["call_count"] == 0


class TestCallLLMEdgeCases:
    def test_all_roles_work(self, llm_config, mock_openai):
        mc = mock_openai.return_value
        mc.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ok", reasoning_content=None, model_extra=None))],
            usage=MagicMock(prompt_tokens=1, completion_tokens=1),
        )
        c = LLMClient(llm_config)
        for role in ("classifier", "writer", "translator_a", "translator_b", "reviewer"):
            assert c.call_llm([{"role": "user", "content": "test"}], role=role)["content"] == "ok"

    def test_no_key_returns_error(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("SENSENOVA_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        r = LLMClient(AppConfig()).call_llm([{"role": "user", "content": "Hi"}])
        assert "Error:" in r.get("content", "")


class TestStats:
    def test_after_call(self, llm_config, mock_openai):
        mc = mock_openai.return_value
        mc.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="x", reasoning_content=None, model_extra=None))],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        )
        c = LLMClient(llm_config)
        c.call_llm([{"role": "user", "content": "Hi"}])
        assert c.get_stats()["call_count"] >= 1

    def test_reset(self, llm_config, mock_openai):
        c = LLMClient(llm_config)
        c.call_count = 5
        c.reset_stats()
        assert c.get_stats()["call_count"] == 0
