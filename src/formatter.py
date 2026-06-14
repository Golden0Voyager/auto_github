import time
from pathlib import Path
from typing import List, Dict, Any
from jinja2 import Environment, FileSystemLoader
from src.config import AppConfig

# Base Directory
BASE_DIR = Path(__file__).resolve().parent.parent

class ReportFormatter:
    """Renders analyzed repositories into aesthetic Markdown, Feishu, and Slack reports."""
    
    def __init__(self, config: AppConfig, persona: Dict[str, Any], timeframe: str):
        self.config = config
        self.persona = persona
        self.timeframe = timeframe
        self.timestamp = time.strftime("%Y-%m-%d %H:%M")
        
        # Initialize Jinja2 environment
        template_dir = BASE_DIR / "templates"
        if template_dir.exists():
            self.env = Environment(loader=FileSystemLoader(str(template_dir)))
        else:
            self.env = None
            print("[Formatter Warning] Templates directory not found. Using fallbacks.")

    def generate_all(self, repos: List[Dict[str, Any]], cooled_repos: List[Dict[str, Any]] = None, archive_total: int = 0) -> Dict[str, Any]:
        """Generates all report formats.

        Args:
            repos: 经策展的项目列表。
            cooled_repos: 今日因高🌟存档而被过滤掉的项目（仅展示用，不进入策展管线）。
            archive_total: 当前高🌟项目存档总数。
        """
        total_input = self.config.github.max_trending_repos
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
            enriched_repos.append(rc)

        context = {
            "repos": enriched_repos,
            "persona": self.persona,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "total_input": total_input,
            "model_v3": self.config.ai.model_v3,
            "model_r1": self.config.ai.model_r1,
            "cooled_repos": cooled_repos,
            "archive_total": archive_total,
        }

        markdown_report = self._render_template("report.md.j2", context, self._fallback_markdown(repos))
        feishu_payload = self._build_feishu_collapsible_payload(repos, cooled_repos, archive_total)
        slack_payload = self._render_json_template("slack_blocks.json.j2", context, self._fallback_slack(repos))

        return {
            "markdown": markdown_report,
            "feishu": feishu_payload,
            "slack": slack_payload
        }

    def _build_feishu_collapsible_payload(self, repos: List[Dict[str, Any]], cooled_repos: List[Dict[str, Any]] = None, archive_total: int = 0) -> Dict[str, Any]:
        """Build Feishu Card JSON 2.0 with collapsible panels for each repo."""
        cooled_repos = cooled_repos or []
        color = "purple" if self.timeframe == "monthly" else "orange" if self.timeframe == "weekly" else "blue"

        overview = (
            f"**🎯 目标画像**: {self.persona['name']} | **时间**: {self.timestamp}\n"
            f"已从 {self.config.github.max_trending_repos} 个热门候选项目中智能甄选 **{len(repos)}** 个最值得关注的项目。\n\n"
            "👇 点击下方项目面板即可在飞书内展开阅读全文。"
        )
        elements: List[Dict[str, Any]] = [{"tag": "markdown", "content": overview}]

        # 高🌟项目存档状态（透明披露本次过滤与累计存档数）
        if cooled_repos or archive_total:
            cooled_names = "、".join(r["full_name"] for r in cooled_repos[:8])
            if len(cooled_repos) > 8:
                cooled_names += f" 等 {len(cooled_repos)} 个"
            archive_note = (
                f"🌟 **高🌟项目存档**: 今日 {len(cooled_repos)} 个高星项目处于 30 天冷却期"
                f"（{cooled_names or '无'}）；累计存档 {archive_total} 个。"
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
            desc_line = ""
            if raw_desc:
                truncated = raw_desc[:150] + ("…" if len(raw_desc) > 150 else "")
                desc_line = f"**📝 简介**: {truncated}"

            content_lines = [
                desc_line,
                f"**🏷️ 标签**: {tags} | **语言**: `{language}` | **总星标**: ⭐️ {stars_display}",
                "",
                f"*{r.get('selection_reason', '')}*",
                "",
                r.get("chinese_summary", ""),
            ]
            content = "\n".join(line for line in content_lines if line is not None)

            panel: Dict[str, Any] = {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {"tag": "plain_text", "content": panel_title},
                    "icon": {
                        "tag": "standard_icon",
                        "token": "down-small-ccm_outlined",
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
            "content": f"⚡ Powered by OpenRouter ({self.config.ai.model_v3} & {self.config.ai.model_r1}). Compressed 85% noise.",
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

    def _render_template(self, template_name: str, context: Dict[str, Any], fallback: str) -> str:
        """Renders a Jinja2 template with fallback support."""
        if not self.env:
            return fallback
            
        try:
            template = self.env.get_template(template_name)
            return template.render(**context)
        except Exception as e:
            print(f"[Formatter Warning] Failed to render {template_name}: {e}. Using fallback.")
            return fallback

    def _render_json_template(self, template_name: str, context: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
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

    def _fallback_markdown(self, repos: List[Dict[str, Any]]) -> str:
        """Generates a simple, robust fallback markdown string if Jinja rendering fails."""
        lines = [
            f"# 🌌 GitHub 开源趋势 & LLM 大厂动态周报 (Fallback)",
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

    def _fallback_feishu(self, repos: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generates a basic fallback Feishu Interactive Card message."""
        elements = [
            {
                "tag": "markdown",
                "content": f"**🌌 GitHub Trend Report ({self.persona['name']})**\nTimestamp: {self.timestamp}"
            },
            {"tag": "hr"}
        ]
        
        for r in repos[:5]: # Top 5 to fit Feishu card limits
            elements.append({
                "tag": "div",
                "text": {
                  "tag": "lark_md",
                  "content": f"**[{r.get('rating', 'B')}] {r['full_name']}**\nTags: {', '.join(r.get('tags', []))}\n\n{r.get('chinese_summary', '')[:500]}"
                },
                "extra": {
                  "tag": "button",
                  "text": {"tag": "plain_text", "content": "Open GitHub"},
                  "type": "primary",
                  "url": r["url"]
                }
            })
            elements.append({"tag": "hr"})
            
        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": "GitHub Trending Fallback"}
                },
                "elements": elements
            }
        }

    def _fallback_slack(self, repos: List[Dict[str, Any]]) -> Dict[str, Any]:
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

# Ensure json is imported
import json
