"""Tests for src/config.py.

Covers:
- Default config values
- Environment variable overrides (SENSENOVA_API_KEY, webhooks, etc.)
- Multiple provider configurations (sensenova, openai, gemini)
- YAML loading from config/config.yaml
"""

from pathlib import Path

import yaml

# Project root is already added by conftest
from src.config import (
    _PROVIDER_ENV,
    BASE_DIR,
    AIConfig,
    AppConfig,
    DedupConfig,
    GitHubConfig,
    load_config,
)


class TestAppConfigDefaults:
    """Test that AppConfig produces correct defaults."""

    def test_github_defaults(self):
        cfg = AppConfig()
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
        assert cfg.ai.default_provider == "openrouter"
        assert cfg.ai.roles["writer"].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
        assert cfg.ai.roles["reviewer"].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
        assert cfg.ai.roles["reviewer"].fallback_model == "nvidia/nemotron-3-super-120b-a12b:free"
        assert cfg.ai.temperature == 0.3
        assert cfg.ai.max_tokens == 8192
        assert cfg.ai.rate_limit_delay == 4.0
        assert cfg.ai.roles["classifier"].provider == "sensenova"
        assert cfg.ai.roles["translator_a"].provider == "siliconflow"
        assert cfg.ai.roles["translator_b"].provider == "siliconflow"

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
        assert config.ai.default_provider == "openrouter"

    def test_missing_yaml_no_crash(self, monkeypatch):
        """If YAML file is missing, load_config should still return defaults."""
        monkeypatch.setattr("src.config.BASE_DIR", Path("/nonexistent"))
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.ai.default_provider == "openrouter"

    def test_resolve_api_key_from_env(self, monkeypatch):
        """resolve_api_key reads from the right env var."""
        from src.config import resolve_api_key
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert resolve_api_key("openrouter") == "sk-or-test"

    def test_resolve_api_key_missing_returns_none(self):
        from src.config import resolve_api_key
        assert resolve_api_key("nonexistent") is None

    def test_resolve_base_url_default(self, monkeypatch):
        monkeypatch.delenv("SENSENOVA_BASE_URL", raising=False)
        from src.config import resolve_base_url
        monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
        assert resolve_base_url("openrouter") == "https://openrouter.ai/api/v1"
        assert resolve_base_url("sensenova") == "https://token.sensenova.cn/v1"

    def test_sensenova_still_works_with_explicit_config(self, monkeypatch):
        """SensoNova role should still work when explicitly configured."""
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova-test")
        cfg = AppConfig()
        cfg.ai.roles["classifier"] = cfg.ai.roles["classifier"]  # default is sensenova
        assert cfg.ai.roles["classifier"].model == "sensenova-6.8-flash-lite"
        assert cfg.ai.roles["classifier"].provider == "sensenova"

    def test_webhook_overrides_from_env(self, monkeypatch):
        """Webhook URLs from env should override config file values."""
        monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://feishu.test/hook")
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://slack.test/hook")
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/hook")
        config = load_config()
        assert config.notifications.feishu_webhook_url == "https://feishu.test/hook"
        assert config.notifications.slack_webhook_url == "https://slack.test/hook"
        assert config.notifications.discord_webhook_url == "https://discord.test/hook"

    def test_default_provider_no_api_key(self):
        """When no API key is set, the config still works with defaults."""
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.ai.roles["writer"].model == "nvidia/nemotron-3-ultra-550b-a55b:free"


class TestModelValidation:
    """Test Pydantic model field types and validation."""

    def test_github_config_requires_lists(self):
        """GitHubConfig monitored lists default to empty."""
        cfg = GitHubConfig()
        assert cfg.monitored_orgs == []
        assert cfg.monitored_users == []

    def test_aiconfig_roles_cover_all_five(self):
        cfg = AIConfig()
        assert set(cfg.roles) == {"classifier", "writer", "translator_a", "translator_b", "reviewer"}
        for role, role_cfg in cfg.roles.items():
            assert role_cfg.model, role
            assert role_cfg.provider in _PROVIDER_ENV, role

    def test_code_role_defaults_mirror_config_yaml(self):
        """config.py 的 roles 默认值必须与 config/config.yaml 一致。

        AGENTS.md 要求两处同步；历史上 translator 通道就是因此漂过，
        把 "Error: no client..." 写进了报告正文。
        """
        yaml_path = BASE_DIR / "config" / "config.yaml"
        yaml_roles = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["ai"]["roles"]
        cfg = AIConfig()
        for role, expected in yaml_roles.items():
            actual = cfg.roles[role]
            assert actual.model == expected["model"], role
            assert actual.provider == expected["provider"], role
            assert actual.fallback_model == expected.get("fallback_model"), role
            assert actual.fallback_provider == expected.get("fallback_provider"), role

    def test_empty_config_yaml_fails_gracefully(self):
        """An empty dict should produce a valid AppConfig with defaults."""
        cfg = AppConfig(**{})
        assert cfg.ai.default_provider == "openrouter"
        assert cfg.dedup.archive_threshold == 3


ULTRA = "nvidia/nemotron-3-ultra-550b-a55b:free"
LIGHTNING = "nvidia/nemotron-3.5-lightning:free"


class TestWriterVariant:
    """writer 的 A/B 开关：'a' 就是 roles.writer,变体表只放替代方案。"""

    def test_default_variant_keeps_current_writer(self):
        cfg = AIConfig()
        assert cfg.writer_variant == "a"
        assert cfg.roles["writer"].model == ULTRA

    def test_variant_b_swaps_writer_and_reverses_fallback(self):
        cfg = AIConfig(writer_variant="b")
        writer = cfg.roles["writer"]
        assert writer.model == LIGHTNING
        assert writer.fallback_model == ULTRA

    def test_other_roles_untouched_by_variant(self):
        cfg = AIConfig(writer_variant="b")
        assert cfg.roles["reviewer"].model == ULTRA
        assert cfg.roles["classifier"].provider == "sensenova"

    def test_unknown_variant_falls_back_to_a(self):
        cfg = AIConfig(writer_variant="nope")
        assert cfg.writer_variant == "a"
        assert cfg.roles["writer"].model == ULTRA

    def test_env_var_overrides_yaml_variant(self, monkeypatch):
        monkeypatch.setenv("WRITER_VARIANT", "b")
        cfg = load_config()
        assert cfg.ai.writer_variant == "b"
        assert cfg.ai.roles["writer"].model == LIGHTNING

    def test_env_var_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("WRITER_VARIANT", "B")
        assert load_config().ai.roles["writer"].model == LIGHTNING

    def test_yaml_variant_block_matches_code_default(self):
        """变体表也有 drift 风险：yaml 的 writer_variants 必须与代码默认一致。"""
        yaml_ai = yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8"))["ai"]
        yaml_variants = yaml_ai.get("writer_variants", {})
        code_variants = AIConfig().writer_variants
        assert set(yaml_variants) == set(code_variants)
        for name, expected in yaml_variants.items():
            actual = code_variants[name]
            assert actual.model == expected["model"], name
            assert actual.provider == expected["provider"], name
            assert actual.fallback_model == expected.get("fallback_model"), name
