"""Shared fixtures and test utilities for auto_github tests."""

# Ensure the project root is on sys.path (same as main.py does)
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import AIConfig, AppConfig, DedupConfig, GitHubConfig, NotificationConfig, Stage2PreFilterConfig

# ---------------------------------------------------------------------------
# Fixtures: Config
# ---------------------------------------------------------------------------

@pytest.fixture
def base_config() -> AppConfig:
    """Returns a minimal AppConfig with defaults suitable for testing."""
    return AppConfig()


@pytest.fixture
def sample_repos() -> list[dict[str, Any]]:
    """A diverse list of mock repository dicts used across many tests."""
    return [
        {
            "owner": "deepseek-ai",
            "name": "DeepSeek-V3",
            "full_name": "deepseek-ai/DeepSeek-V3",
            "url": "https://github.com/deepseek-ai/DeepSeek-V3",
            "description": "A strong MoE language model with MLA.",
            "language": "Python",
            "stars": 15200,
            "forks": 1200,
            "period_stars": "850 stars today",
            "source": "trending",
            "timeframe": "daily",
        },
        {
            "owner": "deepseek-ai",
            "name": "DeepSeek-R1",
            "full_name": "deepseek-ai/DeepSeek-R1",
            "url": "https://github.com/deepseek-ai/DeepSeek-R1",
            "description": "Incentive reasoning with large-scale RL.",
            "language": "Python",
            "stars": 48200,
            "forks": 5100,
            "period_stars": "2300 stars today",
            "source": "trending",
            "timeframe": "daily",
        },
        {
            "owner": "lowstars",
            "name": "tiny-tool",
            "full_name": "lowstars/tiny-tool",
            "url": "https://github.com/lowstars/tiny-tool",
            "description": "A tiny CLI utility for developers.",
            "language": "Rust",
            "stars": 50,
            "forks": 5,
            "period_stars": "",
            "source": "trending",
            "timeframe": "daily",
        },
        {
            "owner": "mega-corp",
            "name": "megatron",
            "full_name": "mega-corp/megatron",
            "url": "https://github.com/mega-corp/megatron",
            "description": "Large-scale transformer training framework.",
            "language": "Python",
            "stars": 35000,
            "forks": 4500,
            "period_stars": "500 stars this week",
            "source": "llm_giant",
            "timeframe": "recent_activity",
        },
        {
            "owner": "new-kid",
            "name": "fresh-project",
            "full_name": "new-kid/fresh-project",
            "url": "https://github.com/new-kid/fresh-project",
            "description": "An innovative new approach to vector search.",
            "language": "Go",
            "stars": 800,
            "forks": 40,
            "period_stars": "200 stars today",
            "source": "trending",
            "timeframe": "daily",
        },
    ]


@pytest.fixture
def dedup_config(tmp_path: Path) -> AppConfig:
    """Config with small thresholds so dedup triggers quickly in tests.

    路径必须落在 tmp_path：这些文件是系统的「记忆」，写进仓库里的 reports/
    会让一次 pytest 覆盖掉真实的去重历史。
    """
    return AppConfig(
        dedup=isolated_dedup_config(
            tmp_path,
            high_star_threshold=100,
            archive_threshold=2,
            archive_cooldown_days=30,
        )
    )


def isolated_dedup_config(tmp_path: Path, **overrides: Any) -> DedupConfig:
    """DedupConfig 指向 tmp_path，测试永远碰不到 reports/ 里的真实记忆。"""
    base: dict[str, Any] = {
        "history_file": str(tmp_path / "repo_history.json"),
        "archive_file": str(tmp_path / "high_star_archive.json"),
        "cycles_file": str(tmp_path / "repo_cycles.json"),
    }
    base.update(overrides)
    return DedupConfig(**base)


@pytest.fixture
def dedup_config_with_custom_paths(tmp_path: Path) -> AppConfig:
    """Config with temporary file paths for dedup tests so we don't pollute reports/."""
    return AppConfig(
        dedup=isolated_dedup_config(
            tmp_path,
            high_star_threshold=100,
            archive_threshold=2,
            archive_cooldown_days=30,
        )
    )


@pytest.fixture
def sample_analyzed_repos() -> list[dict[str, Any]]:
    """Repos after Stage 2 analysis (have rating, tags, selection_reason)."""
    return [
        {
            "full_name": "deepseek-ai/DeepSeek-V3",
            "url": "https://github.com/deepseek-ai/DeepSeek-V3",
            "description": "A strong MoE language model with MLA.",
            "language": "Python",
            "stars": 15200,
            "rating": "S",
            "tags": ["#MoE", "#MLA", "#LLM"],
            "selection_reason": "Innovative MoE architecture.",
            "period_stars": "850 stars today",
            "source": "trending",
        },
        {
            "full_name": "deepseek-ai/DeepSeek-R1",
            "url": "https://github.com/deepseek-ai/DeepSeek-R1",
            "description": "Incentive reasoning with large-scale RL.",
            "language": "Python",
            "stars": 48200,
            "rating": "S",
            "tags": ["#Reasoning", "#RL"],
            "selection_reason": "Breakthrough in RL reasoning.",
            "period_stars": "2300 stars today",
            "source": "trending",
        },
        {
            "full_name": "lowstars/tiny-tool",
            "url": "https://github.com/lowstars/tiny-tool",
            "description": "A tiny CLI utility.",
            "language": "Rust",
            "stars": 50,
            "rating": "B",
            "tags": ["#CLI", "#DevTools"],
            "selection_reason": "Useful CLI utility.",
            "period_stars": "",
            "source": "trending",
        },
    ]


@pytest.fixture
def pipeline_config(tmp_path: Path) -> AppConfig:
    """A config pre-configured for pipeline tests.

    dedup 状态文件与报告输出目录都指向 tmp_path，见 reports_guard 说明。
    """
    return AppConfig(
        github=GitHubConfig(
            max_trending_repos=15,
            max_org_repos=5,
        ),
        ai=AIConfig(
            default_provider="openai",
            temperature=0.3,
            max_tokens=4096,
            rate_limit_delay=0.1,
        ),
        dedup=isolated_dedup_config(tmp_path),
        stage2_pre_filter=Stage2PreFilterConfig(
            enabled=True,
            max_repos=88,
        ),
        notifications=NotificationConfig(
            local_report_dir=str(tmp_path / "reports"),
        ),
    )


@pytest.fixture
def notifier_config(tmp_path: Path) -> AppConfig:
    """Config with a temp directory for local reports in notifier tests."""
    return AppConfig(
        notifications=NotificationConfig(
            local_report_dir=str(tmp_path),
        )
    )


@pytest.fixture
def mock_llm_stats() -> dict[str, int]:
    return {
        "call_count": 3,
        "failed_attempt_count": 1,
        "total_prompt_tokens": 15000,
        "total_completion_tokens": 5000,
        "total_tokens": 20000,
    }


# ---------------------------------------------------------------------------
# Guard: reports/ 下的状态文件是系统的「记忆」，任何用例都不该写它们
# ---------------------------------------------------------------------------

STATE_FILES = ("repo_history.json", "high_star_archive.json", "repo_cycles.json")


@pytest.fixture(autouse=True, scope="session")
def reports_guard():
    """测试结束后逐字节比对 reports/ 状态文件，被改过就直接失败。

    历史事故：用例用默认路径构造 AppConfig，一次 pytest 就把真实去重历史
    覆盖成测试里的假日期，而线上管线依赖这些文件的内容。
    """
    reports_dir = Path(__file__).resolve().parent.parent / "reports"
    snapshot = {
        name: (reports_dir / name).read_bytes()
        for name in STATE_FILES
        if (reports_dir / name).exists()
    }
    yield
    current = {
        name: (reports_dir / name).read_bytes()
        for name in STATE_FILES
        if (reports_dir / name).exists()
    }
    changed = sorted(n for n in set(snapshot) | set(current) if snapshot.get(n) != current.get(n))
    if changed:
        pytest.fail(
            f"tests mutated live state files: {', '.join(changed)}. "
            "用 isolated_dedup_config(tmp_path) / tmp_path 路径隔离。"
        )
