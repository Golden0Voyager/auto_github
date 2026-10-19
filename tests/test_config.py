"""Tests for src/config.py.

Covers:
- Default config values
- Environment variable overrides (SENSENOVA_API_KEY, webhooks, etc.)
- Multiple provider configurations (sensenova, openai, gemini)
- YAML loading from config/config.yaml
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

# Project root is already added by conftest
from src.config import (
    _PROVIDER_ENV,
    BASE_DIR,
    AIConfig,
    AppConfig,
    DedupConfig,
    GitHubConfig,
    ModelLeg,
    RoleConfig,
    load_config,
)

# 模型名常量：链的断言引用它们，换型号时一处生效。
AGNES_30 = "agnes-3.0-flash"
AGNES_25 = "agnes-2.5-flash"
GLM_52 = "glm-5.2"
LING_31 = "inclusionai/ling-3.1-flash"
SN_LITE = "sensenova-6.8-flash-lite"
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b:free"
SUPER_VL = "nvidia/nemotron-3.5-super-vl:free"


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
        assert cfg.ai.temperature == 0.3
        assert cfg.ai.max_tokens == 8192
        assert cfg.ai.rate_limit_delay == 4.0
        # 每个 role 都是一条有序链（spec「最终降级链」）；model/provider 只是 L1 的视图。
        assert [leg.model for leg in cfg.ai.roles["classifier"].chain] == [
            AGNES_30, SUPER_VL, GLM_52, SN_LITE,
        ]
        assert [leg.model for leg in cfg.ai.roles["writer"].chain] == [
            AGNES_30, ULTRA, LING_31, SN_LITE,
        ]
        assert cfg.ai.roles["writer"].model == AGNES_30
        assert cfg.ai.roles["writer"].provider == "agnes"
        assert cfg.ai.roles["reviewer"].chain[0].model == AGNES_30
        # 两条翻译通道的尾腿必须是同一条：L1/L2 都失败时，独立性已无从保证，
        # 此时优先"有译文"。
        for role in ("translator_a", "translator_b"):
            assert cfg.ai.roles[role].chain[-1].model == SN_LITE, role
            assert cfg.ai.roles[role].chain[-1].provider == "sensenova", role

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
        """SensoNova 作为显式配置的腿仍然可用（它是分类/翻译链的末位腿）。"""
        monkeypatch.setenv("SENSENOVA_API_KEY", "sk-sensenova-test")
        cfg = AppConfig()
        cfg.ai.roles["classifier"] = RoleConfig(chain=[ModelLeg(model=SN_LITE, provider="sensenova")])
        assert cfg.ai.roles["classifier"].model == SN_LITE
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
        # 不写死具体型号：仓库可以随时切到 B 案，这里只验证 writer 被正确解析
        assert config.ai.roles["writer"].provider == "agnes"
        assert config.ai.roles["writer"].model in {AGNES_30, AGNES_25}


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
            assert [leg.model_dump(exclude_none=True) for leg in actual.chain] == expected["chain"], role

    def test_empty_config_yaml_fails_gracefully(self):
        """An empty dict should produce a valid AppConfig with defaults."""
        cfg = AppConfig(**{})
        assert cfg.ai.default_provider == "openrouter"
        assert cfg.dedup.archive_threshold == 3


class TestRoleConfigChain:
    """RoleConfig 的链式 schema。"""

    def test_chain_is_the_source_of_truth(self):
        rc = RoleConfig(chain=[ModelLeg(model="m1", provider="p1"),
                               ModelLeg(model="m2", provider="p2", max_tokens=256)])
        # model/provider 只是 L1 的视图，日志与旧调用点无需知道链的存在
        assert rc.model == "m1"
        assert rc.provider == "p1"
        assert [leg.model for leg in rc.chain] == ["m1", "m2"]
        assert rc.chain[1].max_tokens == 256
        assert rc.chain[0].max_tokens is None

    def test_rejects_empty_chain(self):
        """空链等于没有通道：必须在构造时就炸，而不是逐仓库调用时才发现。"""
        with pytest.raises(ValidationError):
            RoleConfig(chain=[])

    def test_normalizes_the_legacy_two_leg_shape(self):
        """旧的 {model, provider, fallback_*} 形状仍被接受并归一化成链，
        这样 config.yaml 与代码默认值可以分两步迁移。"""
        rc = RoleConfig(model="m1", provider="p1",
                        fallback_model="m2", fallback_provider="p2")
        assert [(leg.model, leg.provider) for leg in rc.chain] == [("m1", "p1"), ("m2", "p2")]

    def test_legacy_shape_without_fallback_is_a_one_leg_chain(self):
        rc = RoleConfig(model="m1", provider="p1")
        assert [(leg.model, leg.provider) for leg in rc.chain] == [("m1", "p1")]


class TestFallbackChainConstraints:
    """链的设计约束（spec「排序原则」与「腿的独立性约束」），不是形状断言。"""

    def test_translator_channels_start_on_different_models_and_providers(self):
        """A/B 两条通道的首腿必须既不同模型也不同 provider，否则比稿失去意义。"""
        cfg = AIConfig()
        a_head = cfg.roles["translator_a"].chain[0]
        b_head = cfg.roles["translator_b"].chain[0]
        assert a_head.provider != b_head.provider
        assert a_head.model != b_head.model

    def test_reviewer_never_reuses_a_translator_head(self):
        """裁判不能评自己的译文。实测 Hunyuan-MT-7B 在"A 漏 / B 好"的对照里仍选 A
        （A 偏见），而它本身就是 translator_a 的首腿 —— 两条理由都要求把它排除。"""
        cfg = AIConfig()
        translator_heads = {
            cfg.roles["translator_a"].chain[0].model,
            cfg.roles["translator_b"].chain[0].model,
        }
        assert not translator_heads & {leg.model for leg in cfg.roles["reviewer"].chain}

    def test_classifier_does_not_lead_with_sensenova(self):
        """classifier 一轮只发一次（RPM 需求≈0），但它决定全部仓库的评级与分桶，
        失败代价最高；而 sensenova 的限免额度按低频小请求设计（实测 first-attempt 0/3，
        恒 RateLimitExceeded.EndpointTPMExceeded）。所以首腿必须是配额最宽的那条。"""
        assert AIConfig().roles["classifier"].chain[0].provider != "sensenova"

    def test_every_role_has_at_least_three_legs(self):
        """方案 B 的全部意义就是"两条腿不会一起挂"：1+1 在免费档上已被三次事故证伪。"""
        cfg = AIConfig()
        for role, role_cfg in cfg.roles.items():
            assert len(role_cfg.chain) >= 3, role

    def test_every_leg_provider_is_registered(self):
        cfg = AIConfig()
        for role, role_cfg in cfg.roles.items():
            for leg in role_cfg.chain:
                assert leg.provider in _PROVIDER_ENV, f"{role}: {leg.provider}"


class TestWriterVariant:
    """writer 的 A/B 开关：'a' 就是 roles.writer,变体表只放替代方案。"""

    def test_default_variant_keeps_current_writer(self):
        cfg = AIConfig()
        assert cfg.writer_variant == "a"
        assert cfg.roles["writer"].chain[0].model == AGNES_30

    def test_switch_does_not_alias_chain_lists(self):
        """roles.writer 与 _writer_base 必须是深拷贝：浅拷贝会让两条链共享同一批
        ModelLeg 对象，切回 a 时拿到的是被 b 那条链改过的型号。"""
        cfg = AIConfig()
        cfg.writer_variant = "b"
        cfg.apply_writer_variant()
        cfg.roles["writer"].chain[0].model = "mutated"
        cfg.writer_variant = "a"
        cfg.apply_writer_variant()
        assert cfg.roles["writer"].chain[0].model == AGNES_30

    def test_variant_switch_is_reversible_on_one_instance(self):
        """同一实例反复切换时，roles.writer 必须跟着走，不能粘在最后一次覆写上。"""
        cfg = AIConfig(writer_variant="a")
        assert cfg.roles["writer"].chain[0].model == AGNES_30
        cfg.writer_variant = "b"
        cfg.apply_writer_variant()
        assert cfg.roles["writer"].chain[0].model == AGNES_25
        cfg.writer_variant = "a"
        cfg.apply_writer_variant()
        assert cfg.roles["writer"].chain[0].model == AGNES_30
        assert cfg.roles["writer"].chain[1].model == ULTRA

    def test_variant_b_shares_the_tail_and_hands_the_torch_to_a(self):
        """b 案的 L2 就是 A 案的首腿，两案互为兜底。"""
        writer = AIConfig(writer_variant="b").roles["writer"]
        assert writer.chain[0].model == AGNES_25
        assert writer.chain[1].model == AGNES_30

    def test_other_roles_untouched_by_variant(self):
        cfg = AIConfig(writer_variant="b")
        assert cfg.roles["reviewer"].chain[0].model == AGNES_30
        assert cfg.roles["classifier"].chain[0].provider == "agnes"
        assert cfg.roles["classifier"].chain[0].model == AGNES_30

    def test_unknown_variant_falls_back_to_a(self):
        cfg = AIConfig(writer_variant="nope")
        assert cfg.writer_variant == "a"
        assert cfg.roles["writer"].chain[0].model == AGNES_30

    def test_env_var_overrides_yaml_variant(self, monkeypatch):
        monkeypatch.setenv("WRITER_VARIANT", "b")
        cfg = load_config()
        assert cfg.ai.writer_variant == "b"
        assert cfg.ai.roles["writer"].chain[0].model == AGNES_25

    def test_env_var_can_override_back_to_a(self, monkeypatch):
        """env 必须能双向覆盖 yaml，否则 yaml 切到 b 之后这条就变成空跑。"""
        yaml_variant = yaml.safe_load(
            (BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")
        )["ai"]["writer_variant"]
        monkeypatch.setenv("WRITER_VARIANT", "a" if yaml_variant == "b" else "b")
        expected = AGNES_30 if yaml_variant == "b" else AGNES_25
        assert load_config().ai.roles["writer"].chain[0].model == expected

    def test_env_var_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("WRITER_VARIANT", "B")
        assert load_config().ai.roles["writer"].chain[0].model == AGNES_25

    def test_env_sentinel_defers_to_yaml_variant(self, monkeypatch):
        """空值和 'config' 都表示“不覆盖”：schedule 触发时 env 就是空的，
        一旦被当成 'a'，config.yaml 里的切换永远不会生效。"""
        yaml_variant = yaml.safe_load(
            (BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")
        )["ai"]["writer_variant"]
        expected = AGNES_25 if yaml_variant == "b" else AGNES_30
        for sentinel in ("", "config", "  CONFIG  "):
            monkeypatch.setenv("WRITER_VARIANT", sentinel)
            cfg = load_config()
            assert cfg.ai.writer_variant == yaml_variant, sentinel
            assert cfg.ai.roles["writer"].chain[0].model == expected, sentinel

    def test_workflow_leaves_writer_variant_to_config_yaml(self):
        """workflow 不能把 writer_variant 钉死成 'a'，否则 A/B 开关对定时跑是死的。"""
        wf_text = (BASE_DIR / ".github" / "workflows" / "daily_trending.yml").read_text(encoding="utf-8")
        assert "WRITER_VARIANT: ${{ github.event.inputs.writer_variant }}" in wf_text
        assert "writer_variant || 'a'" not in wf_text
        wf = yaml.safe_load(wf_text)
        inputs = wf[True]["workflow_dispatch"]["inputs"]["writer_variant"]
        assert inputs["default"] == "config"
        assert set(inputs["options"]) == {"config", "a", "b"}

    def test_yaml_variant_block_matches_code_default(self):
        """变体表也有 drift 风险：yaml 的 writer_variants 必须与代码默认一致。"""
        yaml_ai = yaml.safe_load((BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8"))["ai"]
        yaml_variants = yaml_ai.get("writer_variants", {})
        code_variants = AIConfig().writer_variants
        assert set(yaml_variants) == set(code_variants)
        for name, expected in yaml_variants.items():
            actual = code_variants[name]
            assert [leg.model_dump(exclude_none=True) for leg in actual.chain] == expected["chain"], name

    def test_jev_yaml_matches_code_default(self):
        """Jev 通道也有 drift 风险：YAML 的判断参数必须与代码默认一致。"""
        yaml_jev = yaml.safe_load(
            (BASE_DIR / "config" / "config.yaml").read_text(encoding="utf-8")
        )["ai"]["jev"]
        assert AIConfig().jev.model_dump() == yaml_jev

    def test_jev_thresholds_gate_the_escalation(self):
        """阈值是升级判据，不能漂到 0（等于永远相信 Jev）或 >1（等于永远复判）。"""
        jev = AIConfig().jev
        assert 0.0 < jev.confidence_threshold < 1.0
        assert 0.0 < jev.incomplete_threshold < 1.0
        assert jev.timeout > 0
