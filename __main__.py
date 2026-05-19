"""CLI entry point for Poke — AI PR Review Agent.

Usage:
    ./poke                  # list open PRs, pick one to review
    ./poke --pr 4128        # review a specific PR
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poke",
        description="Poke — AI PR Review Agent for HBI (gpt-4o + o3-mini critique)",
    )
    parser.add_argument(
        "--pr",
        type=int,
        metavar="NUMBER",
        help="PR number to review directly",
    )
    return parser


def _load_env_file() -> None:
    for search_dir in (Path.cwd(), Path(__file__).parent):
        env_file = search_dir / ".poke.env"
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())
            break


def _ensure_gh_auth() -> None:
    try:
        subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        from rich.console import Console

        Console().print(
            "\n  [red]✗[/red] [bold]gh[/bold] CLI not found.\n"
            "    Install it: [cyan]https://cli.github.com[/cyan]\n"
        )
        sys.exit(1)
    except subprocess.CalledProcessError:
        from rich.console import Console

        Console().print(
            "\n  [yellow]![/yellow] Not logged in to GitHub. Let's fix that.\n"
        )
        result = subprocess.run(["gh", "auth", "login", "-h", "github.com"])
        if result.returncode != 0:
            from rich.console import Console

            Console().print("  [red]✗[/red] Login failed. Try again.\n")
            sys.exit(1)
        from rich.console import Console

        Console().print("  [green]✓[/green] Logged in!\n")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    _load_env_file()
    _ensure_gh_auth()

    if args.pr:
        pr_number, pr_title = args.pr, ""
    else:
        from .pr_selector import prompt_pr_selection, show_pr_table

        human_prs = show_pr_table()
        if not human_prs:
            sys.exit(0)

        result = prompt_pr_selection(human_prs)
        if result is None:
            sys.exit(0)
        pr_number, pr_title = result

    from .main import run_interactive

    run_interactive(pr_number, pr_title)


main()
