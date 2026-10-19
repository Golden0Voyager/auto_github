import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

BASE_DIR = Path(__file__).resolve().parent.parent


class GitHubConfig(BaseModel):
    monitored_orgs: list[str] = Field(default_factory=list)
    monitored_users: list[str] = Field(default_factory=list)
    max_trending_repos: int = 15
    max_org_repos: int = 5


class ModelLeg(BaseModel):
    """降级链里的一条腿。"""

    model: str
    provider: str
    # 推理型模型会把输出预算烧在 reasoning_content 上；这条例外只对该腿生效。
    max_tokens: int | None = None


class RoleConfig(BaseModel):
    """一个 role 的有序降级链：逐腿尝试，第一条给出非空正文的腿胜出。

    只保留 `chain`；`model` / `provider` 是 L1 的只读视图，让日志与旧调用点不必
    知道链的存在。旧的 `{model, provider, fallback_model, fallback_provider}` 形状
    仍可构造，会被归一化成两条腿的链 —— config.yaml 与代码默认值因此可以分两步迁移。
    """

    chain: list[ModelLeg] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_shape(cls, data: Any) -> Any:
        if isinstance(data, dict) and "chain" not in data and "model" in data:
            legs: list[dict[str, Any]] = [
                {"model": data["model"], "provider": data.get("provider") or "openrouter"},
            ]
            if data.get("fallback_model"):
                legs.append({
                    "model": data["fallback_model"],
                    "provider": data.get("fallback_provider") or "openrouter",
                })
            return {"chain": legs}
        return data

    @property
    def model(self) -> str:
        """L1 的模型名。"""
        return self.chain[0].model

    @property
    def provider(self) -> str:
        """L1 的 provider。"""
        return self.chain[0].provider


class JevConfig(BaseModel):
    """比稿判断通道（TypeSafe System One）。

    不是 OpenAI 兼容协议，走独立 client；凭证是环境变量 TYPESAFE_API_KEY。
    """

    model: str = "jev-latest"
    # 低于 confidence_threshold 或漏译概率高于 incomplete_threshold 时,升级给 ultra 复判
    confidence_threshold: float = 0.6
    incomplete_threshold: float = 0.5
    timeout: float = 30.0


class AIConfig(BaseModel):
    default_provider: str = "openrouter"
    temperature: float = 0.3
    max_tokens: int = 8192
    rate_limit_delay: float = 4.0
    jev: JevConfig = Field(default_factory=JevConfig)
    # Mirror of config/config.yaml `ai.roles` for runs without that file; keep in sync.
    # 每 role 一条有序链：越靠前的腿越可靠（spec「最终降级链」与「排序原则」）。
    roles: dict[str, RoleConfig] = Field(default_factory=lambda: {
        "classifier": RoleConfig(chain=[
            # 首腿不放 sensenova：classifier 的单发是全管线最大的一枪，而 sensenova 的
            # 限免额度按低频小请求设计（实测 first-attempt 0/3）。
            ModelLeg(model="agnes-3.0-flash", provider="agnes"),
            ModelLeg(model="nvidia/nemotron-3.5-super-vl:free", provider="openrouter"),
            ModelLeg(model="glm-5.2", provider="sensenova"),
            ModelLeg(model="sensenova-6.8-flash-lite", provider="sensenova"),
        ]),
        "writer": RoleConfig(chain=[
            ModelLeg(model="agnes-3.0-flash", provider="agnes"),
            ModelLeg(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter"),
            ModelLeg(model="inclusionai/ling-3.1-flash", provider="zenmux"),
            ModelLeg(model="sensenova-6.8-flash-lite", provider="sensenova"),
        ]),
        "translator_a": RoleConfig(chain=[
            ModelLeg(model="tencent/Hunyuan-MT-7B", provider="siliconflow"),
            ModelLeg(model="nvidia/nemotron-3.5-super-vl:free", provider="openrouter"),
            ModelLeg(model="inclusionai/ling-3.1-flash", provider="zenmux"),
            ModelLeg(model="sensenova-6.8-flash-lite", provider="sensenova"),
        ]),
        "translator_b": RoleConfig(chain=[
            ModelLeg(model="agnes-2.5-flash", provider="agnes"),
            ModelLeg(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter"),
            ModelLeg(model="inclusionai/ling-3.1-flash", provider="zenmux"),
            ModelLeg(model="sensenova-6.8-flash-lite", provider="sensenova"),
        ]),
        "reviewer": RoleConfig(chain=[
            # Jev（TypeSafe）是链前的前置判断通道，不在这条链里（见 ai.jev）。
            ModelLeg(model="agnes-3.0-flash", provider="agnes"),
            ModelLeg(model="glm-5.2", provider="sensenova"),
            ModelLeg(model="inclusionai/ling-3.1-flash", provider="zenmux"),
        ]),
    })
    # writer 的 A/B：'a' 就是上面 roles.writer 本身（不重复定义）,
    # 这里只放替代方案。切换 = 把 writer_variant 改成 'b',
    # 或设 WRITER_VARIANT 环境变量覆盖（'config'/空值表示不覆盖）。
    writer_variant: str = "a"
    writer_variants: dict[str, RoleConfig] = Field(default_factory=lambda: {
        # b 案只换首腿：L2 就是 A 案的首腿，两案互为兜底。
        "b": RoleConfig(chain=[
            ModelLeg(model="agnes-2.5-flash", provider="agnes"),
            ModelLeg(model="agnes-3.0-flash", provider="agnes"),
            ModelLeg(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter"),
            ModelLeg(model="inclusionai/ling-3.1-flash", provider="zenmux"),
            ModelLeg(model="sensenova-6.8-flash-lite", provider="sensenova"),
        ]),
    })
    # roles.writer 的原始定义（A 案）,供可逆切换
    _writer_base: RoleConfig | None = PrivateAttr(default=None)

    def apply_writer_variant(self) -> None:
        """把选中的变体写进 roles['writer'],下游所有读取路径不变。

        'a' 不是"什么都不做"：roles.writer 可能已经被上一轮变体覆写过,
        必须回到构造时留存的原始定义,否则 env 覆盖不回 A 案。
        """
        if self._writer_base is None:
            # chain 是 list[ModelLeg]：浅拷贝会让本属性与 roles.writer 共享同一批
            # ModelLeg 对象，切回 a 时拿到的就是被 b 那条链改过的型号。
            current = self.roles.get("writer")
            if current is None:
                raise ValueError("ai.roles 必须定义 writer")
            self._writer_base = current.model_copy(deep=True)
        base = self._writer_base.model_copy(deep=True)

        if self.writer_variant in ("", "a"):
            self.roles["writer"] = base
            return

        chosen = self.writer_variants.get(self.writer_variant)
        if chosen is None:
            options = ", ".join(["a", *sorted(self.writer_variants)])
            print(f"[Config Warning] writer_variant='{self.writer_variant}' 未定义 (可选: {options}),回退到 a。")
            self.writer_variant = "a"
            self.roles["writer"] = base
            return
        self.roles["writer"] = chosen.model_copy(deep=True)

    @model_validator(mode="after")
    def _resolve_writer_variant(self) -> "AIConfig":
        self.apply_writer_variant()
        return self


_PROVIDER_ENV = {
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    "sensenova": ("SENSENOVA_API_KEY", "SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "siliconflow": ("SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"),
    # 降级链上的两家，以及两家预注册（tokenrhythm / intern 当前无 role 使用，
    # 预注册是为了以后切换只改 config.yaml）。
    "agnes": ("AGNES_API_KEY", "AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1"),
    "zenmux": ("ZENMUX_API_KEY", "ZENMUX_BASE_URL", "https://zenmux.ai/api/v1"),
    "tokenrhythm": ("TOKENRHYTHM_API_KEY", "TOKENRHYTHM_BASE_URL", "https://tokenrhythm.studio/v1"),
    "intern": ("INTERN_AI_API_KEY", "INTERN_AI_BASE_URL", "https://discovery-api.intern-ai.org.cn/v1"),
}


class DedupConfig(BaseModel):
    high_star_threshold: int = 10000
    archive_threshold: int = 3
    archive_cooldown_days: int = 30
    history_file: str = "reports/repo_history.json"
    archive_file: str = "reports/high_star_archive.json"
    first_seen_window_days: int = 90
    history_ttl_days: int = 365
    max_cooldown_days: int = 90
    steps_cooldown: bool = True
    cycles_file: str = "reports/repo_cycles.json"


class BucketAllocationConfig(BaseModel):
    enabled: bool = True
    total_slots: int = 9
    early_bird: int = 3
    high_star_hot: int = 3
    deep_dive: int = 3
    diversity_enabled: bool = True
    diversity_max_ratio: float = 0.4

    @model_validator(mode="after")
    def check_quota_sum(self) -> "BucketAllocationConfig":
        # _bucket_allocate fills/trims against total_slots; a mismatch silently
        # changes the intended per-bucket exposure.
        allocated = self.early_bird + self.high_star_hot + self.deep_dive
        if allocated != self.total_slots:
            raise ValueError(
                f"bucket_allocation quotas sum to {allocated}, must equal total_slots={self.total_slots}"
            )
        return self


class Stage2PreFilterConfig(BaseModel):
    enabled: bool = True
    max_repos: int = 88


class NotificationConfig(BaseModel):
    feishu_webhook_url: str | None = None
    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    local_report_dir: str = "./reports"
    save_raw_data: bool = True


class AppConfig(BaseModel):
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    bucket_allocation: BucketAllocationConfig = Field(default_factory=BucketAllocationConfig)
    stage2_pre_filter: Stage2PreFilterConfig = Field(default_factory=Stage2PreFilterConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)


def resolve_api_key(provider: str) -> str | None:
    entry = _PROVIDER_ENV.get(provider)
    return os.getenv(entry[0]) if entry else None


def resolve_jev_api_key() -> str | None:
    """Jev 只有一个固定环境变量，不属于 _PROVIDER_ENV（非 OpenAI 协议）。"""
    return os.getenv("TYPESAFE_API_KEY")


def resolve_base_url(provider: str) -> str | None:
    """provider 的 base_url：环境变量可覆盖，但 host 必须与代码默认值一致。

    本机 shell 常带着 `SENSENOVA_BASE_URL=api.sensenova.cn/compatible-mode/v2` 这类通用端点，
    而 Token Plan 的限免模型只在 `token.sensenova.cn` 上：指错端点会稳定 404，而 404 属模型级
    不可重试 —— 整条腿 0.1s 秒失败，看起来就像"模型下架"。白名单直接从默认 base_url 派生，
    不另立一张会漂的表。宁可响一声，不要静默降级。
    """
    entry = _PROVIDER_ENV.get(provider)
    if entry is None:
        return None
    url_env, default = entry[1], entry[2]
    override = os.getenv(url_env)
    if override and urlparse(override).hostname != urlparse(default).hostname:
        expected = urlparse(default).hostname
        print(f"[Config Warning] {url_env}={override} 的 host 不在 {provider} 的白名单内"
              f"（{expected}），回落到 {default}。")
        return default
    return override or default


def load_config() -> AppConfig:
    yaml_path = BASE_DIR / "config" / "config.yaml"
    if yaml_path.exists():
        with open(yaml_path, encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
    else:
        yaml_data = {}
    config = AppConfig(**yaml_data)

    for env_key, attr in [("FEISHU_WEBHOOK_URL", "feishu_webhook_url"),
                           ("SLACK_WEBHOOK_URL", "slack_webhook_url"),
                           ("DISCORD_WEBHOOK_URL", "discord_webhook_url")]:
        val = os.getenv(env_key)
        if val:
            setattr(config.notifications, attr, val)

    # workflow_dispatch 的默认项是 'config'（= 不覆盖），schedule 触发时 env 为空。
    # 这两种情况都必须让 config.yaml 的 writer_variant 说话。
    env_variant = (os.getenv("WRITER_VARIANT") or "").strip().lower()
    if env_variant and env_variant != "config":
        config.ai.writer_variant = env_variant
        config.ai.apply_writer_variant()
    return config
