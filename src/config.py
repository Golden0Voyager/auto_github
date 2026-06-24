import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent


class GitHubConfig(BaseModel):
    monitored_orgs: list[str] = Field(default_factory=list)
    monitored_users: list[str] = Field(default_factory=list)
    max_trending_repos: int = 15
    max_org_repos: int = 5


class RoleConfig(BaseModel):
    model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    provider: str = "openrouter"
    fallback_model: str | None = None
    fallback_provider: str | None = None


class AIConfig(BaseModel):
    default_provider: str = "openrouter"
    temperature: float = 0.3
    max_tokens: int = 8192
    rate_limit_delay: float = 4.0
    api_key: str | None = None
    base_url: str | None = None
    roles: dict[str, RoleConfig] = Field(default_factory=lambda: {
        "classifier": RoleConfig(model="sensenova-6.7-flash-lite", provider="sensenova"),
        "writer": RoleConfig(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter"),
        "translator_a": RoleConfig(model="sensenova-6.7-flash-lite", provider="sensenova"),
        "translator_b": RoleConfig(model="google/gemma-4-31b-it:free", provider="openrouter",
                                   fallback_model="nvidia/nemotron-3-ultra-550b-a55b:free", fallback_provider="openrouter"),
        "reviewer": RoleConfig(model="nvidia/nemotron-3-ultra-550b-a55b:free", provider="openrouter",
                               fallback_model="nvidia/nemotron-3-super-120b-a12b:free", fallback_provider="openrouter"),
    })
    model_v3: str | None = Field(default=None, exclude=True)
    model_r1: str | None = Field(default=None, exclude=True)


_PROVIDER_ENV = {
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    "sensenova": ("SENSENOVA_API_KEY", "SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1"),
    "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1"),
}


class DedupConfig(BaseModel):
    high_star_threshold: int = 10000
    archive_threshold: int = 3
    archive_cooldown_days: int = 30
    history_file: str = "reports/repo_history.json"
    archive_file: str = "reports/high_star_archive.json"


class BucketAllocationConfig(BaseModel):
    enabled: bool = True
    total_slots: int = 9
    early_bird: int = 3
    high_star_hot: int = 3
    deep_dive: int = 3


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


def resolve_base_url(provider: str) -> str | None:
    entry = _PROVIDER_ENV.get(provider)
    return os.getenv(entry[1], entry[2]) if entry else None


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
    return config
