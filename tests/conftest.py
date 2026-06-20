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
def dedup_config() -> AppConfig:
    """Config with small thresholds so dedup triggers quickly in tests."""
    return AppConfig(
        dedup=DedupConfig(
            high_star_threshold=100,     # low for testing
            archive_threshold=2,          # archive after 2 occurrences
            archive_cooldown_days=30,
            history_file="reports/test_repo_history.json",
            archive_file="reports/test_high_star_archive.json",
        )
    )


@pytest.fixture
def dedup_config_with_custom_paths(tmp_path: Path) -> AppConfig:
    """Config with temporary file paths for dedup tests so we don't pollute reports/."""
    return AppConfig(
        dedup=DedupConfig(
            high_star_threshold=100,
            archive_threshold=2,
            archive_cooldown_days=30,
            history_file=str(tmp_path / "repo_history.json"),
            archive_file=str(tmp_path / "high_star_archive.json"),
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
def pipeline_config() -> AppConfig:
    """A config pre-configured for pipeline tests."""
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
            api_key="test-key",
            base_url="https://api.openai.com/v1",
        ),
        stage2_pre_filter=Stage2PreFilterConfig(
            enabled=True,
            max_repos=88,
        ),
        notifications=NotificationConfig(
            local_report_dir="./reports",
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
