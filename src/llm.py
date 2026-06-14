import os
import time
from typing import Any, Dict, List, Optional

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
        self._clients: Dict[str, Any] = {}
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
            try:
                self._clients[provider] = OpenAI(api_key=key, base_url=url)
            except Exception:
                pass

    def _get_client(self, provider: str):
        return self._clients.get(provider)

    @property
    def client(self) -> Any:
        return next(iter(self._clients.values()), None)

    def call_llm(
        self,
        messages: List[Dict[str, str]],
        role: str = "writer",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retries: int = 8,
        backoff_factor: float = 3.0,
    ) -> Dict[str, Any]:
        role_cfg = self.config.ai.roles.get(role)
        if not role_cfg:
            return {"content": f"Error: unknown role '{role}'", "reasoning": None}

        provider = role_cfg.provider or self.config.ai.default_provider
        model = role_cfg.model
        temp = temperature if temperature is not None else self.config.ai.temperature
        max_t = max_tokens if max_tokens is not None else self.config.ai.max_tokens

        client = self._get_client(provider)
        if client is None:
            return {"content": f"Error: no client for provider '{provider}'", "reasoning": None}

        # Per-provider rate limit delay: sensenova token plan has no limit,
        # OpenRouter paid tier can handle 1s spacing comfortably.
        delay = {"sensenova": 0.0, "openrouter": 1.0, "openai": 1.0}.get(provider, self.config.ai.rate_limit_delay)
        for attempt in range(retries):
            try:
                if attempt > 0:
                    time.sleep(delay * (backoff_factor ** (attempt - 1)))
                print(f"[LLM] {provider}/{model} (role={role}, attempt {attempt + 1}/{retries})")

                response = client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_t, temperature=temp,
                )
                choice = response.choices[0]
                content = choice.message.content or ""

                reasoning: Optional[str] = None
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
                    raise e
                time.sleep(2)

        raise RuntimeError("LLM request failed after maximum retries.")

    def get_stats(self) -> Dict[str, int]:
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
