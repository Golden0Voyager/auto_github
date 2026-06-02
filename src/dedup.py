"""高星项目去重与存档追踪器。

设计目标：
1. 节省 LLM 算力：重复推送同一个老牌高星项目无新增信息。
2. 留展位给新兴项目：把固化的高星项目挪出日报，腾出策展位给新面孔。

数据模型（两个 JSON 文件，存于 reports/）：

  reports/repo_history.json
    {
      "owner/repo": ["2026-05-01", "2026-05-15", "2026-06-02"],
      ...
    }
    —— 每次日报抓到该项目时追加当天日期（同日去重）。

  reports/high_star_archive.json
    {
      "owner/repo": {
        "first_seen": "2026-04-01",
        "archived_at": "2026-06-02",
        "cooldown_until": "2026-07-02",
        "stars": 15200,
        "occurrences": 5
      },
      ...
    }
    —— 高星项目出现次数 ≥ archive_threshold 后被永久存档，
       30 天内（cooldown_until 之前）不再出现在日报中。
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set, Tuple

from src.config import AppConfig


def _today() -> str:
    """返回 YYYY-MM-DD 形式的当前日期（本地时区）。"""
    return datetime.now().strftime("%Y-%m-%d")


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


class RepoHistoryTracker:
    """高星项目追踪：去重 + 存档 + 30 天冷却。

    典型用法（在 pipeline 中）：

        tracker = RepoHistoryTracker(config)
        # 1. 过滤掉仍在冷却期内的存档项目
        filtered = tracker.filter_active(raw_repos)
        # 2. 跑完 6-stage 后，把本次出现的项目计入历史
        archived_now = tracker.record_occurrences(filtered)
    """

    def __init__(self, config: AppConfig):
        self.threshold = config.dedup.high_star_threshold
        self.archive_threshold = config.dedup.archive_threshold
        self.cooldown_days = config.dedup.archive_cooldown_days
        # 历史/存档文件相对 BASE_DIR
        from src.config import BASE_DIR
        self.history_path = BASE_DIR / config.dedup.history_file
        self.archive_path = BASE_DIR / config.dedup.archive_file
        self._history: Dict[str, List[str]] = self._load_json(self.history_path, default={})
        self._archive: Dict[str, Dict] = self._load_json(self.archive_path, default={})

    @staticmethod
    def _load_json(path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save(self) -> None:
        """原子写入历史与存档（先写临时文件再 rename，避免半写状态）。"""
        for path, data in (
            (self.history_path, self._history),
            (self.archive_path, self._archive),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)

    # ------------------------------------------------------------------
    # 查询 API
    # ------------------------------------------------------------------

    def _cooldown_active(self, full_name: str) -> bool:
        """判断一个存档项目是否仍在冷却期内。"""
        info = self._archive.get(full_name)
        if not info:
            return False
        try:
            until = _parse_date(info["cooldown_until"])
        except (KeyError, ValueError):
            return False
        return _parse_date(_today()) < until

    def filter_active(self, repos: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """从原始抓取结果中过滤掉仍在冷却期内的存档项目。

        Returns:
            (active_repos, cooled_repos) —— active 用于进入策展管线；
            cooled 仅用于日志/统计。
        """
        active: List[Dict] = []
        cooled: List[Dict] = []
        for r in repos:
            name = r.get("full_name", "")
            if self._cooldown_active(name):
                cooled.append(r)
            else:
                active.append(r)
        return active, cooled

    # ------------------------------------------------------------------
    # 写入 API
    # ------------------------------------------------------------------

    def record_occurrences(self, repos: List[Dict]) -> List[str]:
        """记录本次抓取中所有项目的出现日期，并晋升满足条件的高星项目到存档。

        Returns:
            本次新进入存档的项目名（full_name）列表。
        """
        today = _today()
        newly_archived: List[str] = []

        for r in repos:
            name = r.get("full_name", "")
            if not name:
                continue
            stars = r.get("stars", 0) or 0
            # 更新历史日期列表（同日去重）
            history = self._history.setdefault(name, [])
            if not history or history[-1] != today:
                history.append(today)

            # 已经在存档中：跳过（冷却期由 cooldown_until 控制）
            if name in self._archive:
                continue

            # 仅当 star ≥ 阈值 且 累计出现次数 ≥ 阈值 时才晋升
            if (
                stars >= self.threshold
                and len(history) >= self.archive_threshold
            ):
                first_seen = history[0]
                cooldown_until = (
                    _parse_date(today) + timedelta(days=self.cooldown_days)
                ).strftime("%Y-%m-%d")
                self._archive[name] = {
                    "first_seen": first_seen,
                    "archived_at": today,
                    "cooldown_until": cooldown_until,
                    "stars": stars,
                    "occurrences": len(history),
                }
                newly_archived.append(name)

        self._save()
        return newly_archived

    # ------------------------------------------------------------------
    # 维护 API
    # ------------------------------------------------------------------

    def purge_expired_cooldowns(self) -> int:
        """清理过期的存档项目（cooldown_until < 今天）。

        过期项目允许重新进入策展管线（如果它们再次进入 trending 列表）。
        Returns:
            被清理的存档项数量。
        """
        today = _parse_date(_today())
        expired = []
        for name, info in self._archive.items():
            try:
                until = _parse_date(info["cooldown_until"])
            except (KeyError, ValueError):
                expired.append(name)
                continue
            if until < today:
                expired.append(name)
        for name in expired:
            del self._archive[name]
        if expired:
            self._save()
        return len(expired)

    # ------------------------------------------------------------------
    # 统计 API
    # ------------------------------------------------------------------

    @property
    def archive_count(self) -> int:
        return len(self._archive)

    @property
    def history_count(self) -> int:
        return len(self._history)

    def active_archived_repos(self) -> List[str]:
        """返回当前所有仍在冷却期内的项目名（用于报告状态输出）。"""
        return [n for n in self._archive if self._cooldown_active(n)]
