import time
from typing import Any, Dict, List, Optional

from src.config import AppConfig


class LLMClient:
    """Wrapper around LLM API with auto_hub.llm as primary backend and direct OpenAI SDK fallback.

    Pipeline gate contract: `self.client` is truthy iff at least one backend is wired,
    matching the pre-migration API that `pipeline.py` relies on for fallback decisions.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.provider = config.ai.default_provider
        self.model_v3 = config.ai.model_v3
        self.model_r1 = config.ai.model_r1

        self.call_count: int = 0
        self.failed_attempt_count: int = 0
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0

        self._hub: Any = None
        self._fallback: Any = None

        if not config.ai.api_key:
            print(
                f"[LLM Warning] No API key found for provider '{self.provider}'. "
                f"LLM requests will fail unless mock runs are used."
            )

        try:
            from auto_hub.llm import LLMClient as HubClient
            self._hub = HubClient.from_env(
                max_retries=5,
                rate_limit_delay=config.ai.rate_limit_delay,
            )
        except (RuntimeError, ImportError):
            pass

        if self._hub is None and config.ai.api_key:
            from openai import OpenAI
            self._fallback = OpenAI(api_key=config.ai.api_key, base_url=config.ai.base_url)

    @property
    def client(self) -> Any:
        """Backward-compat truthy accessor for `pipeline.py` fallback gates."""
        return self._hub if self._hub is not None else self._fallback

    def call_llm(
        self,
        messages: List[Dict[str, str]],
        use_reasoning: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retries: int = 5,
        backoff_factor: float = 2.0,
    ) -> Dict[str, Any]:
        """Calls the LLM with fallback support.

        Delegates to auto_hub.llm when available; on hub failure, transparently
        falls back to the direct OpenAI SDK path so the pipeline never blocks.
        """
        if self._hub is None and self._fallback is None:
            print("[LLM Error] Cannot call LLM: API key is not configured.")
            return {"content": "Error: LLM API key not configured.", "reasoning": None}

        model = self.model_r1 if use_reasoning else self.model_v3
        temp = temperature if temperature is not None else self.config.ai.temperature
        max_t = max_tokens if max_tokens is not None else self.config.ai.max_tokens

        if self._hub is not None:
            return self._call_with_hub_or_fallback(messages, model, temp, max_t, use_reasoning, retries, backoff_factor)

        return self._call_via_fallback(messages, model, temp, max_t, use_reasoning, retries, backoff_factor)

    def _sync_stats_from_hub(self) -> None:
        """Mirror hub CallStats into auto_github counters (single source of truth = hub)."""
        if self._hub is None or not hasattr(self._hub, "stats"):
            return
        snapshot = self._hub.stats.snapshot()
        self.call_count = snapshot["call_count"]
        self.failed_attempt_count = snapshot["failed_attempt_count"]
        self.total_prompt_tokens = snapshot["total_prompt_tokens"]
        self.total_completion_tokens = snapshot["total_completion_tokens"]

    def _call_with_hub_or_fallback(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        use_reasoning: bool,
        retries: int,
        backoff_factor: float,
    ) -> Dict[str, Any]:
        """Try hub first; on hard failure, degrade to direct OpenAI SDK path.

        Hub's CallStats is the single source of truth — auto_github counters
        are mirrored from `self._hub.stats` after every attempt.
        """
        delay = self.config.ai.rate_limit_delay
        last_err: Optional[Exception] = None

        for attempt in range(retries):
            try:
                if attempt > 0:
                    time.sleep(delay * (backoff_factor ** (attempt - 1)))

                print(f"[LLM] Requesting model={model} (Attempt {attempt + 1}/{retries}) [hub]...")

                content = self._hub.chat(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._sync_stats_from_hub()

                time.sleep(self.config.ai.rate_limit_delay / 2.0)
                return {"content": content, "reasoning": None, "model": model}

            except Exception as e:
                last_err = e
                self._sync_stats_from_hub()
                err_msg = str(e).lower()
                print(f"[LLM Warning] Hub attempt {attempt + 1} failed: {e}")

                is_rate_limit = "429" in err_msg or "rate limit" in err_msg or "too many requests" in err_msg
                if is_rate_limit and attempt < retries - 1:
                    continue
                if not is_rate_limit and attempt < retries - 1:
                    time.sleep(2)
                    continue
                break

        if self._fallback is not None:
            print(f"[LLM Info] Hub exhausted ({last_err}); degrading to direct OpenAI SDK.")
            return self._call_via_fallback(
                messages, model, temperature, max_tokens, use_reasoning, retries, backoff_factor
            )

        raise RuntimeError(f"LLM request failed via hub (no fallback available). Last error: {last_err}")

    def _call_via_fallback(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        use_reasoning: bool,
        retries: int,
        backoff_factor: float,
    ) -> Dict[str, Any]:
        """Fallback: direct OpenAI SDK call (same as pre-migration behavior)."""
        kwargs: Dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if not use_reasoning:
            kwargs["temperature"] = temperature

        delay = self.config.ai.rate_limit_delay

        for attempt in range(retries):
            try:
                if attempt > 0:
                    time.sleep(delay * (backoff_factor ** (attempt - 1)))

                print(f"[LLM] Requesting model={model} (Attempt {attempt + 1}/{retries}) [direct]...")

                response = self._fallback.chat.completions.create(**kwargs)
                choice = response.choices[0]
                content = choice.message.content or ""

                usage = getattr(response, "usage", None)
                if usage:
                    self.total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    self.total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                self.call_count += 1

                reasoning: Optional[str] = None
                if hasattr(choice.message, "reasoning_content"):
                    reasoning = getattr(choice.message, "reasoning_content")
                elif hasattr(choice.message, "model_extra") and choice.message.model_extra:
                    reasoning = choice.message.model_extra.get("reasoning_content")

                time.sleep(self.config.ai.rate_limit_delay / 2.0)

                return {"content": content, "reasoning": reasoning, "model": model}

            except Exception as e:
                err_msg = str(e)
                self.failed_attempt_count += 1
                print(f"[LLM Warning] Direct attempt {attempt + 1} failed: {err_msg}")

                if "429" in err_msg or "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                    continue
                elif "temperature" in err_msg.lower() and use_reasoning and "temperature" in kwargs:
                    print("[LLM Info] Retrying R1 without temperature parameter...")
                    del kwargs["temperature"]
                    continue
                else:
                    if attempt == retries - 1:
                        raise e
                    time.sleep(2)

        raise RuntimeError("LLM request failed after maximum retries due to persistent rate limiting.")

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
        if self._hub is not None and hasattr(self._hub, "stats"):
            self._hub.stats.reset()
