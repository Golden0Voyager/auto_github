import json
import time
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from src.config import AppConfig

# Base Directory
BASE_DIR = Path(__file__).resolve().parent.parent

PROVIDER_FOOTER = "Multi-model curation: SenseNova (classify) + OpenRouter (write/review) + SiliconFlow (translate A/B)."

TIMEFRAME_LABELS_ZH = {"daily": "日报", "weekly": "周报", "monthly": "月报"}

# 看板分组的真实语义 = bucket allocation 的三桶,不是趋势榜的时间窗
BUCKET_SECTIONS = (
    ("early_bird", "🌱 Early Bird 早鸟新星", "星标 <3k 或 90 天内首次出现的潜力项目"),
    ("high_star", "🔥 High-Star Hot 高星热点", "星标 ≥10k 或本期新增 ≥500 的爆热项目"),
    ("deep_dive", "🔍 Deep Dive 技术深潜", "架构/底层价值优先的项目"),
)


class ReportFormatter:
    """Renders analyzed repositories into aesthetic Markdown, Feishu, and Slack reports."""

    def __init__(self, config: AppConfig, persona: dict[str, Any], timeframe: str):
        self.config = config
        self.persona = persona
        self.timeframe = timeframe
        self.timestamp = time.strftime("%Y-%m-%d %H:%M")

        # Initialize Jinja2 environment
        template_dir = BASE_DIR / "templates"
        if template_dir.exists():
            # Output is Markdown / JSON payloads, never HTML, so autoescape stays off.
            # trim_blocks/lstrip_blocks: 模板里的 {% %} 控制行不该在报告里留下空行
            self.env: Environment | None = Environment(
                loader=FileSystemLoader(str(template_dir)),
                autoescape=False,  # nosec B701
                trim_blocks=True,
                lstrip_blocks=True,
            )
        else:
            self.env = None
            print("[Formatter Warning] Templates directory not found. Using fallbacks.")

    def generate_all(self, repos: list[dict[str, Any]], cooled_repos: list[dict[str, Any]] | None = None,
                     archive_total: int = 0, candidate_total: int = 0) -> dict[str, Any]:
        """Generates all report formats.

        Args:
            repos: 经策展的项目列表。
            cooled_repos: 今日因高🌟存档而被过滤掉的项目（仅展示用，不进入策展管线）。
            archive_total: 当前高🌟项目存档总数。
            candidate_total: 本轮实际抓到的候选项目数（报告里的分母）。
        """
        total_input = candidate_total or self.config.github.max_trending_repos
        cooled_repos = cooled_repos or []

        enriched_repos = []
        for r in repos:
            rc = dict(r)
            desc = (r.get("description") or "").strip()
            short_desc = desc[:80] + ("…" if len(desc) > 80 else "")
            short_desc = short_desc.replace("|", "\\|").replace("\n", " ")
            rc["description_short"] = short_desc
            # Ensure all keys that templates expect have defaults
            rc.setdefault("period_stars", "")
            rc.setdefault("tags", [])
            rc.setdefault("rating", "B")
            rc.setdefault("chinese_summary", "")
            rc.setdefault("refined_summary", "")
            rc.setdefault("selection_reason", "")
            rc.setdefault("language", "Unknown")
            rc.setdefault("tds", "S")
            # 分桶引擎关闭时（走 _prefilter_top_n）没有 _bucket，看板会全空
            rc.setdefault("_bucket", "deep_dive")
            enriched_repos.append(rc)

        context = {
            "repos": enriched_repos,
            "persona": self.persona,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "total_input": total_input,
            "cooled_repos": cooled_repos,
            "archive_total": archive_total,
            "provider_footer": PROVIDER_FOOTER,
            "timeframe_label": TIMEFRAME_LABELS_ZH.get(self.timeframe, "报告"),
            "bucket_sections": BUCKET_SECTIONS,
        }

        markdown_report = self._render_template("report.md.j2", context, self._fallback_markdown(enriched_repos))
        feishu_payload = self._build_feishu_collapsible_payload(
            enriched_repos, cooled_repos=cooled_repos, archive_total=archive_total, candidate_total=total_input,
        )
        slack_payload = self._render_json_template("slack_blocks.json.j2", context, self._fallback_slack(enriched_repos))

        return {
            "markdown": markdown_report,
            "feishu": feishu_payload,
            "slack": slack_payload
        }

    def _build_feishu_collapsible_payload(self, repos: list[dict[str, Any]],
                                          cooled_repos: list[dict[str, Any]] | None = None,
                                          archive_total: int = 0, candidate_total: int = 0) -> dict[str, Any]:
        """Build Feishu Card JSON 2.0 with collapsible panels for each repo."""
        cooled_repos = cooled_repos or []
        total_input = candidate_total or self.config.github.max_trending_repos
        color = "purple" if self.timeframe == "monthly" else "orange" if self.timeframe == "weekly" else "blue"

        overview = (
            f"**🎯 目标画像**: {self.persona['name']} | **时间**: {self.timestamp}\n"
            f"已从 {total_input} 个候选项目中智能甄选 **{len(repos)}** 个最值得关注的项目。\n\n"
            "👇 点击下方项目面板即可在飞书内展开阅读全文。"
        )
        elements: list[dict[str, Any]] = [{"tag": "markdown", "content": overview}]

        # 高🌟项目存档状态（透明披露本次过滤与累计存档数）
        if cooled_repos or archive_total:
            cooled_names = "、".join(r["full_name"] for r in cooled_repos[:8])
            if len(cooled_repos) > 8:
                cooled_names += f" 等 {len(cooled_repos)} 个"
            archive_note = (
                f"🌟 **高🌟项目存档**: 今日 {len(cooled_repos)} 个高星项目处于阶梯冷却期"
                f"（{cooled_names or '无待冷却项目'}）；累计存档 {archive_total} 个。"
            )
            elements.append({"tag": "markdown", "content": archive_note})
            elements.append({"tag": "hr"})

        for r in repos:
            rating = r.get("rating", "B")
            rating_emoji = "👑" if rating == "S" else "🔥" if rating == "A" else "🔹"
            panel_title = f"{rating_emoji} [{rating}级] {r['full_name']}"

            if r.get("period_stars"):
                panel_title += f" | ⭐️ +{r['period_stars']}"
            elif r.get("stars"):
                panel_title += f" | ⭐️ {r['stars']}"

            tags = " ".join(f"`{t}`" for t in r.get("tags", [])) or "无"
            language = r.get("language") or "未知"
            stars_display = f"+{r['period_stars']} this {self.timeframe}" if r.get("period_stars") else f"{r.get('stars', 0)}"
            raw_desc = (r.get("description") or "").strip()

            content_lines = [
                f"**📝 简介**: {raw_desc[:150]}{'…' if len(raw_desc) > 150 else ''}" if raw_desc else "",
                f"**🏷️ 标签**: {tags} | **语言**: `{language}` | **深度**: {r.get('tds', 'S')} | **总星标**: ⭐️ {stars_display}",
                "",
                f"*{r.get('selection_reason', '')}*",
                "",
                r.get("chinese_summary", ""),
            ]
            content = "\n".join(content_lines)

            panel: dict[str, Any] = {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {"tag": "plain_text", "content": panel_title},
                    "icon": {
                        "tag": "standard_icon",
                        "token": "down-small-ccm_outlined",  # nosec B105 - Feishu 图标名,不是凭据
                        "size": "16px 16px",
                    },
                    "icon_position": "right",
                    "icon_expanded_angle": -180,
                },
                "border": {"color": "grey", "corner_radius": "5px"},
                "elements": [
                    {"tag": "markdown", "content": content},
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔗 查看仓库"},
                        "type": "primary",
                        "url": r["url"],
                    },
                ],
            }
            elements.append(panel)

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "markdown",
            "content": f"🤖 {PROVIDER_FOOTER}",
        })

        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "config": {
                    "wide_screen_mode": True,
                    "update_multi": True,
                    "enable_forward": True,
                },
                "header": {
                    "template": color,
                    "title": {
                        "tag": "plain_text",
                        "content": f"🌌 GitHub Trend & Activity ({self.persona['name']}) - {self.timeframe.capitalize()}",
                    },
                },
                "body": {
                    "elements": elements,
                },
            },
        }

    def _render_template(self, template_name: str, context: dict[str, Any], fallback: str) -> str:
        """Renders a Jinja2 template with fallback support."""
        if not self.env:
            return fallback

        try:
            template = self.env.get_template(template_name)
            return template.render(**context)
        except Exception as e:
            print(f"[Formatter Warning] Failed to render {template_name}: {e}. Using fallback.")
            return fallback

    def _render_json_template(self, template_name: str, context: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        """Renders a Jinja2 template and parses it as a JSON object, with fallback support."""
        if not self.env:
            return fallback

        try:
            template = self.env.get_template(template_name)
            rendered = template.render(**context)
            # Remove any trailing commas that J2 loops might have generated
            # and clean up whitespaces
            return json.loads(rendered)
        except Exception as e:
            print(f"[Formatter Warning] Failed to render or parse JSON {template_name}: {e}. Using fallback.")
            return fallback

    def _fallback_markdown(self, repos: list[dict[str, Any]]) -> str:
        """Generates a simple, robust fallback markdown string if Jinja rendering fails."""
        lines = [
            f"# 🌌 GitHub 开源趋势 & LLM 大厂动态{TIMEFRAME_LABELS_ZH.get(self.timeframe, '报告')} (Fallback)",
            f"> **画像**: {self.persona['name']} | **时间**: {self.timestamp}\n",
            "---",
            "## 📊 精选列表\n"
        ]

        for r in repos:
            lines.append(f"### [{r.get('rating', 'B')}] {r['full_name']}")
            lines.append(f"- **URL**: {r['url']}")
            lines.append(f"- **Tags**: {', '.join(r.get('tags', []))}")
            lines.append(f"- **Description**: {(r.get('description') or '')[:200]}\n")
            lines.append(r.get('chinese_summary', r.get('refined_summary', '')))
            lines.append("\n---\n")

        lines.append("\n*Generated by auto_github fallback system.*")
        return "\n".join(lines)

    def _fallback_slack(self, repos: list[dict[str, Any]]) -> dict[str, Any]:
        """Generates a basic fallback Slack message payload."""
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"GitHub Trend Report ({self.persona['name']})",
                    "emoji": True
                }
            }
        ]
        for r in repos[:5]:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*<{r['url']}|{r['full_name']}>* | Rating: `{r.get('rating', 'B')}`\n{r.get('chinese_summary', '')[:300]}..."
                }
            })
        return {"blocks": blocks}
