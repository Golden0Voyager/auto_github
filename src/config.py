import os
import yaml
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

# Base Directory of the Project
BASE_DIR = Path(__file__).resolve().parent.parent

class GitHubConfig(BaseModel):
    monitored_orgs: List[str] = Field(default_factory=list)
    monitored_users: List[str] = Field(default_factory=list)
    max_trending_repos: int = 15
    max_org_repos: int = 5

class AIConfig(BaseModel):
    default_provider: str = "sensenova"
    # Token Plan 端点 + sensenova-6.7-flash-lite，详见 docs/api/sensenova-token-plan-usage.md
    model_v3: str = "sensenova-6.7-flash-lite"
    model_r1: str = "sensenova-6.7-flash-lite"
    temperature: float = 0.3
    max_tokens: int = 4096
    rate_limit_delay: float = 2.0
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class DedupConfig(BaseModel):
    """高星项目去重配置：节省 LLM 算力 + 给新兴项目留展位。"""
    high_star_threshold: int = 10000
    archive_threshold: int = 3
    archive_cooldown_days: int = 30
    history_file: str = "reports/repo_history.json"
    archive_file: str = "reports/high_star_archive.json"

class NotificationConfig(BaseModel):
    feishu_webhook_url: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    local_report_dir: str = "./reports"
    save_raw_data: bool = True

class AppConfig(BaseModel):
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)

def load_config() -> AppConfig:
    """Loads configuration from YAML and overrides with environment variables."""
    yaml_path = BASE_DIR / "config" / "config.yaml"
    
    if yaml_path.exists():
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f) or {}
    else:
        yaml_data = {}
        
    config = AppConfig(**yaml_data)
    
    # Override AI API details from Env
    # Support multiple names for flexible integration
    sensenova_key = os.getenv("SENSENOVA_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    
    if config.ai.default_provider == "sensenova":
        config.ai.api_key = sensenova_key or openai_key
        # Token Plan 端点：https://token.sensenova.cn/v1（注意：不是 api.sensenova.cn）
        config.ai.base_url = os.getenv("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1")
    elif config.ai.default_provider == "gemini":
        config.ai.api_key = gemini_key
        config.ai.base_url = os.getenv("GEMINI_BASE_URL")
    else: # openai
        config.ai.api_key = openai_key
        config.ai.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        
    # Override webhooks from environment variables if present
    feishu_env = os.getenv("FEISHU_WEBHOOK_URL")
    if feishu_env:
        config.notifications.feishu_webhook_url = feishu_env
        
    slack_env = os.getenv("SLACK_WEBHOOK_URL")
    if slack_env:
        config.notifications.slack_webhook_url = slack_env
        
    discord_env = os.getenv("DISCORD_WEBHOOK_URL")
    if discord_env:
        config.notifications.discord_webhook_url = discord_env
        
    return config
