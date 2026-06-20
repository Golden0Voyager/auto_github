"""Tests for src/notifier.py.

Covers:
- ReportNotifier initialization
- save_locally() with and without llm_stats
- send_feishu() success and failure
- send_slack() success and failure
- send_discord() success, failure, and truncation
- notify_all() orchestration
- _llm_footer_text() generation
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import AppConfig, NotificationConfig
from src.notifier import ReportNotifier


@pytest.fixture
def notifier_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        notifications=NotificationConfig(
            local_report_dir=str(tmp_path),
            feishu_webhook_url="https://feishu.test/hook",
            slack_webhook_url="https://slack.test/hook",
            discord_webhook_url="https://discord.test/hook",
        )
    )


@pytest.fixture
def notifier(notifier_config) -> ReportNotifier:
    return ReportNotifier(notifier_config)


@pytest.fixture
def sample_reports() -> dict[str, Any]:
    return {
        "markdown": "# Test Report\n\nThis is a test report.",
        "feishu": {"msg_type": "interactive", "card": {"body": {"elements": []}}},
        "slack": {"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Test"}}]},
    }


@pytest.fixture
def long_markdown() -> str:
    """Generate a markdown string over 2000 chars."""
    return "# Long Report\n" + "\n".join(f"This is line {i} of the long report for truncation testing." for i in range(100))


class TestReportNotifierInit:
    """Test initialization."""

    def test_init_stores_webhooks(self, notifier_config):
        notifier = ReportNotifier(notifier_config)
        assert notifier.feishu_url == "https://feishu.test/hook"
        assert notifier.slack_url == "https://slack.test/hook"
        assert notifier.discord_url == "https://discord.test/hook"

    def test_init_creates_report_dir(self, tmp_path):
        """Report directory should be created on init."""
        cfg = AppConfig(
            notifications=NotificationConfig(local_report_dir=str(tmp_path / "new_reports"))
        )
        ReportNotifier(cfg)
        assert (tmp_path / "new_reports").exists()

    def test_init_no_webhooks(self, tmp_path):
        """Without webhook URLs, notifier should still work for local saves."""
        cfg = AppConfig(
            notifications=NotificationConfig(local_report_dir=str(tmp_path))
        )
        notifier = ReportNotifier(cfg)
        assert notifier.feishu_url is None
        assert notifier.slack_url is None
        assert notifier.discord_url is None


class TestLLMFooterText:
    """Test the _llm_footer_text helper."""

    def test_footer_en(self, notifier):
        stats = {"call_count": 3, "failed_attempt_count": 1, "total_prompt_tokens": 15000, "total_completion_tokens": 5000, "total_tokens": 20000}
        footer = notifier._llm_footer_text(stats, locale="en")
        assert "3 calls" in footer
        assert "20,000 tokens" in footer
        assert "15,000 in" in footer
        assert "5,000 out" in footer

    def test_footer_zh(self, notifier):
        stats = {"call_count": 3, "failed_attempt_count": 1, "total_prompt_tokens": 15000, "total_completion_tokens": 5000, "total_tokens": 20000}
        footer = notifier._llm_footer_text(stats, locale="zh")
        assert "3 次成功" in footer
        assert "20,000 tokens" in footer

    def test_footer_zero_stats(self, notifier):
        stats = {"call_count": 0, "failed_attempt_count": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0}
        footer = notifier._llm_footer_text(stats, locale="zh")
        assert "0 次成功" in footer


class TestSaveLocally:
    """Test local file saving."""

    def test_saves_markdown_file(self, notifier, tmp_path):
        """Should save markdown to a timestamped file."""
        notifier.report_dir = tmp_path
        success = notifier.save_locally("# Test Report", "daily")
        assert success is True
        files = list(tmp_path.glob("daily_*.md"))
        assert len(files) >= 1

    def test_saves_latest_file(self, notifier, tmp_path):
        """Should also save to latest_daily.md."""
        notifier.report_dir = tmp_path
        notifier.save_locally("# Test Report", "daily")
        latest = tmp_path / "latest_daily.md"
        assert latest.exists()
        assert latest.read_text(encoding="utf-8").strip() == "# Test Report"

    def test_saves_with_llm_stats_footer(self, notifier, tmp_path, mock_llm_stats):
        """When llm_stats is provided, footer should be appended."""
        notifier.report_dir = tmp_path
        notifier.save_locally("# Report", "daily", llm_stats=mock_llm_stats)
        latest = tmp_path / "latest_daily.md"
        content = latest.read_text(encoding="utf-8")
        assert "LLM 调用" in content
        assert "3 次成功" in content

    def test_write_failure_returns_false(self, notifier, tmp_path):
        """When file write fails, should return False."""
        notifier.report_dir = tmp_path
        with patch("builtins.open", side_effect=PermissionError("Denied")):
            result = notifier.save_locally("# Test", "daily")
            assert result is False


class TestSendFeishu:
    """Test Feishu webhook sending."""

    @patch("src.notifier.requests.post")
    def test_successful_send(self, mock_post, notifier):
        """A successful Feishu push should return True."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0}
        mock_post.return_value = mock_response

        result = notifier.send_feishu({"msg_type": "interactive", "card": {}})
        assert result is True

    @patch("src.notifier.requests.post")
    def test_api_error_code(self, mock_post, notifier):
        """When Feishu returns a non-zero code, should return False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 10003, "msg": "Invalid signature"}
        mock_post.return_value = mock_response

        result = notifier.send_feishu({"msg_type": "interactive", "card": {}})
        assert result is False

    @patch("src.notifier.requests.post")
    def test_http_error(self, mock_post, notifier):
        """HTTP errors should return False."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        result = notifier.send_feishu({"msg_type": "interactive", "card": {}})
        assert result is False

    @patch("src.notifier.requests.post")
    def test_exception_returns_false(self, mock_post, notifier):
        """Network exceptions should return False (not crash)."""
        mock_post.side_effect = Exception("Connection timeout")

        result = notifier.send_feishu({"msg_type": "interactive", "card": {}})
        assert result is False

    @patch("src.notifier.requests.post")
    def test_includes_llm_stats(self, mock_post, notifier, mock_llm_stats):
        """When llm_stats is provided, footer should be injected."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": 0}
        mock_post.return_value = mock_response

        payload = {"msg_type": "interactive", "card": {"body": {"elements": [{"tag": "markdown", "content": "Hello"}]}}}
        result = notifier.send_feishu(payload, llm_stats=mock_llm_stats)
        assert result is True
        # Verify the payload was modified to include footer
        sent_payload = mock_post.call_args[1]["json"]
        elements = sent_payload["card"]["body"]["elements"]
        assert len(elements) > 1  # Original + hr + footer


class TestSendSlack:
    """Test Slack webhook sending."""

    @patch("src.notifier.requests.post")
    def test_successful_send(self, mock_post, notifier):
        """A successful Slack push should return True."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_post.return_value = mock_response

        result = notifier.send_slack({"blocks": []})
        assert result is True

    @patch("src.notifier.requests.post")
    def test_http_error(self, mock_post, notifier):
        """HTTP errors should return False."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response

        result = notifier.send_slack({"blocks": []})
        assert result is False

    @patch("src.notifier.requests.post")
    def test_exception_returns_false(self, mock_post, notifier):
        """Network exceptions should return False."""
        mock_post.side_effect = Exception("Timeout")

        result = notifier.send_slack({"blocks": []})
        assert result is False

    @patch("src.notifier.requests.post")
    def test_includes_llm_stats(self, mock_post, notifier, mock_llm_stats):
        """When llm_stats is provided, context block should be added."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"
        mock_post.return_value = mock_response

        payload = {"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}]}
        result = notifier.send_slack(payload, llm_stats=mock_llm_stats)
        assert result is True

        sent_payload = mock_post.call_args[1]["json"]
        blocks = sent_payload["blocks"]
        # Should have original block + context footer
        assert len(blocks) >= 2
        assert blocks[-1]["type"] == "context"


class TestSendDiscord:
    """Test Discord webhook sending."""

    @patch("src.notifier.requests.post")
    def test_successful_send(self, mock_post, notifier):
        """A successful Discord push should return True."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        result = notifier.send_discord("Hello from test")
        assert result is True

    @patch("src.notifier.requests.post")
    def test_http_error(self, mock_post, notifier):
        """HTTP errors should return False."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate Limited"
        mock_post.return_value = mock_response

        result = notifier.send_discord("Hello")
        assert result is False

    @patch("src.notifier.requests.post")
    def test_exception_returns_false(self, mock_post, notifier):
        """Network exceptions should return False."""
        mock_post.side_effect = Exception("Connection error")

        result = notifier.send_discord("Hello")
        assert result is False

    @patch("src.notifier.requests.post")
    def test_truncates_long_content(self, mock_post, notifier, long_markdown):
        """Content over 1950 chars should be truncated to 1900."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        result = notifier.send_discord(long_markdown)
        assert result is True

        sent_content = mock_post.call_args[1]["json"]["content"]
        # The content should be truncated (shorter than original)
        assert len(sent_content) < len(long_markdown)
        assert "(Truncated" in sent_content

    @patch("src.notifier.requests.post")
    def test_short_content_not_truncated(self, mock_post, notifier):
        """Short content should not be truncated."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        result = notifier.send_discord("Short content")
        assert result is True

        sent_content = mock_post.call_args[1]["json"]["content"]
        assert "Truncated" not in sent_content

    @patch("src.notifier.requests.post")
    def test_includes_llm_stats(self, mock_post, notifier, mock_llm_stats):
        """When llm_stats is provided, footer should be appended."""
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_post.return_value = mock_response

        result = notifier.send_discord("Content", llm_stats=mock_llm_stats)
        assert result is True

        sent_content = mock_post.call_args[1]["json"]["content"]
        assert "LLM 调用" in sent_content


class TestNotifyAll:
    """Test the full notify_all orchestration."""

    def test_local_save_always_active(self, notifier, sample_reports):
        """Local save should always be attempted."""
        results = notifier.notify_all(sample_reports, "daily")
        assert "local" in results
        assert results["local"] is True

    def test_all_channels_attempted(self, notifier, sample_reports):
        """All configured channels should be attempted."""
        results = notifier.notify_all(sample_reports, "daily")
        assert "feishu" in results
        assert "slack" in results
        assert "discord" in results

    def test_skips_unconfigured_channels(self, tmp_path):
        """Channels without webhooks should be skipped (not crash)."""
        cfg = AppConfig(
            notifications=NotificationConfig(local_report_dir=str(tmp_path))
        )
        notifier = ReportNotifier(cfg)
        results = notifier.notify_all(
            {"markdown": "# Test", "feishu": {}, "slack": {}, "discord": {}},
            "daily",
        )
        # Only local should be present
        assert "local" in results
        assert "feishu" not in results


class TestEdgeCases:
    """Test edge cases."""

    def test_empty_markdown_content(self, notifier, tmp_path):
        """Empty markdown should be saved successfully."""
        notifier.report_dir = tmp_path
        success = notifier.save_locally("", "daily")
        assert success is True
        assert (tmp_path / "latest_daily.md").exists()

    def test_notify_all_with_llm_stats(self, notifier, sample_reports, mock_llm_stats):
        """notify_all should pass llm_stats to each channel."""
        with patch.multiple(
            notifier,
            send_feishu=MagicMock(return_value=True),
            send_slack=MagicMock(return_value=True),
            send_discord=MagicMock(return_value=True),
        ):
            notifier.notify_all(sample_reports, "daily", llm_stats=mock_llm_stats)
            assert notifier.send_feishu.call_count == 1
            assert notifier.send_slack.call_count == 1
            assert notifier.send_discord.call_count == 1
            # llm_stats is passed as positional arg (2nd arg), not keyword arg
            feishu_args = notifier.send_feishu.call_args[0]
            assert feishu_args[1] == mock_llm_stats
