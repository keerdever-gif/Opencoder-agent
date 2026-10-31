"""OpenCoder-Agent command-line entry point."""
from __future__ import annotations

import argparse
import asyncio
import sys

from config import config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencoder-agent",
        description="Run the OpenCoder AI agent as a Discord bot or interactive CLI.",
    )
    subparsers = parser.add_subparsers(dest="mode")
    subparsers.add_parser("discord", help="Start the Discord bot (default).")
    subparsers.add_parser("cli", help="Start an interactive terminal session.")
    return parser


async def _run_cli() -> int:
    if not config.ai_slots:
        print("Configuration error: set AI_API_KEY or AI_API_KEY_1 in your environment.", file=sys.stderr)
        return 2

    from loop_agent.agent import agent
    from loop_agent.connector import connector
    from sandbox.sandbox import sandbox

    await agent.init()
    print(f"{config.bot_name} CLI is ready. Type /exit or /quit to stop.\n")
    try:
        while True:
            try:
                prompt = await asyncio.to_thread(input, "You> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            prompt = prompt.strip()
            if not prompt:
                continue
            if prompt.lower() in {"/exit", "/quit", "exit", "quit"}:
                break
            try:
                answer, image_url, files = await agent.ask(
                    user_id=0, channel_id=0, prompt=prompt
                )
                if answer:
                    print(f"\n{config.bot_name}> {answer}\n")
                if image_url:
                    print(f"Image: {image_url}")
                if files:
                    for file_path in files:
                        print(f"Generated file: {file_path}")
            except Exception as exc:
                print(f"Agent error: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        await connector.close()
        await agent.close()
        await sandbox.close()
    return 0


def main() -> int:
    args = _parser().parse_args()
    mode = args.mode or "discord"
    if mode == "cli":
        return asyncio.run(_run_cli())

    if not config.discord_token:
        print("Configuration error: set DISCORD_BOT_TOKEN to run Discord mode.", file=sys.stderr)
        return 2
    if not config.ai_slots:
        print("Configuration error: set AI_API_KEY or AI_API_KEY_1.", file=sys.stderr)
        return 2

    from bot.bot import main as run_discord_bot
    asyncio.run(run_discord_bot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
