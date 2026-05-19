"""Fetch open PRs and display an interactive selection table."""

from __future__ import annotations

import json
import subprocess

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .config import ROUTE_PATTERNS

console = Console()

BOT_PREFIXES = ("app/", "dependabot", "renovate", "red-hat-konflux", "github-actions")
ALLOWED_BOTS = {"bugkiller-agent", "app/bugkiller-agent"}


def fetch_open_prs() -> list[dict]:
    result = subprocess.run(
        [
            "gh", "pr", "list",
            "--state", "open",
            "--limit", "30",
            "--json", "number,title,author,changedFiles,files,isDraft",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _is_bot(pr: dict) -> bool:
    login = pr.get("author", {}).get("login", "")
    if login in ALLOWED_BOTS or any(login.startswith(a) for a in ALLOWED_BOTS):
        return False
    return any(login.startswith(p) or login.endswith("[bot]") for p in BOT_PREFIXES)


def _classify_lenses(pr: dict) -> list[str]:
    """Predict which review lenses would trigger based on file paths."""
    lenses: set[str] = set()
    has_reviewable = False

    for f in pr.get("files", []):
        path = f.get("path", "")
        if not path.endswith((".py", ".yaml", ".yml", ".json")):
            continue
        has_reviewable = True
        for lens_name, patterns in ROUTE_PATTERNS.items():
            if any(p in path for p in patterns):
                lenses.add(lens_name.capitalize())

    if has_reviewable:
        lenses.add("Security")

    order = ["Migration", "Auth", "Kafka", "Api", "Test", "Security"]
    return [l for l in order if l in lenses]


def _fetch_pr_title(pr_number: int) -> str:
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title", "--jq", ".title"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def show_pr_table() -> list[dict]:
    """Fetch open PRs and display them in a rich table.

    Returns the list of human (non-bot, non-draft) PRs that were shown.
    """
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]Poke[/bold cyan] [dim]—[/dim] AI Review Agent",
            border_style="cyan",
        )
    )
    console.print()

    with console.status("[bold]Fetching open PRs…", spinner="dots"):
        all_prs = fetch_open_prs()

    human_prs = [pr for pr in all_prs if not pr.get("isDraft") and not _is_bot(pr)]
    bot_count = len(all_prs) - len(human_prs)

    if not human_prs:
        console.print("  [yellow]No reviewable PRs found.[/yellow]")
        return []

    table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        title=f"[bold]{len(human_prs)}[/bold] reviewable PRs",
        caption=f"[dim]{bot_count} bot/draft PRs hidden[/dim]" if bot_count else None,
        expand=True,
    )
    table.add_column("PR", style="cyan bold", width=6, justify="right")
    table.add_column("Title", no_wrap=True, ratio=3)
    table.add_column("Author", style="green", width=14)
    table.add_column("Files", justify="center", width=5)
    table.add_column("Lenses", style="yellow", no_wrap=True, ratio=2)

    for pr in human_prs:
        lenses = _classify_lenses(pr)
        lens_str = ", ".join(lenses) if lenses else "[dim]—[/dim]"
        table.add_row(
            str(pr["number"]),
            pr["title"],
            pr["author"]["login"][:14],
            str(pr.get("changedFiles", "?")),
            lens_str,
        )

    console.print(table)
    console.print()
    return human_prs


def prompt_pr_selection(human_prs: list[dict]) -> tuple[int, str] | None:
    """Ask the user to pick a PR from the displayed list.

    Returns (pr_number, pr_title) or None if the user quits.
    """
    choice = Prompt.ask(
        "  [bold]Enter PR number to review[/bold] [dim](q to quit)[/dim]",
        default="q",
        show_default=False,
    )

    if choice.lower() == "q":
        return None

    try:
        pr_num = int(choice)
    except ValueError:
        console.print("  [red]Invalid number.[/red]")
        return None

    title = ""
    for pr in human_prs:
        if pr["number"] == pr_num:
            title = pr["title"]
            break
    if not title:
        title = _fetch_pr_title(pr_num)

    return (pr_num, title)
