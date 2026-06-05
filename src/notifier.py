import os
import time
import requests
from pathlib import Path
from typing import Dict, Any, Optional
from src.config import AppConfig

class ReportNotifier:
    """Delivers reports to configured channels (Feishu, Slack, Discord, Local Files)."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.feishu_url = config.notifications.feishu_webhook_url
        self.slack_url = config.notifications.slack_webhook_url
        self.discord_url = config.notifications.discord_webhook_url
        
        # Ensure local report output directory exists
        self.report_dir = Path(config.notifications.local_report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def notify_all(self, reports: Dict[str, Any], timeframe: str, llm_stats: Optional[Dict[str, int]] = None) -> Dict[str, bool]:
        """Pushes reports to all active channels and logs the outcomes.

        Args:
            reports: dict with keys 'markdown', 'feishu', 'slack', 'discord'
            timeframe: 'daily' | 'weekly' | 'monthly'
            llm_stats: optional dict from LLMClient.get_stats(); if provided,
                each channel gets a small footer with call count + token usage.
        """
        results = {}

        # 1. Save locally (Always active)
        local_success = self.save_locally(reports["markdown"], timeframe, llm_stats)
        results["local"] = local_success

        # 2. Push to Feishu if configured
        if self.feishu_url:
            feishu_success = self.send_feishu(reports["feishu"], llm_stats)
            results["feishu"] = feishu_success
        else:
            print("[Notify] Feishu Webhook is not configured. Skipping.")

        # 3. Push to Slack if configured
        if self.slack_url:
            slack_success = self.send_slack(reports["slack"], llm_stats)
            results["slack"] = slack_success
        else:
            print("[Notify] Slack Webhook is not configured. Skipping.")

        # 4. Push to Discord if configured
        if self.discord_url:
            discord_success = self.send_discord(reports["markdown"], llm_stats)
            results["discord"] = discord_success
        else:
            print("[Notify] Discord Webhook is not configured. Skipping.")

        return results

    def _llm_footer_text(self, stats: Dict[str, int], locale: str) -> str:
        if locale == "en":
            return (
                f"🤖 LLM: {stats['call_count']} calls · "
                f"{stats['total_tokens']:,} tokens "
                f"({stats['total_prompt_tokens']:,} in / {stats['total_completion_tokens']:,} out)"
            )
        return (
            f"🤖 LLM 调用: {stats['call_count']} 次成功 / "
            f"{stats['failed_attempt_count']} 重试失败 · "
            f"{stats['total_tokens']:,} tokens "
            f"({stats['total_prompt_tokens']:,} in / {stats['total_completion_tokens']:,} out)"
        )

    def save_locally(self, markdown_content: str, timeframe: str, llm_stats: Optional[Dict[str, int]] = None) -> bool:
        """Saves the markdown report into the local reports folder."""
        date_str = time.strftime("%Y-%m-%d_%H%M")
        filename = f"{timeframe}_{date_str}.md"
        filepath = self.report_dir / filename

        if llm_stats:
            markdown_content = markdown_content.rstrip() + "\n\n---\n\n" + self._llm_footer_text(llm_stats, locale="zh") + "\n"

        try:
            print(f"[Notify] Saving markdown report locally to {filepath}...")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            # Also create a 'latest.md' symlink/copy for easy access
            latest_path = self.report_dir / f"latest_{timeframe}.md"
            with open(latest_path, "w", encoding="utf-8") as f:
                f.write(markdown_content)

            print(f"[Notify] Local report successfully written. Latest: {latest_path}")
            return True
        except Exception as e:
            print(f"[Notify Error] Failed to write local report: {e}")
            return False

    def send_feishu(self, payload: Dict[str, Any], llm_stats: Optional[Dict[str, int]] = None) -> bool:
        """Sends interactive card to Feishu webhook."""
        if llm_stats:
            payload = {
                **payload,
                "card": {
                    **payload["card"],
                    "elements": list(payload["card"]["elements"]) + [
                        {"tag": "hr"},
                        {
                            "tag": "note",
                            "elements": [
                                {"tag": "plain_text", "content": self._llm_footer_text(llm_stats, locale="zh")}
                            ]
                        }
                    ]
                }
            }
        print("[Notify] Sending interactive card to Feishu Webhook...")
        try:
            response = requests.post(self.feishu_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if response.status_code in (200, 201):
                res_data = response.json()
                if res_data.get("code") == 0 or res_data.get("StatusCode") == 0:
                    print("[Notify] Feishu push successful.")
                    return True
                else:
                    print(f"[Notify Warning] Feishu rejected payload: {res_data}")
                    return False
            else:
                print(f"[Notify Warning] Feishu HTTP error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"[Notify Error] Exception sending to Feishu: {e}")
            return False

    def send_slack(self, payload: Dict[str, Any], llm_stats: Optional[Dict[str, int]] = None) -> bool:
        """Sends Block Kit message to Slack webhook."""
        if llm_stats:
            payload = {
                **payload,
                "blocks": list(payload["blocks"]) + [
                    {
                        "type": "context",
                        "elements": [
                            {"type": "mrkdwn", "text": self._llm_footer_text(llm_stats, locale="en")}
                        ]
                    }
                ]
            }
        print("[Notify] Sending Block Kit to Slack Webhook...")
        try:
            response = requests.post(self.slack_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if response.status_code in (200, 201, 204) or response.text.strip() == "ok":
                print("[Notify] Slack push successful.")
                return True
            else:
                print(f"[Notify Warning] Slack HTTP error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"[Notify Error] Exception sending to Slack: {e}")
            return False

    def send_discord(self, markdown_content: str, llm_stats: Optional[Dict[str, int]] = None) -> bool:
        """Sends markdown message to Discord webhook."""
        print("[Notify] Sending markdown report to Discord Webhook...")
        if llm_stats:
            markdown_content = markdown_content.rstrip() + "\n\n---\n\n" + self._llm_footer_text(llm_stats, locale="zh")
        # Discord content has a max length limit of 2000 characters
        # If it's too long, we truncate it gracefully with a notice.
        content = markdown_content
        if len(content) > 1950:
            content = content[:1900] + "\n\n...(Truncated due to Discord character limits. See local reports for full text.)"
            
        payload = {
            "content": content
        }
        
        try:
            response = requests.post(self.discord_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if response.status_code in (200, 201, 204):
                print("[Notify] Discord push successful.")
                return True
            else:
                print(f"[Notify Warning] Discord HTTP error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"[Notify Error] Exception sending to Discord: {e}")
            return False
