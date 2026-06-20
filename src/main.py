#!/usr/bin/env python3
import argparse
import os
import sys

from dotenv import load_dotenv

# Add project root to path to ensure modules are importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config
from src.llm import LLMClient
from src.notifier import ReportNotifier
from src.pipeline import CurationPipeline


def parse_args():
    parser = argparse.ArgumentParser(
        description="🌌 auto_github: GitHub Trend & LLM Giant AI Analysis Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--since",
        choices=["daily", "weekly", "monthly"],
        default="daily",
        help="Analysis timeframe (default: daily)"
    )
    parser.add_argument(
        "--persona",
        choices=["beginner", "intermediate", "advanced"],
        default="intermediate",
        help="Target reader persona (default: intermediate)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run offline using high-fidelity mock data (prevents network and token rate limits)"
    )
    parser.add_argument(
        "--feishu",
        type=str,
        help="Direct Feishu webhook URL (overrides config)"
    )
    parser.add_argument(
        "--slack",
        type=str,
        help="Direct Slack webhook URL (overrides config)"
    )
    parser.add_argument(
        "--discord",
        type=str,
        help="Direct Discord webhook URL (overrides config)"
    )
    return parser.parse_args()

def main():
    # Load .env file if it exists (for local development)
    load_dotenv()

    args = parse_args()

    print("=" * 60)
    print("🌌 auto_github: AI-Generated GitHub Trend Observer")
    print("=" * 60)

    # 1. Load Configurations
    config = load_config()

    # Override webhooks if specified directly via CLI
    if args.feishu:
        config.notifications.feishu_webhook_url = args.feishu
    if args.slack:
        config.notifications.slack_webhook_url = args.slack
    if args.discord:
        config.notifications.discord_webhook_url = args.discord

    print("[Init] Configuration loaded successfully.")
    print(f"[Init] LLM Provider: {config.ai.default_provider.upper()} (API: {'Configured' if any(os.getenv(f'{p.upper()}_API_KEY') for p in ('openrouter', 'sensenova', 'openai')) else 'Missing'})")
    roles = getattr(config.ai, "roles", {})
    print(f"[Init] Classifier: {roles.get('classifier',{}).model if hasattr(roles.get('classifier'),'model') else '?'} | Writer: {roles.get('writer',{}).model if hasattr(roles.get('writer'),'model') else '?'}")
    print(f"[Init] Notification Webhooks: "
          f"Feishu={'Configured' if config.notifications.feishu_webhook_url else 'None'}, "
          f"Slack={'Configured' if config.notifications.slack_webhook_url else 'None'}, "
          f"Discord={'Configured' if config.notifications.discord_webhook_url else 'None'}")

    # 2. Instantiate LLM Client
    llm_client = LLMClient(config)

    # 3. Instantiate and run Pipeline
    pipeline = CurationPipeline(config, llm_client, args.persona)

    try:
        curated_data = pipeline.run(since=args.since, use_mock=args.mock)

        if not curated_data or "reports" not in curated_data:
            print("\n[Error] Pipeline execution completed but produced no report.")
            sys.exit(1)

        # 4. Push notifications
        notifier = ReportNotifier(config)
        notif_results = notifier.notify_all(curated_data["reports"], args.since, llm_stats=llm_client.get_stats())

        print("\n" + "=" * 60)
        print("🎉 Execution Completed Successfully!")
        print("=" * 60)
        print(f"Timeframe: {args.since} | Persona: {args.persona}")
        print(f"Report size: {len(curated_data['repos'])} repositories matching criteria.")

        # Dedup 状态
        meta = curated_data.get("meta", {})
        cooled = meta.get("cooled_repos", []) or []
        newly_archived = meta.get("newly_archived", []) or []
        archive_total = meta.get("archive_total", 0)
        if cooled or newly_archived or archive_total:
            print("Dedup 状态（高🌟项目存档）:")
            print(f"  - 今日过滤（冷却中）: {len(cooled)} 个")
            print(f"  - 本次新晋存档: {len(newly_archived)} 个")
            print(f"  - 存档总数: {archive_total} 个")
            if newly_archived:
                for n in newly_archived:
                    print(f"      🌟 {n}")

        print("Notification Deliveries:")
        for channel, success in notif_results.items():
            status = "✅ Delivered" if success else "❌ Failed"
            print(f"  - {channel.capitalize()}: {status}")

        stats = llm_client.get_stats()
        print("LLM 调用统计:")
        print(f"  - 成功调用: {stats['call_count']} 次")
        if stats["failed_attempt_count"] > 0:
            print(f"  - 失败/重试: {stats['failed_attempt_count']} 次")
        print(
            f"  - Token 消耗: {stats['total_prompt_tokens']:,} input + "
            f"{stats['total_completion_tokens']:,} output = "
            f"{stats['total_tokens']:,} total"
        )
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\n[Fatal Error] Pipeline failed to execute: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
