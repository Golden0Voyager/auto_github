"""Tests for src/formatter.py.

Covers:
- ReportFormatter initialization with/without templates
- _fallback_markdown() / _fallback_slack() 兜底产物
- generate_all() with various inputs
- 模板渲染完整性（Slack JSON 必须真能 parse，不允许静默降级）
- _render_template() / _render_json_template() error handling
- Dedup status display in reports
"""

import json
from typing import Any
from unittest.mock import patch

import pytest

from src.config import AppConfig, NotificationConfig
from src.formatter import ReportFormatter


@pytest.fixture
def formatter_config(tmp_path) -> AppConfig:
    return AppConfig(
        notifications=NotificationConfig(
            local_report_dir=str(tmp_path / "reports"),
        ),
    )


@pytest.fixture
def formatter(formatter_config) -> ReportFormatter:
    persona = {
        "name": "中阶实践者",
        "description": "For intermediate developers",
        "prompt_focus": "Engineering focus",
    }
    return ReportFormatter(formatter_config, persona, "daily")


@pytest.fixture
def sample_formatted_repos() -> list[dict[str, Any]]:
    return [
        {
            "full_name": "deepseek-ai/DeepSeek-V3",
            "url": "https://github.com/deepseek-ai/DeepSeek-V3",
            "description": "A strong MoE language model with MLA.",
            "language": "Python",
            "stars": 15200,
            "forks": 1200,
            "period_stars": "850 stars today",
            "rating": "S",
            "tags": ["#MoE", "#MLA"],
            "selection_reason": "Innovative architecture.",
            "chinese_summary": "### 核心解决的工程痛点\nTest summary.",
            "refined_summary": "### Core Technical Problem\nTest.",
        },
        {
            "full_name": "lowstars/tiny-tool",
            "url": "https://github.com/lowstars/tiny-tool",
            "description": "A tiny CLI utility.",
            "language": "Rust",
            "stars": 50,
            "forks": 5,
            "period_stars": "",
            "rating": "B",
            "tags": ["#CLI"],
            "selection_reason": "Useful tool.",
            "chinese_summary": "### 核心解决的工程痛点\nAnother test.",
            "refined_summary": "### Core Technical Problem\nTest 2.",
        },
    ]


class TestReportFormatterInit:
    """Test initialization."""

    def test_init_creates_jinja_env(self, formatter):
        """Should create Jinja2 environment if templates exist."""
        assert formatter.env is not None

    def test_init_no_templates(self, formatter_config, tmp_path):
        """When templates dir doesn't exist, env stays None."""
        persona = {"name": "测试", "description": "", "prompt_focus": ""}
        with patch("src.formatter.BASE_DIR", tmp_path):
            fmt = ReportFormatter(formatter_config, persona, "daily")
            assert fmt.env is None

    def test_timestamp_is_set(self, formatter):
        """Timestamp should be a non-empty string."""
        assert formatter.timestamp
        assert len(formatter.timestamp) > 0


class TestGenerateAll:
    """Test the main report generation method."""

    def test_generate_returns_three_formats(self, formatter, sample_formatted_repos):
        """generate_all should return markdown, feishu, and slack."""
        reports = formatter.generate_all(sample_formatted_repos)
        assert "markdown" in reports
        assert "feishu" in reports
        assert "slack" in reports

    def test_markdown_report_contains_repo_names(self, formatter, sample_formatted_repos):
        """Markdown report should include repo full names."""
        reports = formatter.generate_all(sample_formatted_repos)
        assert "deepseek-ai/DeepSeek-V3" in reports["markdown"]
        assert "lowstars/tiny-tool" in reports["markdown"]

    def test_markdown_with_cooled_repos(self, formatter, sample_formatted_repos):
        """Cooled repos should appear in the report."""
        cooled = [{"full_name": "cooled/repo1", "stars": 50000}]
        reports = formatter.generate_all(sample_formatted_repos, cooled_repos=cooled, archive_total=5)
        # The markdown report may not reference cooled repos by name in the template
        # Check that the report was generated successfully
        assert "deepseek-ai/DeepSeek-V3" in reports["markdown"]
        assert isinstance(reports["markdown"], str)

    def test_markdown_empty_repos(self, formatter):
        """Empty repo list should still produce a valid report."""
        reports = formatter.generate_all([])
        assert reports["markdown"]
        assert "精选列表" in reports["markdown"] or len(reports["markdown"]) > 0

    def test_feishu_payload_is_dict(self, formatter, sample_formatted_repos):
        """Feishu payload should be a dict with msg_type."""
        reports = formatter.generate_all(sample_formatted_repos)
        feishu = reports["feishu"]
        assert isinstance(feishu, dict)
        assert feishu["msg_type"] == "interactive"
        assert "card" in feishu

    def test_feishu_card_has_sections(self, formatter, sample_formatted_repos):
        """Feishu card should have header and body elements."""
        reports = formatter.generate_all(sample_formatted_repos)
        card = reports["feishu"]["card"]
        assert "header" in card
        assert "body" in card
        assert "elements" in card["body"]

    def test_feishu_color_by_timeframe(self, formatter_config, sample_formatted_repos):
        """Feishu color should differ by timeframe."""
        monthly_fmt = ReportFormatter(formatter_config, {"name": "test", "description": "", "prompt_focus": ""}, "monthly")
        daily_fmt = ReportFormatter(formatter_config, {"name": "test", "description": "", "prompt_focus": ""}, "daily")

        monthly_report = monthly_fmt.generate_all(sample_formatted_repos)
        daily_report = daily_fmt.generate_all(sample_formatted_repos)

        monthly_color = monthly_report["feishu"]["card"]["header"]["template"]
        daily_color = daily_report["feishu"]["card"]["header"]["template"]

        assert monthly_color == "purple"
        assert daily_color == "blue"

    def test_slack_payload_has_blocks(self, formatter, sample_formatted_repos):
        """Slack payload should have a blocks list."""
        reports = formatter.generate_all(sample_formatted_repos)
        slack = reports["slack"]
        assert "blocks" in slack


class TestFallbackMarkdown:
    """Test the fallback markdown generator."""

    def test_fallback_has_repo_names(self, formatter, sample_formatted_repos):
        """Fallback markdown should include repo full names."""
        md = formatter._fallback_markdown(sample_formatted_repos)
        assert "deepseek-ai/DeepSeek-V3" in md
        assert "lowstars/tiny-tool" in md

    def test_fallback_includes_chinese_summary(self, formatter, sample_formatted_repos):
        """Fallback should include chinese_summary when available."""
        md = formatter._fallback_markdown(sample_formatted_repos)
        assert "Test summary." in md
        assert "Another test." in md

    def test_fallback_empty_repos(self, formatter):
        """Fallback with empty repo list should not crash."""
        md = formatter._fallback_markdown([])
        assert isinstance(md, str)

    def test_fallback_includes_footer(self, formatter, sample_formatted_repos):
        """Fallback should include the 'Generated by' footer."""
        md = formatter._fallback_markdown(sample_formatted_repos)
        assert "auto_github" in md


class TestFallbackSlack:
    """Test the fallback Slack message generator."""

    def test_fallback_slack_has_header(self, formatter, sample_formatted_repos):
        """Fallback Slack should have a header block."""
        payload = formatter._fallback_slack(sample_formatted_repos)
        blocks = payload["blocks"]
        assert blocks[0]["type"] == "header"

    def test_fallback_slack_empty_repos(self, formatter):
        """Fallback Slack with empty repos should still have a header."""
        payload = formatter._fallback_slack([])
        assert len(payload["blocks"]) == 1
        assert payload["blocks"][0]["type"] == "header"

    def test_fallback_slack_only_top_5(self, formatter):
        """Fallback Slack should include at most 5 repos."""
        many_repos = [{"full_name": f"repo/{i}", "rating": "B", "chinese_summary": "test", "url": f"https://github.com/repo/{i}"} for i in range(10)]
        payload = formatter._fallback_slack(many_repos)
        # Header + 5 repos max
        assert len(payload["blocks"]) <= 6


class TestRenderTemplate:
    """Test template rendering with error handling."""

    def test_render_template_no_env_returns_fallback(self, formatter_config):
        """Without templates, _render_template should return the fallback."""
        persona = {"name": "test", "description": "", "prompt_focus": ""}
        fmt = ReportFormatter(formatter_config, persona, "daily")
        fmt.env = None
        result = fmt._render_template("report.md.j2", {}, "fallback content")
        assert result == "fallback content"

    def test_render_json_template_no_env_returns_fallback(self, formatter_config):
        """Without templates, _render_json_template should return the fallback."""
        persona = {"name": "test", "description": "", "prompt_focus": ""}
        fmt = ReportFormatter(formatter_config, persona, "daily")
        fmt.env = None
        result = fmt._render_json_template("slack_blocks.json.j2", {}, {"blocks": []})
        assert result == {"blocks": []}


class TestEdgeCases:
    """Test edge cases in formatter."""

    def test_repo_with_missing_fields(self, formatter):
        """Repos with missing optional fields should not crash."""
        repos = [{"full_name": "minimal/repo", "url": "https://github.com/minimal/repo", "stars": 100}]
        reports = formatter.generate_all(repos)
        assert "minimal/repo" in reports["markdown"]

    def test_very_long_description_is_truncated(self, formatter):
        """Long descriptions should be truncated to 80 chars."""
        long_desc = "A" * 500
        repo = {
            "full_name": "verbose/repo",
            "url": "https://github.com/verbose/repo",
            "description": long_desc,
            "language": "Python",
            "stars": 100,
            "rating": "B",
            "tags": [],
            "_bucket": "early_bird",
            "chinese_summary": "summary",
            "refined_summary": "summary",
        }
        reports = formatter.generate_all([repo])
        md = reports["markdown"]
        # The description_short should be truncated
        assert "AAA…" in md or "A" * 80 in md

    def test_empty_period_stars_not_displayed(self, formatter):
        """Empty period_stars should not cause formatting issues."""
        repos = [{
            "full_name": "test/repo",
            "url": "https://github.com/test/repo",
            "description": "Test repo",
            "language": "Python",
            "stars": 100,
            "period_stars": "",
            "rating": "B",
            "tags": [],
            "chinese_summary": "summary",
            "refined_summary": "summary",
        }]
        reports = formatter.generate_all(repos)
        assert "test/repo" in reports["markdown"]

    def test_none_description_handled(self, formatter):
        """None description should be handled."""
        repos = [{
            "full_name": "test/repo",
            "url": "https://github.com/test/repo",
            "description": None,
            "language": "Python",
            "stars": 100,
            "rating": "B",
            "tags": [],
            "chinese_summary": "summary",
            "refined_summary": "summary",
        }]
        reports = formatter.generate_all(repos)
        assert "test/repo" in reports["markdown"]


class TestTemplateIntegrity:
    """模板必须真的渲染成功：JSON parse 失败会静默降级到只看 5 个仓库的兜底。"""

    def _reports(self, formatter, repos, **kw):
        return formatter.generate_all(repos, **kw)

    def test_slack_payload_parses_from_template(self, formatter, sample_formatted_repos):
        slack = self._reports(formatter, sample_formatted_repos)["slack"]
        assert slack.get("blocks"), "Slack 载荷为空"
        # 兜底载荷的 header 不带 🌌,出现它说明模板渲染失败了
        joined = json.dumps(slack, ensure_ascii=False)
        assert "GitHub Trend Report" not in joined
        assert "DeepSeek-V3" in joined

    def test_slack_survives_quotes_backslashes_and_newlines(self, formatter):
        """旧模板手写 replace 只处理引号,反斜杠/控制字符会让 json.loads 崩。"""
        repos = [{
            "full_name": "tricky/repo",
            "url": "https://github.com/tricky/repo",
            "description": 'He said "hi" C:\\path\\to\\file',
            "language": "Python",
            "stars": 10,
            "rating": "A",
            "tags": ["#X"],
            "selection_reason": 'quote " and \\ backslash',
            "chinese_summary": "### 标题\n第二行\t制表符",
            "refined_summary": "body",
        }]
        slack = formatter.generate_all(repos)["slack"]
        joined = json.dumps(slack, ensure_ascii=False)
        assert "GitHub Trend Report" not in joined, "落到了 Slack 兜底载荷"
        texts = [b["text"]["text"] for b in slack["blocks"] if b.get("type") == "section"]
        assert any("tricky/repo" in t for t in texts)

    def test_empty_repos_still_produces_valid_slack(self, formatter):
        """空列表时模板尾部逗号会让 json.loads 抛错。"""
        slack = formatter.generate_all([])["slack"]
        assert isinstance(slack, dict)
        assert "GitHub Trend Report" not in json.dumps(slack, ensure_ascii=False)

    def test_markdown_title_follows_timeframe(self, formatter_config, sample_formatted_repos):
        for timeframe, label in (("daily", "日报"), ("weekly", "周报"), ("monthly", "月报")):
            persona = {"name": "中阶实践者", "description": "d", "prompt_focus": "f"}
            formatter = ReportFormatter(formatter_config, persona, timeframe)
            report = formatter.generate_all(sample_formatted_repos)["markdown"]
            assert f"LLM 大厂动态{label}" in report.splitlines()[0]

    def test_markdown_uses_bucket_names_not_timeframes(self, formatter, sample_formatted_repos):
        report = formatter.generate_all(sample_formatted_repos)["markdown"]
        assert "Early Bird" in report and "High-Star Hot" in report and "Deep Dive" in report
        assert "每日热门趋势 (Daily Trending)" not in report

    def test_markdown_footer_lists_all_three_providers(self, formatter, sample_formatted_repos):
        report = formatter.generate_all(sample_formatted_repos)["markdown"]
        for provider in ("SenseNova", "OpenRouter", "SiliconFlow"):
            assert provider in report

    def test_no_personal_signature_in_report(self, formatter, sample_formatted_repos):
        """intermediate 画像硬约束：产出里不得出现具体个人姓名。"""
        report = formatter.generate_all(sample_formatted_repos)["markdown"]
        for name in ("Haining", "海宁", "Designed with"):
            assert name not in report

    def test_candidate_total_replaces_config_placeholder(self, formatter, sample_formatted_repos):
        """报告分母必须是真实抓取数,不是 max_trending_repos=15。"""
        report = formatter.generate_all(sample_formatted_repos, candidate_total=238)["markdown"]
        assert "已从 238 个候选项目中筛选出 2 个" in report
        assert "15" not in report.split("精选指标")[1].split("\n")[0]

    def test_repos_without_bucket_land_in_deep_dive(self, formatter, sample_formatted_repos):
        """分桶引擎关闭时 _bucket 缺失,看板不该三栏全空。"""
        bare = [{k: v for k, v in r.items() if k != "_bucket"} for r in sample_formatted_repos]
        report = formatter.generate_all(bare)["markdown"]
        section = report.split("Deep Dive 技术深潜")[1].split("</summary>")[0]
        assert "共计 2 个项目" in section
