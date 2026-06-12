"""Tests for src/config.py.

Covers:
- Default config values
- Environment variable overrides (SENSENOVA_API_KEY, webhooks, etc.)
- Multiple provider configurations (sensenova, openai, gemini)
- YAML loading from config/config.yaml
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest
import yaml

# Project root is already added by conftest
from src.config import (
    AppConfig,
    GitHubConfig,
    AIConfig,
    DedupConfig,
    Stage2PreFilterConfig,
    NotificationConfig,
    load_config,
    BASE_DIR,
)


class TestAppConfigDefaults:
    """Test that AppConfig produces correct defaults."""

    def test_github_defaults(self):
        cfg = AppConfig()
        # AppConfig() returns empty defaults (no YAML loaded)
        assert cfg.github.monitored_orgs == []
        assert cfg.github.monitored_users == []
        assert cfg.github.max_trending_repos == 15
        assert cfg.github.max_org_repos == 5

    def test_github_defaults_loaded_from_yaml(self):
        """load_config() populates from config.yaml with monitored orgs."""
        cfg = load_config()
        assert len(cfg.github.monitored_orgs) >= 15
        assert len(cfg.github.monitored_users) >= 5

    def test_ai_defaults(self):
        cfg = AppConfig()
        assert cfg.ai.default_provider == "sensenova"
        assert cfg.ai.model_v3 == "sensenova-6.7-flash-lite"
        assert cfg.ai.model_r1 == "sensenova-6.7-flash-lite"
        assert cfg.ai.temperature == 0.3
        assert cfg.ai.max_tokens == 4096
        assert cfg.ai.rate_limit_delay == 2.0
        assert cfg.ai.api_key is None
        assert cfg.ai.base_url is None

    def test_dedup_defaults(self):
        cfg = AppConfig()
        assert cfg.dedup.high_star_threshold == 10000
        assert cfg.dedup.archive_threshold == 3
        assert cfg.dedup.archive_cooldown_days == 30
        assert cfg.dedup.history_file == "reports/repo_history.json"
        assert cfg.dedup.archive_file == "reports/high_star_archive.json"

    def test_stage2_pre_filter_defaults(self):
        cfg = AppConfig()
        assert cfg.stage2_pre_filter.enabled is True
        assert cfg.stage2_pre_filter.max_repos == 88

    def test_notification_defaults(self):
        cfg = AppConfig()
        assert cfg.notifications.feishu_webhook_url is None
        assert cfg.notifications.slack_webhook_url is None
        assert cfg.notifications.discord_webhook_url is None
        assert cfg.notifications.local_report_dir == "./reports"
        assert cfg.notifications.save_raw_data is True

    def test_custom_values_override_defaults(self):
        """Verify that passing values to the model overrides defaults."""
        cfg = AppConfig(
            github=GitHubConfig(max_trending_repos=5),
            ai=AIConfig(temperature=0.7, max_tokens=2048),
            dedup=DedupConfig(high_star_threshold=5000, archive_threshold=5),
        )
        assert cfg.github.max_trending_repos == 5
        assert cfg.ai.temperature == 0.7
        assert cfg.ai.max_tokens == 2048
        assert cfg.dedup.high_star_threshold == 5000
        assert cfg.dedup.archive_threshold == 5


class TestLoadConfig:
    """Test the load_config() function with YAML and env overrides."""

    def test_load_config_loads_yaml(self):
        """load_config() should load values from config/config.yaml."""
        config = load_config()
        assert isinstance(config, AppConfig)
        # The actual config.yaml has specific values
        assert config.ai.default_provider == "sensenova"
        assert config.dedup.high_star_threshold == 10000

    def test_missing_yaml_no_crash(self, monkeypatch):
        """If YAML file is missing, load_config should still return defaults."""
        monkeypatch.setattr("src.config.BASE_DIR", Path("/nonexistent"))
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.ai.default_provider == "sensenova"

    def test_sensenova_api_key_from_env(self, monkeypatch):
        """Sensenova reads SENSENOVA_API_KEY env var."""
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova-test")
        config = load_config()
        assert config.ai.api_key == "sk-sensenova-test"

    def test_sensenova_base_url_default(self, monkeypatch):
        """Sensenova provider defaults base_url to token.sensenova.cn."""
        monkeypatch.delenv("SENSENOVA_BASE_URL", raising=False)
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-test")
        config = load_config()
        assert config.ai.base_url == "https://token.sensenova.cn/v1"

    def test_sensenova_base_url_custom(self, monkeypatch):
        """SENSENOVA_BASE_URL env var overrides default."""
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-test")
        monkeypatch.setenv("SENSENOVA_BASE_URL", "https://custom.sensenova.cn/v1")
        config = load_config()
        assert config.ai.base_url == "https://custom.sensenova.cn/v1"



    def test_sensenova_base_url_override(self, monkeypatch):
        """Sensenova provider should honor SENSENOVA_BASE_URL env var."""
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-test")
        monkeypatch.setenv("SENSENOVA_BASE_URL", "https://custom.sensenova.cn/v1")
        config = load_config()
        assert config.ai.base_url == "https://custom.sensenova.cn/v1"

    def test_webhook_overrides_from_env(self, monkeypatch):
        """Webhook URLs from env should override config file values."""
        monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://feishu.test/hook")
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://slack.test/hook")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
        config = load_config()
        assert config.notifications.feishu_webhook_url == "https://feishu.test/hook"
        assert config.notifications.slack_webhook_url == "https://slack.test/hook"
        assert config.notifications.discord_webhook_url == "https://discord.test/hook"

    def test_openai_provider_config_no_api_key(self):
        """When no API key is set, the config still works with defaults."""
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.ai.model_v3 == "sensenova-6.7-flash-lite"


class TestModelValidation:
    """Test Pydantic model field types and validation."""

    def test_github_config_requires_lists(self):
        """GitHubConfig monitored lists default to empty."""
        cfg = GitHubConfig()
        assert cfg.monitored_orgs == []
        assert cfg.monitored_users == []

    def test_aiconfig_no_key_by_default(self):
        cfg = AIConfig()
        assert cfg.api_key is None

    def test_aiconfig_with_key(self):
        cfg = AIConfig(api_key="sk-test")
        assert cfg.api_key == "sk-test"

    def test_empty_config_yaml_fails_gracefully(self):
        """An empty dict should produce a valid AppConfig with defaults."""
        cfg = AppConfig(**{})
        assert cfg.ai.default_provider == "sensenova"
        assert cfg.dedup.archive_threshold == 3
