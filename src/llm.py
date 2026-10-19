import re
import time
from typing import Any

from src.config import (
    _PROVIDER_ENV,
    AppConfig,
    ModelLeg,
    resolve_api_key,
    resolve_base_url,
    resolve_jev_api_key,
)

# Retry waits are multiplicative on this floor. Free-tier providers answer 429
# within milliseconds, so a zero floor turns retries into a hammering storm.
MIN_RETRY_DELAY = 2.0

# per-provider 的最小重试间隔（秒）。数字全部来自实测（spec「per-provider 参数」表）：
# sensenova 的限免额度最紧 —— 3s 内发第二发必 429（EndpointTPMExceeded / EndpointRPMExceeded）。
PROVIDER_MIN_INTERVAL: dict[str, float] = {
    "sensenova": 6.0,
    "openrouter": 2.0,
    "siliconflow": 2.0,
    "agnes": 2.0,
    "zenmux": 2.0,
    "tokenrhythm": 2.0,
    "intern": 2.0,
}
# 单次请求的超时（秒）。超时太紧的代价是"每次都先白烧一次超时重试"（sensenova 在 30s 下
# 就是这样），而不是失败；所以宁可给宽。
PROVIDER_TIMEOUTS: dict[str, int] = {
    "sensenova": 60,      # glm-5.2 写作实测 66.0s
    "openrouter": 60,     # super-vl 翻译实测 39.2s，已逼近旧的 45s
    "siliconflow": 90,    # Hunyuan 长输出 >30s：模型慢，不是通道死
    "agnes": 60,          # 写作实测最大 40.6s
    "zenmux": 60,         # 写作实测 53.3s
    "tokenrhythm": 90,    # 预留（当前无 role 使用）
    "intern": 120,        # 预留（当前无 role 使用）
}
DEFAULT_MIN_INTERVAL = 2.0
DEFAULT_TIMEOUT = 30

# 401/402/403 是账户级闸门：欠费、密钥失效、权限问题重试都不会变好，而且同 provider
# 的其它模型走同一个闸门（硅基流动 402 时 Hunyuan 与 Qwen2.5 报同一个错）。
ACCOUNT_LEVEL_STATUS = (401, 402, 403)
# 404 只说明这一个模型下架了（免费档随时下架,sensenova 6.7 是前例),provider 本身仍可用。
MODEL_LEVEL_STATUS = (404,)
NON_RETRYABLE_STATUS = ACCOUNT_LEVEL_STATUS + MODEL_LEVEL_STATUS


def _error_status(exc: Exception) -> int | None:
    """从 SDK 异常里取 HTTP 状态码；取不到就返回 None（按可重试处理）。"""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    match = re.search(r"Error code:\s*(\d{3})", str(exc))
    return int(match.group(1)) if match else None


def _leg_id(leg: ModelLeg) -> str:
    """per-(role, leg) 熔断键里的腿标识。"""
    return f"{leg.provider}/{leg.model}"


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
        self._banned_providers: set[str] = set()
        # 熔断粒度是 (role, provider/model)：一条腿死了不该让同 provider 的其它腿陪葬，
        # 也不该让同一 role 的下一条腿替它挨罚。
        self._exhausted_legs: set[tuple[str, str]] = set()
        self._jev_client: Any = None
        self._jev_disabled = False
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
                # max_retries 必须显式归零：SDK 默认 2,会把我们的 retries=3 乘成
                # 9 次 HTTP —— 10/16 那班 openrouter 逐仓库超时,单模型耗掉 ~135s
                # (3×45s 超时 + 退避),整条 curate 撞上 timeout-minutes 被杀,当天没出报告。
                self._clients[provider] = OpenAI(api_key=key, base_url=url, max_retries=0)
            except Exception as e:
                print(f"[LLM Error] Failed to initialize provider '{provider}': {e}")
        ready = ", ".join(sorted(self._clients)) or "none"
        print(f"[LLM Init] Providers with keys: {ready}")

    def _get_client(self, provider: str):
        if provider in self._banned_providers:
            return None
        return self._clients.get(provider)

    @property
    def client(self) -> Any:
        return next(iter(self._clients.values()), None)

    def has_provider(self, provider: str) -> bool:
        return provider in self._clients and provider not in self._banned_providers

    def legs(self, role: str) -> list[ModelLeg]:
        """role 的降级链，保持配置顺序，并剔除当前不可用的腿。

        剔除两类：没有 client 的 provider（没配 key，或已账户级拉黑），以及本次运行
        已把重试耗尽的 (role, provider/model)。
        """
        if role in self._unavailable_roles:
            return []
        role_cfg = self.config.ai.roles.get(role)
        if role_cfg is None:
            return []
        return [
            leg for leg in role_cfg.chain
            if self.has_provider(leg.provider)
            and (role, _leg_id(leg)) not in self._exhausted_legs
        ]

    def has_role(self, role: str) -> bool:
        """True when the role still has at least one reachable leg.

        Lets pipeline stages skip a channel without burning a request.
        Once every leg is exhausted the role is tripped off for the rest of the
        run — a dead model otherwise costs retries × every repository.
        """
        return bool(self.legs(role))

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

        # 间隔与超时都来自 per-provider 表；MIN_RETRY_DELAY 只是下限，防止某个 provider
        # 配成 0（免费档 429 在毫秒级返回，零下限等于原地猛重）。
        interval = PROVIDER_MIN_INTERVAL.get(provider, DEFAULT_MIN_INTERVAL)
        delay = max(interval, MIN_RETRY_DELAY)
        timeout = PROVIDER_TIMEOUTS.get(provider, DEFAULT_TIMEOUT)
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
                if not content.strip():
                    # 推理型模型会把输出预算烧在 reasoning_content 上，于是返回 200 + 空正文。
                    # 空正文不是"答案"：上层没法用它做任何决策，比稿更会把它静默当成"选 A"。
                    raise LLMError(f"{provider}/{model} returned empty content")

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
                status = _error_status(e)
                if status in NON_RETRYABLE_STATUS:
                    if status in ACCOUNT_LEVEL_STATUS:
                        # 一次就够：整个 provider 对本次运行拉黑,剩余仓库不再付重试税
                        self._banned_providers.add(provider)
                        print(f"[LLM] {provider} 账户级错误 (HTTP {status}) → 本次运行不再调用该 provider")
                    else:
                        print(f"[LLM] {provider}/{model} 模型不可用 (HTTP {status}) → 不再重试该模型")
                    return None
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
        if role in self._unavailable_roles:
            raise LLMError(f"role '{role}' already tripped off this run")
        if role not in self.config.ai.roles:
            raise LLMError(f"unknown role '{role}'")

        legs = self.legs(role)
        if not legs:
            # 没配 key 与"重试耗尽"必须分开报，否则会被误诊成后者
            wanted = [f"{leg.provider}/{leg.model}" for leg in self.config.ai.roles[role].chain]
            raise LLMError(f"no client for provider(s) {wanted} (role={role})")

        temp = temperature if temperature is not None else self.config.ai.temperature
        attempts_before = self.failed_attempt_count

        for index, leg in enumerate(legs, start=1):
            # max_tokens 优先级：腿上的专属值 > 调用点 > 全局默认。
            leg_max = leg.max_tokens or max_tokens or self.config.ai.max_tokens
            print(f"[LLM] role={role} → leg {index}/{len(legs)}: {leg.provider}/{leg.model}")
            result = self._try_model(
                messages=messages, role=role,
                provider=leg.provider,
                model=leg.model,
                temperature=temp, max_tokens=leg_max,
                retries=retries, backoff_factor=backoff_factor,
                tag=f"leg {index}/{len(legs)}",
            )
            if result is not None:
                result["leg"] = f"{index}/{len(legs)}"
                return result
            # 这条腿的重试已耗尽：本次运行剩余仓库不再为它付失败税
            self._exhausted_legs.add((role, _leg_id(leg)))

        # 熔断：本次运行剩余仓库不再为该 role 付重试的失败税
        self._unavailable_roles.add(role)
        attempts_used = self.failed_attempt_count - attempts_before
        raise LLMError(
            f"role '{role}' failed after {attempts_used} attempt(s) across {len(legs)} leg(s); "
            f"channel tripped off for the rest of this run."
        )

    def has_judgment(self) -> bool:
        """Jev 判断通道是否可用：有凭证，且本次运行尚未因失败停用。"""
        return bool(resolve_jev_api_key()) and not self._jev_disabled

    def _get_jev_client(self) -> Any:
        if self._jev_client is None:
            from typesafe_sdk import RetryPolicy, TypeSafeClient

            jev = self.config.ai.jev
            # RetryPolicy 默认 max_retries=2，和 OpenAI SDK 一样会在我们之下再乘一层
            # HTTP 重试 —— 归零，让退避只有我们这一层
            self._jev_client = TypeSafeClient(
                api_key=resolve_jev_api_key(),
                model=jev.model,
                retry=RetryPolicy(max_retries=0),
                timeout=jev.timeout,
            )
        return self._jev_client

    def call_judgment(self, state: dict[str, str],
                      questions: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """一次 System One 调用，返回 {问题ID: {type, ...}}。

        Choice → choice/confidence/probabilities；Noul → noul（yes 的概率）。
        失败一律抛 LLMError 并在本次运行内停用该通道：付费通道报错后继续逐仓库
        敲门只是白烧预算，调用方必须显式降级（比稿这里退回 ultra 字母裁判）。
        """
        if not self.has_judgment():
            raise LLMError("judgment channel unavailable (no TYPESAFE_API_KEY, or disabled)")

        try:
            response = self._get_jev_client().system_one(state=state, questions=questions)
        except Exception as e:
            self.failed_attempt_count += 1
            self._jev_disabled = True
            raise LLMError(f"jev judgment failed: {e}") from e

        usage = getattr(response, "usage", None)
        if usage:
            self.total_prompt_tokens += getattr(usage, "input_tokens", 0) or 0
            self.total_completion_tokens += getattr(usage, "output_tokens", 0) or 0
        self.call_count += 1

        answers: dict[str, dict[str, Any]] = {}
        for qid, ans in (getattr(response, "answers", None) or {}).items():
            kind = getattr(ans, "type", None)
            if kind == "choice":
                answers[qid] = {
                    "type": kind,
                    "choice": ans.choice,
                    "confidence": ans.confidence,
                    "probabilities": dict(ans.probabilities or {}),
                }
            elif kind == "noul":
                answers[qid] = {"type": kind, "noul": ans.noul}
        return answers

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
