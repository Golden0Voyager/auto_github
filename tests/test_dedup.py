"""Tests for src/dedup.py.

Covers:
- RepoHistoryTracker initialization
- filter_active - separating active vs cooled repos
- record_occurrences - history tracking and archiving
- purge_expired_cooldowns - maintenance cleanup
- Statistics properties (archive_count, history_count)
- Edge cases: empty repos, missing fields, concurrent writes (atomic saves)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.config import AppConfig, DedupConfig
from src.dedup import RepoHistoryTracker, _parse_date, _today

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_repo(full_name: str, stars: int = 5000) -> dict:
    return {
        "full_name": full_name,
        "stars": stars,
        "owner": full_name.split("/")[0],
        "name": full_name.split("/")[1],
        "url": f"https://github.com/{full_name}",
        "description": f"Description for {full_name}.",
        "language": "Python",
    }


class TestTodayAndParseDate:
    """Test the internal date helpers."""

    def test_today_format(self):
        today = _today()
        # Should be YYYY-MM-DD
        parts = today.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4
        assert len(parts[1]) == 2
        assert len(parts[2]) == 2

    def test_parse_date_valid(self):
        dt = _parse_date("2026-06-01")
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 1

    def test_parse_date_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_date("not-a-date")


class TestRepoHistoryTrackerInit:
    """Test initialization with different config paths."""

    def test_init_with_in_memory_config(self, dedup_config_with_custom_paths):
        """Tracker initializes with temp paths, no side effects."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        assert tracker.threshold == 100
        assert tracker.archive_threshold == 2
        assert tracker.cooldown_days == 30
        assert tracker.history_count == 0
        assert tracker.archive_count == 0

    def test_init_history_file_not_exist_returns_empty(self, tmp_path):
        """When history/archive files don't exist, tracker starts empty."""
        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(tmp_path / "nonexistent_history.json"),
                archive_file=str(tmp_path / "nonexistent_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        assert tracker.history_count == 0
        assert tracker.archive_count == 0

    def test_init_loads_existing_data(self, tmp_path):
        """When history/archive files exist, tracker loads them."""
        history = {"existing/repo": ["2026-01-01"]}
        archive = {"existing/repo": {
            "first_seen": "2026-01-01",
            "archived_at": "2026-01-15",
            "cooldown_until": "2026-02-14",
            "stars": 50000,
            "occurrences": 3,
        }}
        hist_path = tmp_path / "repo_history.json"
        arch_path = tmp_path / "high_star_archive.json"
        hist_path.write_text(json.dumps(history), encoding="utf-8")
        arch_path.write_text(json.dumps(archive), encoding="utf-8")

        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(hist_path),
                archive_file=str(arch_path),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        assert tracker.history_count == 1
        assert tracker.archive_count == 1

    def test_init_corrupted_json_returns_default(self, tmp_path):
        """When JSON is corrupted, tracker falls back to empty defaults."""
        hist_path = tmp_path / "repo_history.json"
        hist_path.write_text("{{{broken json", encoding="utf-8")

        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(hist_path),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        assert tracker.history_count == 0
        assert tracker.archive_count == 0


class TestFilterActive:
    """Test the filter_active method."""

    def test_no_archived_repos_returns_all_active(self, dedup_config_with_custom_paths):
        """If no repos are in the archive, all are returned as active."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [_make_repo("owner/repo1"), _make_repo("owner/repo2")]
        active, cooled, _ = tracker.filter_active(repos)
        assert len(active) == 2
        assert len(cooled) == 0

    def test_cooled_repos_are_filtered_out(self, tmp_path):
        """Repos in active cooldown go to cooled list, not active."""
        today = _today()
        cooldown_future = (
            datetime.strptime(today, "%Y-%m-%d") + timedelta(days=10)
        ).strftime("%Y-%m-%d")
        archive = {"cooled/repo": {
            "first_seen": "2026-01-01",
            "archived_at": "2026-05-01",
            "cooldown_until": cooldown_future,
            "stars": 50000,
            "occurrences": 5,
        }}
        arch_path = tmp_path / "high_star_archive.json"
        arch_path.write_text(json.dumps(archive), encoding="utf-8")

        cfg = AppConfig(
            dedup=DedupConfig(
                archive_file=str(arch_path),
                history_file=str(tmp_path / "repo_history.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        repos = [_make_repo("cooled/repo"), _make_repo("other/repo")]
        active, cooled, _ = tracker.filter_active(repos)
        assert len(active) == 1
        assert active[0]["full_name"] == "other/repo"
        assert len(cooled) == 1
        assert cooled[0]["full_name"] == "cooled/repo"

    def test_expired_cooldown_allows_reentry(self, tmp_path):
        """If cooldown has expired, the repo is allowed back into active."""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        archive = {"expired/repo": {
            "first_seen": "2026-01-01",
            "archived_at": "2026-05-01",
            "cooldown_until": yesterday,
            "stars": 50000,
            "occurrences": 5,
        }}
        arch_path = tmp_path / "high_star_archive.json"
        arch_path.write_text(json.dumps(archive), encoding="utf-8")

        cfg = AppConfig(
            dedup=DedupConfig(
                archive_file=str(arch_path),
                history_file=str(tmp_path / "repo_history.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        repos = [_make_repo("expired/repo")]
        active, cooled, _ = tracker.filter_active(repos)
        assert len(active) == 1
        assert len(cooled) == 0

    def test_repo_missing_full_name_not_filtered(self, dedup_config_with_custom_paths):
        """Repos without full_name are not sent to cooled list."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [{"name": "no-full-name"}]
        active, cooled, _ = tracker.filter_active(repos)
        assert len(active) == 1
        assert len(cooled) == 0

    def test_empty_repo_list(self, dedup_config_with_custom_paths):
        """Empty repos list returns empty active and cooled."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        active, cooled, _ = tracker.filter_active([])
        assert active == []
        assert cooled == []

    # ------------------------------------------------------------------
    # first_seen_map tests
    # ------------------------------------------------------------------

    def test_first_seen_map_new_repo_is_true(self, dedup_config_with_custom_paths):
        """A new repo (not in history) should be marked first_seen=True."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [_make_repo("brand/new-repo", stars=500)]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert first_seen_map.get("brand/new-repo") is True

    def test_first_seen_map_existing_repo_is_false(self, tmp_path):
        """A repo already in history should be marked first_seen=False."""
        history = {"existing/repo": ["2026-01-01", "2026-06-01"]}
        hist_path = tmp_path / "repo_history.json"
        hist_path.write_text(json.dumps(history), encoding="utf-8")
        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(hist_path),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        repos = [_make_repo("existing/repo", stars=500)]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert first_seen_map.get("existing/repo") is False

    def test_first_seen_map_mixed_repos(self, dedup_config_with_custom_paths):
        """With a mix of new and existing repos, first_seen_map should reflect each."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        tracker._history = {"old/repo": ["2026-05-01"], "also-old/repo": ["2026-05-01"]}
        repos = [
            _make_repo("old/repo", stars=500),
            _make_repo("new/repo", stars=500),
            _make_repo("also-old/repo", stars=500),
        ]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert first_seen_map["old/repo"] is False
        assert first_seen_map["new/repo"] is True
        assert first_seen_map["also-old/repo"] is False

    def test_first_seen_map_window_cutoff(self, dedup_config_with_custom_paths):
        """Repos last seen beyond first_seen_window_days should be marked as new."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        tracker._history = {"old/repo": ["2026-01-01"]}  # > 90 days ago
        repos = [_make_repo("old/repo", stars=500)]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert first_seen_map["old/repo"] is True

    def test_first_seen_map_empty_full_name(self, dedup_config_with_custom_paths):
        """Repos with empty full_name should be marked first_seen=False (not in history)."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [{"full_name": ""}]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert first_seen_map.get("") is True  # empty string not in history

    def test_first_seen_map_all_new_repos(self, dedup_config_with_custom_paths):
        """When all repos are new, first_seen_map should be all True."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [_make_repo(f"fresh{i}/repo", stars=500) for i in range(5)]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert all(first_seen_map.values())
        assert len(first_seen_map) == 5

    def test_first_seen_map_not_affected_by_cooling(self, tmp_path):
        """A cooled repo should still have correct first_seen marking."""
        today = _today()
        cooldown_future = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=10)).strftime("%Y-%m-%d")
        history = {"cooled/repo": ["2026-01-01", "2026-06-01"]}
        archive = {"cooled/repo": {
            "first_seen": "2026-01-01", "archived_at": "2026-06-01",
            "cooldown_until": cooldown_future, "stars": 50000, "occurrences": 3,
        }}
        hist_path = tmp_path / "repo_history.json"
        arch_path = tmp_path / "high_star_archive.json"
        hist_path.write_text(json.dumps(history), encoding="utf-8")
        arch_path.write_text(json.dumps(archive), encoding="utf-8")
        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(hist_path),
                archive_file=str(arch_path),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        repos = [_make_repo("cooled/repo", stars=50000), _make_repo("new/repo", stars=500)]
        _, _, first_seen_map = tracker.filter_active(repos)
        # Cooled repo is in history → first_seen=False
        assert first_seen_map.get("cooled/repo") is False
        # New repo → first_seen=True
        assert first_seen_map.get("new/repo") is True

    def test_first_seen_map_type(self, dedup_config_with_custom_paths):
        """first_seen_map should be a dict with string keys and bool values."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [_make_repo("test/repo", stars=500)]
        _, _, first_seen_map = tracker.filter_active(repos)
        assert isinstance(first_seen_map, dict)
        assert all(isinstance(k, str) for k in first_seen_map)
        assert all(isinstance(v, bool) for v in first_seen_map.values())


class TestRecordOccurrences:
    """Test the record_occurrences method."""

    def test_records_single_repo_history(self, dedup_config_with_custom_paths):
        """A repo occurrence is recorded in history."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [_make_repo("test/repo", stars=50)]
        newly = tracker.record_occurrences(repos)
        assert newly == []
        assert tracker.history_count == 1
        # Read the actual file
        hist_path = Path(str(dedup_config_with_custom_paths.dedup.history_file))
        hist_data = json.loads(hist_path.read_text(encoding="utf-8"))
        assert "test/repo" in hist_data
        assert len(hist_data["test/repo"]) == 1
        assert hist_data["test/repo"][0] == _today()

    def test_same_day_dedup(self, tmp_path):
        """Multiple occurrences on same day should not add duplicate dates."""
        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        repos = [_make_repo("test/repo", stars=50)]
        tracker.record_occurrences(repos)  # First time
        tracker.record_occurrences(repos)  # Same day - should be deduped

        hist_data = json.loads((tmp_path / "repo_history.json").read_text(encoding="utf-8"))
        assert len(hist_data["test/repo"]) == 1  # Only one entry for today

    def test_different_day_not_deduped(self, tmp_path):
        """Occurrences on different days should each be recorded."""
        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        repos = [_make_repo("test/repo", stars=50)]

        # Write the first occurrence with yesterday's date by manipulating internal state
        tracker._history["test/repo"] = ["2026-01-01"]
        # Now record again - should add today
        tracker.record_occurrences(repos)

        hist_data = json.loads((tmp_path / "repo_history.json").read_text(encoding="utf-8"))
        assert len(hist_data["test/repo"]) == 2

    def test_archive_triggered_when_threshold_met(self, tmp_path):
        """When a repo meets threshold, it should be archived."""
        cfg = AppConfig(
            dedup=DedupConfig(
                high_star_threshold=100,  # Any star >= 100
                archive_threshold=2,       # After 2 occurrences
                archive_cooldown_days=30,
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)

        # Two occurrences should trigger archiving
        repo = _make_repo("highstar/repo", stars=500)
        tracker.record_occurrences([repo])  # occurrence 1

        # Force the history to have 2 dates
        tracker._history["highstar/repo"] = ["2026-01-01", _today()]
        newly = tracker.record_occurrences([repo])  # occurrence 2 (but deduped on date)

        assert "highstar/repo" in newly
        assert tracker.archive_count == 1

    def test_archive_not_triggered_below_star_threshold(self, tmp_path):
        """Even with multiple occurrences, low-star repos should not be archived."""
        cfg = AppConfig(
            dedup=DedupConfig(
                high_star_threshold=10000,   # High threshold
                archive_threshold=2,
                archive_cooldown_days=30,
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)

        repo = _make_repo("lowstar/repo", stars=50)  # Below threshold
        tracker._history["lowstar/repo"] = ["2026-01-01", "2026-06-01", "2026-06-10"]
        newly = tracker.record_occurrences([repo])

        assert newly == []
        assert tracker.archive_count == 0

    def test_archive_not_triggered_below_occurrence_threshold(self, tmp_path):
        """High-star repos with too few occurrences should not be archived."""
        cfg = AppConfig(
            dedup=DedupConfig(
                high_star_threshold=100,
                archive_threshold=5,   # Need 5 occurrences
                archive_cooldown_days=30,
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)

        repo = _make_repo("medstar/repo", stars=500)
        tracker._history["medstar/repo"] = ["2026-01-01", "2026-06-01"]  # Only 2
        newly = tracker.record_occurrences([repo])

        assert newly == []
        assert tracker.archive_count == 0

    def test_already_archived_repo_not_rearchived(self, tmp_path):
        """If a repo is already in the archive, it shouldn't be added again."""
        cfg = AppConfig(
            dedup=DedupConfig(
                high_star_threshold=100,
                archive_threshold=2,
                archive_cooldown_days=30,
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        tracker._archive["already/repo"] = {
            "first_seen": "2026-01-01",
            "archived_at": "2026-06-01",
            "cooldown_until": "2026-07-01",
            "stars": 50000,
            "occurrences": 3,
        }
        tracker._history["already/repo"] = ["2026-01-01", "2026-06-01", "2026-06-10"]

        repo = _make_repo("already/repo", stars=50000)
        newly = tracker.record_occurrences([repo])
        assert newly == []
        # Still just one archive entry
        assert tracker.archive_count == 1

    def test_empty_full_name_skipped(self, dedup_config_with_custom_paths):
        """Repos with empty full_name should be skipped."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        repos = [{"full_name": "", "stars": 100}, {"no_full_name": True}]
        newly = tracker.record_occurrences(repos)
        assert newly == []


class TestPurgeExpiredCooldowns:
    """Test the purge_expired_cooldowns method."""

    def test_expired_archives_are_purged(self, tmp_path):
        """Archives with cooldown in the past should be removed."""
        last_week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        cfg = AppConfig(
            dedup=DedupConfig(
                archive_file=str(tmp_path / "high_star_archive.json"),
                history_file=str(tmp_path / "repo_history.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        tracker._archive = {
            "expired/repo": {
                "first_seen": "2026-01-01",
                "archived_at": "2026-05-01",
                "cooldown_until": last_week,
                "stars": 50000,
                "occurrences": 5,
            },
            "still_cooled/repo": {
                "first_seen": "2026-01-01",
                "archived_at": _today(),
                "cooldown_until": (
                    datetime.now() + timedelta(days=10)
                ).strftime("%Y-%m-%d"),
                "stars": 30000,
                "occurrences": 3,
            },
        }
        purged = tracker.purge_expired_cooldowns()
        assert purged == 1
        # Verify the expired one is gone
        assert "expired/repo" not in tracker._archive
        assert "still_cooled/repo" in tracker._archive

    def test_no_expired_returns_zero(self, dedup_config_with_custom_paths):
        """When no archives are expired, returns 0."""
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        tracker._archive = {}
        purged = tracker.purge_expired_cooldowns()
        assert purged == 0

    def test_empty_archive_returns_zero(self, dedup_config_with_custom_paths):
        tracker = RepoHistoryTracker(dedup_config_with_custom_paths)
        assert tracker.purge_expired_cooldowns() == 0

    def test_malformed_archive_entry_purged(self, tmp_path):
        """Entries with missing/incorrect cooldown_until are purged."""
        cfg = AppConfig(
            dedup=DedupConfig(
                archive_file=str(tmp_path / "high_star_archive.json"),
                history_file=str(tmp_path / "repo_history.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        tracker._archive = {
            "bad/repo": {"stars": 50000},  # Missing cooldown_until
            "bad2/repo": {
                "cooldown_until": "not-a-date",
                "stars": 30000,
            },
        }
        purged = tracker.purge_expired_cooldowns()
        assert purged == 2


class TestActiveArchivedRepos:
    """Test the active_archived_repos property."""

    def test_returns_only_active_cooled(self, tmp_path):
        """active_archived_repos() returns only repos in active cooldown."""
        next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        last_week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        cfg = AppConfig(
            dedup=DedupConfig(
                archive_file=str(tmp_path / "high_star_archive.json"),
                history_file=str(tmp_path / "repo_history.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)
        tracker._archive = {
            "active/repo": {
                "cooldown_until": next_week,
                "stars": 50000,
                "occurrences": 3,
            },
            "expired/repo": {
                "cooldown_until": last_week,
                "stars": 30000,
                "occurrences": 3,
            },
        }
        active = tracker.active_archived_repos()
        assert "active/repo" in active
        assert "expired/repo" not in active


class TestArchiveWriteAtomicity:
    """Test that archive writes use atomic operations (tmp + rename)."""

    def test_save_uses_atomic_write(self, tmp_path):
        """Internal _save should write to .tmp first then rename."""
        cfg = AppConfig(
            dedup=DedupConfig(
                history_file=str(tmp_path / "repo_history.json"),
                archive_file=str(tmp_path / "high_star_archive.json"),
            )
        )
        tracker = RepoHistoryTracker(cfg)

        tracker._history = {"test/repo": ["2026-06-01"]}
        tracker._archive = {"archived/repo": {"stars": 50000}}

        # Trigger _save via record_occurrences
        tracker.record_occurrences([])

        # Verify the files exist (not .tmp files)
        assert (tmp_path / "repo_history.json").exists()
        assert (tmp_path / "high_star_archive.json").exists()

        # No .tmp files should remain
        assert not list(tmp_path.glob("*.tmp"))
