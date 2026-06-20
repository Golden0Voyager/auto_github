"""Tests for src/main.py.

Covers:
- Argument parsing for all CLI options
- main() with --mock flag (runs pipeline in mock mode)
- Error handling when pipeline fails
- Webhook URL overrides from CLI
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# Prevent main from executing on import
import src.main as main_mod


class TestParseArgs:
    """Test CLI argument parsing."""

    def test_default_args(self):
        """Default args should be daily timeframe, intermediate persona, no mock."""
        test_args = ["prog"]
        with patch.object(sys, "argv", test_args):
            args = main_mod.parse_args()
        assert args.since == "daily"
        assert args.persona == "intermediate"
        assert args.mock is False
        assert args.feishu is None
        assert args.slack is None
        assert args.discord is None

    @pytest.mark.parametrize("since_val", ["daily", "weekly", "monthly"])
    def test_since_choices(self, since_val):
        """--since should accept daily, weekly, monthly."""
        test_args = ["prog", "--since", since_val]
        with patch.object(sys, "argv", test_args):
            args = main_mod.parse_args()
        assert args.since == since_val

    @pytest.mark.parametrize("persona_val", ["beginner", "intermediate", "advanced"])
    def test_persona_choices(self, persona_val):
        """--persona should accept beginner, intermediate, advanced."""
        test_args = ["prog", "--persona", persona_val]
        with patch.object(sys, "argv", test_args):
            args = main_mod.parse_args()
        assert args.persona == persona_val

    def test_mock_flag(self):
        """--mock should set mock flag to True."""
        test_args = ["prog", "--mock"]
        with patch.object(sys, "argv", test_args):
            args = main_mod.parse_args()
        assert args.mock is True

    def test_webhook_overrides(self):
        """CLI webhook args should override config."""
        test_args = [
            "prog",
            "--feishu", "https://feishu.test/cli",
            "--slack", "https://slack.test/cli",
            "--discord", "https://discord.test/cli",
        ]
        with patch.object(sys, "argv", test_args):
            args = main_mod.parse_args()
        assert args.feishu == "https://feishu.test/cli"
        assert args.slack == "https://slack.test/cli"
        assert args.discord == "https://discord.test/cli"


class TestMainFunction:
    """Test the main() function with mocked dependencies."""

    @patch("src.main.CurationPipeline")
    @patch("src.main.LLMClient")
    @patch("src.main.load_config")
    @patch("src.main.ReportNotifier")
    def test_main_mock_run(self, mock_notifier_cls, mock_load_config,
                            mock_llm_cls, mock_pipeline_cls):
        """main() with --mock should run pipeline and exit 0."""
        # Configures
        mock_config = MagicMock()
        mock_config.notifications.feishu_webhook_url = None
        mock_config.notifications.slack_webhook_url = None
        mock_config.notifications.discord_webhook_url = None
        mock_load_config.return_value = mock_config

        # LLM
        mock_llm = MagicMock()
        mock_llm.get_stats.return_value = {
            "call_count": 3, "failed_attempt_count": 0,
            "total_prompt_tokens": 100, "total_completion_tokens": 50, "total_tokens": 150,
        }
        mock_llm_cls.return_value = mock_llm

        # Pipeline
        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = {
            "meta": {"cooled_repos": [], "newly_archived": [], "archive_total": 0},
            "repos": [{"full_name": "test/repo"}],
            "reports": {"markdown": "# Report", "feishu": {}, "slack": {}},
        }
        mock_pipeline_cls.return_value = mock_pipeline

        # Notifier
        mock_notifier = MagicMock()
        mock_notifier.notify_all.return_value = {"local": True}
        mock_notifier_cls.return_value = mock_notifier

        test_args = ["prog", "--mock"]
        with patch.object(sys, "argv", test_args):
            main_mod.main()

        mock_pipeline.run.assert_called_once_with(since="daily", use_mock=True)
        mock_notifier.notify_all.assert_called_once()

    @patch("src.main.CurationPipeline")
    @patch("src.main.LLMClient")
    @patch("src.main.load_config")
    def test_main_pipeline_error_exits(self, mock_load_config,
                                        mock_llm_cls, mock_pipeline_cls):
        """When pipeline returns empty result, should sys.exit(1)."""
        mock_config = MagicMock()
        mock_config.notifications.feishu_webhook_url = None
        mock_config.notifications.slack_webhook_url = None
        mock_config.notifications.discord_webhook_url = None
        mock_load_config.return_value = mock_config

        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm

        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = {}  # No reports key
        mock_pipeline_cls.return_value = mock_pipeline

        test_args = ["prog", "--mock"]
        with patch.object(sys, "argv", test_args), pytest.raises(SystemExit) as exc_info:
            main_mod.main()
        assert exc_info.value.code == 1

    @patch("src.main.CurationPipeline")
    @patch("src.main.LLMClient")
    @patch("src.main.load_config")
    def test_main_exception_exits(self, mock_load_config,
                                   mock_llm_cls, mock_pipeline_cls):
        """When pipeline raises exception, should sys.exit(1)."""
        mock_config = MagicMock()
        mock_config.notifications.feishu_webhook_url = None
        mock_config.notifications.slack_webhook_url = None
        mock_config.notifications.discord_webhook_url = None
        mock_load_config.return_value = mock_config

        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm

        mock_pipeline = MagicMock()
        mock_pipeline.run.side_effect = RuntimeError("Test crash")
        mock_pipeline_cls.return_value = mock_pipeline

        test_args = ["prog", "--mock"]
        with patch.object(sys, "argv", test_args), pytest.raises(SystemExit) as exc_info:
            main_mod.main()
        assert exc_info.value.code == 1

    @patch("src.main.CurationPipeline")
    @patch("src.main.LLMClient")
    @patch("src.main.load_config")
    def test_webhook_cli_overrides(self, mock_load_config,
                                    mock_llm_cls, mock_pipeline_cls):
        """CLI webhook args should override config values."""
        mock_config = MagicMock()
        mock_config.notifications.feishu_webhook_url = "original/feishu"
        mock_config.notifications.slack_webhook_url = None
        mock_config.notifications.discord_webhook_url = None
        mock_load_config.return_value = mock_config

        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm
        mock_llm.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }

        mock_pipeline = MagicMock()
        mock_pipeline.run.return_value = {
            "meta": {}, "repos": [],
            "reports": {
                "markdown": "",
                "feishu": {"msg_type": "interactive", "card": {"body": {"elements": []}}},
                "slack": {"blocks": []},
            },
        }
        mock_pipeline_cls.return_value = mock_pipeline

        # Mock Notifier to avoid actual HTTP calls
        with patch("src.main.ReportNotifier") as mock_notifier_cls:
            mock_notifier = MagicMock()
            mock_notifier.notify_all.return_value = {"local": True, "feishu": True}
            mock_notifier_cls.return_value = mock_notifier

            test_args = ["prog", "--mock", "--feishu", "new/feishu"]
            with patch.object(sys, "argv", test_args):
                main_mod.main()

        # Config should have been overridden
        assert mock_config.notifications.feishu_webhook_url == "new/feishu"
