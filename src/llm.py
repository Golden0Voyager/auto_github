import contextlib
import time
from typing import Any

from src.config import AppConfig, resolve_api_key, resolve_base_url


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
        self._init_clients()

        if not self._clients:
            print("[LLM Warning] No API keys configured. LLM requests will fail.")

    def _init_clients(self):
        from openai import OpenAI
        for provider in ("openrouter", "sensenova", "openai"):
            key = resolve_api_key(provider)
            if not key:
                continue
            url = resolve_base_url(provider)
            with contextlib.suppress(Exception):
                self._clients[provider] = OpenAI(api_key=key, base_url=url)

    def _get_client(self, provider: str):
        return self._clients.get(provider)

    @property
    def client(self) -> Any:
        return next(iter(self._clients.values()), None)

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

        delay = {"sensenova": 0.0, "openrouter": 1.0, "openai": 1.0}.get(provider, self.config.ai.rate_limit_delay)
        timeout = {"sensenova": 30, "openrouter": 45, "openai": 30}.get(provider, 30)
        for attempt in range(retries):
            try:
                if attempt > 0:
                    time.sleep(delay * (backoff_factor ** (attempt - 1)))
                print(f"[LLM] {provider}/{model} (role={role}, {tag}, attempt {attempt + 1}/{retries})")

                response = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                )
                choice = response.choices[0]
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
                time.sleep(2)

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
        role_cfg = self.config.ai.roles.get(role)
        if not role_cfg:
            return {"content": f"Error: unknown role '{role}'", "reasoning": None}

        provider = role_cfg.provider or self.config.ai.default_provider
        fb_provider = role_cfg.fallback_provider or provider
        primary_ok = self._get_client(provider) is not None
        fb_ok = self._get_client(fb_provider) is not None if role_cfg.fallback_model else False

        if not primary_ok and not fb_ok:
            return {"content": f"Error: no client for provider '{provider}'", "reasoning": None}

        temp = temperature if temperature is not None else self.config.ai.temperature
        max_t = max_tokens if max_tokens is not None else self.config.ai.max_tokens

        if primary_ok:
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

        if role_cfg.fallback_model and fb_ok:
            print(f"[LLM] Primary failed, switching to fallback model: {fb_provider}/{role_cfg.fallback_model}")
            result = self._try_model(
                messages=messages, role=role,
                provider=fb_provider,
                model=role_cfg.fallback_model,
                temperature=temp, max_tokens=max_t,
                retries=retries, backoff_factor=backoff_factor,
                tag="fallback",
            )
            if result is not None:
                return result

        raise RuntimeError("LLM request failed after maximum retries (including fallback).")

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
