"""Tests for src/llm.py.

Covers:
- LLMClient initialization with/without API key
- call_llm with role-based model selection
- Stats tracking (get_stats, reset_stats)
- Rate limiting / retry behavior (via mock)
- Edge cases: missing API key, unknown role
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import _PROVIDER_ENV, AIConfig, AppConfig, ModelLeg, RoleConfig, load_config
from src.llm import (
    DEFAULT_MIN_INTERVAL,
    DEFAULT_TIMEOUT,
    MIN_RETRY_DELAY,
    PROVIDER_MIN_INTERVAL,
    PROVIDER_TIMEOUTS,
    LLMClient,
    LLMError,
    _error_status,
)


def _clear_all_provider_keys(monkeypatch) -> None:
    """清掉 _PROVIDER_ENV 里**所有** provider 的 key。

    不写死名单：每加一个 provider 就多一个能污染"无 key 应降级"类测试的环境变量，
    而本机 shell 里往往正好带着它们（写死名单时漏掉的那家会让这些用例假红）。
    """
    for env_key, _url_env, _default in _PROVIDER_ENV.values():
        monkeypatch.delenv(env_key, raising=False)


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


class TestProviderParameterTables:
    """per-provider 的超时与最小间隔都是实测数字（spec「per-provider 参数」表）。

    它们必须是可断言的数据，而不是散在 `_try_model` 里的字面量 —— 换 provider 前
    "先重算配额"这条 AGENTS.md 规则，前提是配额看得见。
    """

    def test_timeouts_match_the_measured_table(self):
        assert PROVIDER_TIMEOUTS["sensenova"] == 60      # glm-5.2 写作实测 66.0s
        assert PROVIDER_TIMEOUTS["openrouter"] == 60     # super-vl 翻译实测 39.2s，已逼近旧的 45s
        assert PROVIDER_TIMEOUTS["siliconflow"] == 90    # Hunyuan 长输出 >30s：模型慢，不是通道死
        assert PROVIDER_TIMEOUTS["agnes"] == 60          # 写作实测最大 40.6s
        assert PROVIDER_TIMEOUTS["zenmux"] == 60         # 写作实测 53.3s
        assert PROVIDER_TIMEOUTS["tokenrhythm"] == 90    # 预留（当前无 role 使用）
        assert PROVIDER_TIMEOUTS["intern"] == 120        # 预留

    def test_sensenova_min_interval_is_six_seconds(self):
        """3s 内发第二发实测必 429（RateLimitExceeded.EndpointTPMExceeded）。"""
        assert PROVIDER_MIN_INTERVAL["sensenova"] == 6.0

    def test_unknown_provider_falls_back_to_defaults(self):
        assert PROVIDER_TIMEOUTS.get("nope", DEFAULT_TIMEOUT) == 30
        assert PROVIDER_MIN_INTERVAL.get("nope", DEFAULT_MIN_INTERVAL) == 2.0

    def test_every_provider_a_leg_uses_has_both_parameters(self):
        """新 provider 忘了配参数会静默拿默认值 —— 那正是"以为设了超时、其实没设"的坑。"""
        cfg = AIConfig()
        for role_cfg in cfg.roles.values():
            for leg in role_cfg.chain:
                assert leg.provider in PROVIDER_TIMEOUTS, leg.provider
                assert leg.provider in PROVIDER_MIN_INTERVAL, leg.provider

    def test_try_model_actually_passes_the_provider_timeout_to_the_sdk(self, llm_config, mock_openai, monkeypatch):
        """表必须真的被用上：否则这些常量只是装饰。"""
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova-test")
        llm_config.ai.roles["writer"] = RoleConfig(
            chain=[ModelLeg(model="sensenova-6.8-flash-lite", provider="sensenova")],
        )
        ok = MagicMock(
            choices=[MagicMock(message=MagicMock(content="译文", reasoning_content=None, model_extra=None))],
            usage=MagicMock(prompt_tokens=5, completion_tokens=2),
        )
        mock_openai.return_value.chat.completions.create.return_value = ok

        LLMClient(llm_config).call_llm([{"role": "user", "content": "Hi"}], role="writer")

        kwargs = mock_openai.return_value.chat.completions.create.call_args.kwargs
        assert kwargs["timeout"] == PROVIDER_TIMEOUTS["sensenova"] == 60


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

    def test_call_llm_no_key_raises(self, no_key_config):
        """Without API key, call_llm must raise — never hand back an error string as content."""
        client = LLMClient(no_key_config)
        with pytest.raises(LLMError, match="no client for provider"):
            client.call_llm([{"role": "user", "content": "Hi"}])

    def test_call_llm_unknown_role_raises(self, llm_config):
        """An unknown role should raise instead of returning an error dict."""
        client = LLMClient(llm_config)
        with pytest.raises(LLMError, match="unknown role"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="nonexistent")

    def test_role_with_fallback_reports_missing_client(self, monkeypatch):
        """配了 fallback 的 role 在完全没 key 时要报"没有 client"，不能被误诊成重试耗尽。"""
        _clear_all_provider_keys(monkeypatch)
        client = LLMClient(load_config())  # writer 带降级链
        assert client.has_role("writer") is False
        with pytest.raises(LLMError, match="no client for provider"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=1)

    def test_has_role_reflects_provider_availability(self, llm_config, monkeypatch, mock_openai):
        """has_role 让上层在没有 key 时直接跳过通道，不浪费请求。"""
        assert LLMClient(llm_config).has_role("writer") is True
        _clear_all_provider_keys(monkeypatch)
        bare = LLMClient(llm_config)
        assert bare.has_role("writer") is False
        assert bare.has_role("nonexistent") is False


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
        """When all retries fail, should raise LLMError instead of returning error text."""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("429 rate limit")

        client = LLMClient(llm_config)
        with pytest.raises(LLMError, match="failed after"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=2, backoff_factor=1.0)

    def test_retry_backoff_has_floor_for_zero_delay_providers(self, llm_config, mock_openai):
        """sensenova/siliconflow 配的 delay 是 0，退避必须有下限，否则 429 时无脑重试。"""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("429 rate limit")

        client = LLMClient(llm_config)
        slept: list[float] = []
        with (
            patch("src.llm.time.sleep", side_effect=slept.append),
            pytest.raises(LLMError),
        ):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=3, backoff_factor=1.0)
        assert all(wait >= MIN_RETRY_DELAY for wait in slept), slept

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
        client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=3, backoff_factor=1.0)
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


class TestRoleCircuitBreaker:
    """一个 role 耗尽重试后本次运行熔断，剩余仓库不再付重试的失败税。

    真实事故：siliconflow 的 Hunyuan-MT-7B 每次 30s 超时，9 个仓库各烧 3 次
    = 约 14 分钟，整条 curate job 撞上 timeout 被取消，一份报告都没出。
    """

    def test_exhausted_role_trips_off(self, llm_config, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("Request timed out.")
        client = LLMClient(llm_config)

        with pytest.raises(LLMError, match="tripped off"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="translator_a", retries=2, backoff_factor=1.0)

        assert client.has_role("translator_a") is False

    def test_tripped_role_sends_no_more_requests(self, llm_config, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("Request timed out.")
        client = LLMClient(llm_config)

        with pytest.raises(LLMError):
            client.call_llm([{"role": "user", "content": "1"}], role="translator_a", retries=2, backoff_factor=1.0)
        calls_after_trip = mock_client.chat.completions.create.call_count

        with pytest.raises(LLMError, match="already tripped off"):
            client.call_llm([{"role": "user", "content": "2"}], role="translator_a", retries=2, backoff_factor=1.0)
        assert mock_client.chat.completions.create.call_count == calls_after_trip

    def test_trip_is_scoped_to_the_role(self, llm_config, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("Request timed out.")
        client = LLMClient(llm_config)
        with pytest.raises(LLMError):
            client.call_llm([{"role": "user", "content": "Hi"}], role="translator_a", retries=1, backoff_factor=1.0)
        assert client.has_role("translator_a") is False
        assert client.has_role("writer") is True


class TestNonRetryableErrors:
    """402/401/403 是账户级闸门，404 是单个模型下架：两者都不该付重试的失败税。

    真实事故：硅基流动欠费 402 后，两个 translator 各烧 3 次重试（响应只花 0.2s，
    根本没进推理），翻译层整条死掉时白跑 6 次请求。
    """

    def test_error_status_reads_attr_and_message_shape(self):
        err = Exception("billing gate")
        err.status_code = 402  # type: ignore[attr-defined]
        assert _error_status(err) == 402
        assert _error_status(Exception("Error code: 404 - {'message': 'no model'}")) == 404
        assert _error_status(Exception("Error code: 429 - Too Many Requests")) == 429
        assert _error_status(Exception("Request timed out.")) is None

    def test_402_stops_after_one_attempt_and_bans_provider(self, llm_config, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception(
            "Error code: 402 - {'code': 30001, 'message': 'Sorry, your account balance is insufficient'}"
        )
        client = LLMClient(llm_config)

        with pytest.raises(LLMError, match="failed after 1 attempt"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="translator_a", retries=3, backoff_factor=1.0)

        assert mock_client.chat.completions.create.call_count == 1
        # 同 provider 的其它通道也被账户闸门拦着，一次请求都不该再发
        assert client.has_provider("openai") is False
        assert client.has_role("translator_b") is False

        with pytest.raises(LLMError, match="already tripped off"):
            client.call_llm([{"role": "user", "content": "again"}], role="translator_a", retries=3)
        assert mock_client.chat.completions.create.call_count == 1

    def test_404_trips_role_but_keeps_provider(self, llm_config, mock_openai):
        """404 只说明这个模型没了：provider 还要留给别的 role，但不能重试同一个模型。"""
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = Exception("Error code: 404 - model removed")
        client = LLMClient(llm_config)

        with pytest.raises(LLMError, match="failed after 1 attempt"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=3, backoff_factor=1.0)

        assert mock_client.chat.completions.create.call_count == 1
        assert client.has_provider("openai") is True
        assert client.has_role("writer") is False
        assert client.has_role("reviewer") is True

    def test_account_ban_still_allows_other_provider_fallback(self, llm_config, mock_openai, monkeypatch):
        """openai 欠费不该顺手打死 openrouter 的降级腿。"""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        llm_config.ai.roles["writer"] = RoleConfig(
            model="primary-dead", provider="openai",
            fallback_model="fallback-alive", fallback_provider="openrouter",
        )
        ok = MagicMock(
            choices=[MagicMock(message=MagicMock(content="翻译结果", reasoning_content=None, model_extra=None))],
            usage=MagicMock(prompt_tokens=5, completion_tokens=2),
        )
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.side_effect = [Exception("Error code: 402 - insufficient balance"), ok]

        client = LLMClient(llm_config)
        result = client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=2, backoff_factor=1.0)
        assert result["content"] == "翻译结果"
        assert result["model"] == "fallback-alive"


class TestEmptyChoicesResponse:
    """provider 返回没有 choices 的 200 响应时不能 'NoneType' object is not subscriptable。

    真实 CI 跑里 openrouter 出现过一次,靠重试才蒙混过去。
    """

    def test_missing_choices_counts_as_failed_attempt(self, llm_config, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = MagicMock(choices=None)
        client = LLMClient(llm_config)

        # 空 choices 被当成一次失败重试，耗尽后由 call_llm 统一抛"失败后熔断"
        with pytest.raises(LLMError, match="failed after"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=1, backoff_factor=1.0)
        assert client.get_stats()["failed_attempt_count"] == 1

    def test_empty_choices_list_is_retried_then_raises(self, llm_config, mock_openai):
        mock_client = mock_openai.return_value
        mock_client.chat.completions.create.return_value = MagicMock(choices=[])
        client = LLMClient(llm_config)

        with pytest.raises(LLMError, match="failed after"):
            client.call_llm([{"role": "user", "content": "Hi"}], role="writer", retries=2, backoff_factor=1.0)
        assert mock_client.chat.completions.create.call_count == 2


class _FakeClients:
    """按 provider 造假的 OpenAI client，并记录调用次数与最后一次的 kwargs。"""

    def __init__(self) -> None:
        self.replies: dict[str, Any] = {}
        self.calls: dict[str, int] = {}
        self.last_kwargs: dict[str, Any] = {}

    def client_for(self, provider: str) -> MagicMock:
        def create(**kwargs: Any) -> MagicMock:
            self.calls[provider] = self.calls.get(provider, 0) + 1
            self.last_kwargs = kwargs
            reply = self.replies.get(provider, "ok")
            if isinstance(reply, Exception):
                raise reply
            return MagicMock(
                choices=[MagicMock(message=MagicMock(
                    content=reply, reasoning_content=None, model_extra=None))],
                usage=MagicMock(prompt_tokens=1, completion_tokens=1),
            )

        client = MagicMock()
        client.chat.completions.create.side_effect = create
        return client


@pytest.fixture
def fake_clients():
    """这些用例既不联网也不真的睡：sleep 换成 no-op，好让重试路径能快速跑完。"""
    fake = _FakeClients()
    with patch("src.llm.time.sleep"):
        yield fake


def _client_with_chain(fake: _FakeClients, legs: list[tuple], replies: dict[str, Any],
                       keys: set[str] | None = None) -> LLMClient:
    """造一个不联网的 LLMClient。

    `legs` 形如 `[(provider, model)]` 或 `[(provider, model, max_tokens)]`（按链序）。
    链装在全部五个 role 上，测试想用哪个 role 都行；`keys` 限定哪些 provider 有 client
    （不给就是全都有）。client 造好后直接替换 `_clients`，绕开环境变量与网络。
    """
    fake.replies = dict(replies)
    available = keys if keys is not None else {leg[0] for leg in legs}
    clients = {provider: fake.client_for(provider) for provider in available}
    config = AppConfig(ai=AIConfig(
        roles={
            role: RoleConfig(chain=[
                ModelLeg(model=leg[1], provider=leg[0], max_tokens=(leg[2] if len(leg) > 2 else None))
                for leg in legs
            ])
            for role in ("classifier", "writer", "translator_a", "translator_b", "reviewer")
        },
        rate_limit_delay=0.01,
    ))
    client = LLMClient(config)
    client._clients = clients
    return client


class TestFallbackChainRouting:
    """有序链：逐腿降级、per-(role, leg) 熔断、空正文不算答案。"""

    def test_walks_the_chain_in_order(self, fake_clients):
        """leg1 返回空正文，必须换到 leg2，而不是把空串当成功。"""
        llm = _client_with_chain(fake_clients, [("p1", "m1"), ("p2", "m2")],
                                 replies={"p1": "", "p2": "ok"})
        res = llm.call_llm([{"role": "user", "content": "x"}], role="writer")
        assert res["content"] == "ok"
        assert res["model"] == "m2"
        assert res["leg"] == "2/2"

    def test_exhausted_leg_is_skipped_on_the_next_call(self, fake_clients):
        """per-(role, leg) 熔断：第二个仓库不再为已死的 leg1 付失败税。"""
        llm = _client_with_chain(fake_clients, [("p1", "m1"), ("p2", "m2")],
                                 replies={"p1": RuntimeError("boom"), "p2": "ok"})
        llm.call_llm([{"role": "user", "content": "x"}], role="writer", retries=1)
        hit_before = fake_clients.calls["p1"]
        llm.call_llm([{"role": "user", "content": "y"}], role="writer", retries=1)
        assert fake_clients.calls["p1"] == hit_before

    def test_leg_max_tokens_overrides_the_call_site_value(self, fake_clients):
        """leg 上的 max_tokens 优先于调用点给的 8192（比稿只要一个字母）。"""
        llm = _client_with_chain(fake_clients, [("p1", "m1", 256)], replies={"p1": "A"})
        llm.call_llm([{"role": "user", "content": "x"}], role="reviewer", max_tokens=8192)
        assert fake_clients.last_kwargs["max_tokens"] == 256

    def test_adjacent_legs_on_one_provider_neither_loop_nor_crash(self, fake_clients):
        """classifier 的 L3/L4 都是 sensenova：两条腿各打一次就收手，不互相打转。"""
        llm = _client_with_chain(fake_clients, [("p1", "m1"), ("p1", "m2")], replies={"p1": ""})
        with pytest.raises(LLMError):
            llm.call_llm([{"role": "user", "content": "x"}], role="classifier", retries=1)
        assert fake_clients.calls["p1"] == 2

    def test_missing_provider_key_is_unconfigured_not_exhausted(self, fake_clients):
        """没配 key 不是失败，也不能拉黑 provider（否则同 provider 的别的 role 一起陪葬）。"""
        llm = _client_with_chain(fake_clients, [("p_missing", "m1"), ("p2", "m2")],
                                 replies={"p2": "ok"}, keys={"p2"})
        res = llm.call_llm([{"role": "user", "content": "x"}], role="writer")
        assert res["content"] == "ok"
        assert res["leg"] == "1/1"
        assert "p_missing" not in llm._banned_providers


def _jev_response(choice="B", confidence=0.82, noul=0.05):
    """按 SDK 的真实字段形状造假响应：answers 按问题 ID 索引，type 区分 choice/noul。"""
    return MagicMock(
        answers={
            "better_draft": MagicMock(
                type="choice", choice=choice, confidence=confidence,
                probabilities={"A": 1 - confidence, "B": confidence},
            ),
            "draft_incomplete": MagicMock(type="noul", noul=noul),
        },
        usage=MagicMock(input_tokens=1200, output_tokens=7),
    )


class TestJevJudgmentChannel:
    """比稿判断通道：付费、独立 client，失败必须能整条运行停用并退回字母裁判。"""

    def test_has_judgment_requires_key(self, llm_config, mock_openai, monkeypatch):
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        client = LLMClient(llm_config)
        assert client.has_judgment() is False
        with pytest.raises(LLMError, match="unavailable"):
            client.call_judgment(state={"a": "x"}, questions={})

    def test_call_judgment_normalizes_answers_and_counts_stats(
        self, llm_config, mock_openai, monkeypatch
    ):
        monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
        fake = MagicMock()
        fake.system_one.return_value = _jev_response()
        client = LLMClient(llm_config)
        with patch.object(LLMClient, "_get_jev_client", return_value=fake):
            answers = client.call_judgment(state={"translation_a": "A"}, questions={"q": 1})

        assert answers["better_draft"]["choice"] == "B"
        assert answers["better_draft"]["confidence"] == 0.82
        assert answers["draft_incomplete"]["noul"] == 0.05
        stats = client.get_stats()
        assert stats["call_count"] == 1
        # Jev 的 usage 是 input/output 命名，必须并进同一套 token 口径
        assert stats["total_prompt_tokens"] == 1200
        assert stats["total_completion_tokens"] == 7

    def test_jev_failure_disables_channel_for_the_rest_of_the_run(
        self, llm_config, mock_openai, monkeypatch
    ):
        monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
        fake = MagicMock()
        fake.system_one.side_effect = Exception("402 payment required")
        client = LLMClient(llm_config)
        with patch.object(LLMClient, "_get_jev_client", return_value=fake):
            with pytest.raises(LLMError, match="jev judgment failed"):
                client.call_judgment(state={"a": "x"}, questions={"q": 1})
            assert client.has_judgment() is False
            with pytest.raises(LLMError, match="unavailable"):
                client.call_judgment(state={"a": "x"}, questions={"q": 1})
        assert fake.system_one.call_count == 1

    def test_openai_clients_disable_sdk_internal_retries(self, llm_config, mock_openai):
        """SDK 默认 max_retries=2 会把我们的 retries=3 乘成 9 次 HTTP。"""
        LLMClient(llm_config)
        assert mock_openai.call_args.kwargs["max_retries"] == 0

    def test_jev_client_disables_sdk_internal_retries(self, llm_config, mock_openai, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
        client = LLMClient(llm_config)
        with patch("typesafe_sdk.TypeSafeClient") as ts_client:
            client._get_jev_client()
        retry = ts_client.call_args.kwargs["retry"]
        assert retry.max_retries == 0
        assert ts_client.call_args.kwargs["model"] == "jev-latest"
