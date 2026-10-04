import time
from typing import Any

from src.config import _PROVIDER_ENV, AppConfig, resolve_api_key, resolve_base_url

# Retry waits are multiplicative on this floor. Free-tier providers answer 429
# within milliseconds, so a zero floor turns retries into a hammering storm.
MIN_RETRY_DELAY = 2.0


class LLMError(RuntimeError):
    """Raised when a role cannot produce usable content.

    Callers must degrade explicitly (stub / skip channel). Content is never an
    error message, so a broken channel cannot leak text into the report body.
    """


class LLMClient:
    """Multi-provider LLM client with role-based model selection.

    Each role (classifier, writer, translator_a, reviewer) maps to a
    model + provider pair defined in config.ai.roles. The client maintains
    one OpenAI client per provider, initialized from environment variables.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.call_count: int = 0
        self.failed_attempt_count: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self._clients: dict[str, Any] = {}
        self._unavailable_roles: set[str] = set()
        self._init_clients()

        if not self._clients:
            print("[LLM Warning] No API keys configured. All LLM roles will degrade.")

    def _init_clients(self) -> None:
        from openai import OpenAI
        # Providers come from config._PROVIDER_ENV so adding a provider is a
        # config-only change; the role table then decides which are actually used.
        for provider in _PROVIDER_ENV:
            key = resolve_api_key(provider)
            if not key:
                continue
            url = resolve_base_url(provider)
            try:
                self._clients[provider] = OpenAI(api_key=key, base_url=url)
            except Exception as e:
                print(f"[LLM Error] Failed to initialize provider '{provider}': {e}")
        ready = ", ".join(sorted(self._clients)) or "none"
        print(f"[LLM Init] Providers with keys: {ready}")

    def _get_client(self, provider: str):
        return self._clients.get(provider)

    @property
    def client(self) -> Any:
        return next(iter(self._clients.values()), None)

    def has_provider(self, provider: str) -> bool:
        return provider in self._clients

    def has_role(self, role: str) -> bool:
        """True when the role has at least one reachable provider.

        Lets pipeline stages skip a channel without burning a request.
        Once a role has exhausted its retries it is tripped off for the rest of
        the run — a dead model otherwise costs retries × every repository.
        """
        if role in self._unavailable_roles:
            return False
        role_cfg = self.config.ai.roles.get(role)
        if role_cfg is None:
            return False
        if self.has_provider(role_cfg.provider or self.config.ai.default_provider):
            return True
        if role_cfg.fallback_model:
            return self.has_provider(role_cfg.fallback_provider or role_cfg.provider)
        return False

    def _resolve_providers(self, role: str) -> tuple[str, str | None]:
        role_cfg = self.config.ai.roles.get(role)
        if role_cfg is None:
            raise LLMError(f"unknown role '{role}'")
        provider = role_cfg.provider or self.config.ai.default_provider
        fallback = role_cfg.fallback_provider or provider if role_cfg.fallback_model else None
        return provider, fallback

    def _try_model(
        self,
        messages: list[dict[str, str]],
        role: str,
        provider: str,
        model: str,
        temperature: float,
        max_tokens: int,
        retries: int,
        backoff_factor: float,
        tag: str = "primary",
    ) -> dict[str, Any] | None:
        client = self._get_client(provider)
        if client is None:
            return None

        delay = {"sensenova": 0.0, "openrouter": 1.0, "openai": 1.0, "siliconflow": 0.1}.get(provider, self.config.ai.rate_limit_delay)
        delay = max(delay, MIN_RETRY_DELAY)
        # Hunyuan-MT-7B 在硅基流动上响应 >30s(实测每次都在 30s 处 Request timed out),
        # 而同一 provider 的 Qwen2.5 只要 ~22s,所以是模型慢不是通道死 —— 给它更宽的额度。
        timeout = {"sensenova": 30, "openrouter": 45, "openai": 30, "siliconflow": 90}.get(provider, 30)
        for attempt in range(retries):
            try:
                if attempt > 0:
                    time.sleep(delay * (backoff_factor ** (attempt - 1)))
                print(f"[LLM] {provider}/{model} (role={role}, {tag}, attempt {attempt + 1}/{retries})")

                response = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                )
                choices = getattr(response, "choices", None)
                if not choices:
                    # 部分 provider 在限流/审核/空补全时返回没有 choices 的 200,
                    # 直接下标会炸 'NoneType' object is not subscriptable
                    raise LLMError(f"{provider}/{model} returned no choices")
                choice = choices[0]
                content = choice.message.content or ""

                reasoning: str | None = None
                rc = getattr(choice.message, "reasoning_content", None)
                if rc is not None:
                    reasoning = rc
                elif hasattr(choice.message, "model_extra") and choice.message.model_extra:
                    reasoning = choice.message.model_extra.get("reasoning_content")

                usage = getattr(response, "usage", None)
                if usage:
                    self.total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    self.total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                self.call_count += 1

                time.sleep(self.config.ai.rate_limit_delay / 2.0)
                return {"content": content, "reasoning": reasoning, "model": model}

            except Exception as e:
                self.failed_attempt_count += 1
                print(f"[LLM Warning] Attempt {attempt + 1} failed: {e}")
                if "429" in str(e).lower() or "rate limit" in str(e).lower():
                    continue
                if attempt == retries - 1:
                    return None

        return None

    def call_llm(
        self,
        messages: list[dict[str, str]],
        role: str = "writer",
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 3,
        backoff_factor: float = 2.0,
    ) -> dict[str, Any]:
        provider, fallback = self._resolve_providers(role)
        if role in self._unavailable_roles:
            raise LLMError(f"role '{role}' already tripped off this run")
        if not self.has_role(role):
            # 配了 fallback 的 role 也要报"没 client",否则会被误诊成"重试耗尽"
            wanted = [p for p in (provider, fallback) if p]
            raise LLMError(f"no client for provider(s) {wanted} (role={role})")

        temp = temperature if temperature is not None else self.config.ai.temperature
        max_t = max_tokens if max_tokens is not None else self.config.ai.max_tokens
        role_cfg = self.config.ai.roles[role]

        if self.has_provider(provider):
            result = self._try_model(
                messages=messages, role=role,
                provider=provider,
                model=role_cfg.model,
                temperature=temp, max_tokens=max_t,
                retries=retries, backoff_factor=backoff_factor,
                tag="primary",
            )
            if result is not None:
                return result

        if fallback is not None and self.has_provider(fallback):
            print(f"[LLM] Primary failed, switching to fallback model: {fallback}/{role_cfg.fallback_model}")
            result = self._try_model(
                messages=messages, role=role,
                provider=fallback,
                model=role_cfg.fallback_model or "",
                temperature=temp, max_tokens=max_t,
                retries=retries, backoff_factor=backoff_factor,
                tag="fallback",
            )
            if result is not None:
                return result

        # 熔断：本次运行剩余仓库不再为该 role 付重试的失败税
        self._unavailable_roles.add(role)
        raise LLMError(
            f"role '{role}' failed after {retries} attempts (primary + fallback); "
            f"channel tripped off for the rest of this run."
        )

    def get_stats(self) -> dict[str, int]:
        return {
            "call_count": self.call_count,
            "failed_attempt_count": self.failed_attempt_count,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
        }

    def reset_stats(self) -> None:
        self.call_count = 0
        self.failed_attempt_count = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
