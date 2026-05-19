"""Main orchestrator for the AI PR Review Agent.

Pipeline: index -> auto-discover -> parse diff -> fetch context -> route -> lenses -> critique -> dedup -> output
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from .change_router import route_all
from .comment_builder import build_github_review
from .config import Config
from .deduplicator import deduplicate
from .diff_parser import fetch_pr_metadata, get_pr_changes
from .github_client import dismiss_stale_reviews, submit_review
from .knowledge.auto_discover import discover_all
from .lenses import ALL_LENSES
from .lenses.base import set_discovered
from .llm_engine import LLMEngine
from .models import ReviewSummary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pr_review_agent")


# ---------------------------------------------------------------------------
# CI / GitHub Actions mode (original logging-based flow)
# ---------------------------------------------------------------------------

def run(config: Config | None = None) -> ReviewSummary:
    config = config or Config()

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Config error: %s", err)
        sys.exit(1)

    logger.info("=== Poke — PR Review Agent ===")
    logger.info("PR #%d | Provider: %s | Model: %s | Dry run: %s",
                config.pr_number, config.llm_provider, config.resolved_model, config.dry_run)

    discovered = discover_all()
    set_discovered(discovered)

    changes = get_pr_changes(config.pr_number)
    if not changes:
        return ReviewSummary(pr_number=config.pr_number)

    if len(changes) > config.max_files:
        changes = changes[: config.max_files]

    routing = route_all(changes)
    llm_engine = LLMEngine(config)
    lens_instances = {lens_cls.lens_type: lens_cls() for lens_cls in ALL_LENSES}

    summary = ReviewSummary(pr_number=config.pr_number, files_reviewed=len(changes))

    for lens_type, files in routing.items():
        lens = lens_instances.get(lens_type)
        if lens is None:
            continue
        summary.lenses_applied.append(lens_type.value)
        for change in files:
            if not lens.should_review(change):
                continue
            pre_findings = lens.pre_check(change)
            summary.findings.extend(pre_findings)
            rules_text = lens.get_rules_text()
            llm_findings = llm_engine.review_file(change, lens_type, rules_text)
            summary.findings.extend(llm_findings)

    summary.findings = deduplicate(summary.findings)

    review_payload = build_github_review(summary)

    if config.dry_run:
        from .cli_formatter import print_summary
        print_summary(summary)
    else:
        try:
            dismiss_stale_reviews(config, config.bot_login)
        except Exception:
            pass
        if summary.findings:
            submit_review(config, review_payload)

    return summary


# ---------------------------------------------------------------------------
# Interactive CLI mode (rich progress bars, no log noise)
# ---------------------------------------------------------------------------

def _auto_detect_env() -> None:
    if not os.environ.get("GH_TOKEN"):
        try:
            token = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if token:
                os.environ["GH_TOKEN"] = token
        except Exception:
            pass

    if not os.environ.get("GITHUB_REPOSITORY"):
        try:
            repo = subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            if repo:
                os.environ["GITHUB_REPOSITORY"] = repo
        except Exception:
            pass


def run_interactive(pr_number: int, pr_title: str = "") -> ReviewSummary:
    """Run the full review pipeline with rich progress output."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

    console = Console()

    for name in (
        "scripts.pr_review_agent", "pr_review_agent",
        "httpx", "httpcore", "openai", "chromadb",
    ):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    logging.getLogger().setLevel(logging.CRITICAL)

    _auto_detect_env()
    os.environ["PR_NUMBER"] = str(pr_number)
    os.environ["DRY_RUN"] = "true"
    config = Config()

    errors = config.validate()
    if errors:
        for err in errors:
            console.print(f"  [red]✗[/red] {err}")
        console.print()
        console.print("  [dim]Tip: run [bold]gh auth login[/bold] to authenticate.[/dim]")
        sys.exit(1)

    # Fetch PR metadata
    pr_meta = {}
    if not pr_title:
        try:
            pr_meta = fetch_pr_metadata(pr_number)
            pr_title = pr_meta.get("title", "")
        except Exception:
            pass
    pr_description = pr_meta.get("body", "") or ""

    # Header
    title_line = f"[dim]{pr_title}[/dim]\n" if pr_title else ""
    console.print()
    console.print(Panel(
        f"[bold cyan]Poke[/bold cyan] [dim]—[/dim] Reviewing PR [bold]#{pr_number}[/bold]\n"
        f"{title_line}"
        f"[dim]Model: {config.resolved_model} | Critique: {config.resolved_critique_model}[/dim]",
        border_style="cyan",
        width=74,
    ))
    console.print()

    def step_ok(label: str, detail: str = "") -> None:
        det = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [green]✓[/green] {label}{det}")

    def step_warn(label: str, detail: str = "") -> None:
        det = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  [yellow]![/yellow] {label}{det}")

    # Phase 0 — codebase index
    with console.status("  [bold]Indexing codebase…[/bold]", spinner="dots"):
        try:
            from .codebase_index import build_index, query_context
            collection = build_index()
            index_count = collection.count()
        except Exception:
            collection = None
            index_count = 0
    if collection:
        step_ok("Codebase indexed", f"{index_count} chunks")
    else:
        step_warn("Codebase index skipped", "chromadb not available")

    # Phase 1 — auto-discover
    with console.status("  [bold]Discovering codebase facts…[/bold]", spinner="dots"):
        discovered = discover_all()
        set_discovered(discovered)
    topics = len(discovered.get("kafka_topics", []))
    tables = len(discovered.get("partitioned_tables", []))
    endpoints = len(discovered.get("api_endpoints", []))
    step_ok("Codebase scanned", f"{topics} topics · {tables} tables · {endpoints} endpoints")

    # Phase 2 — parse diff + fetch full files
    with console.status("  [bold]Fetching PR diff + full files…[/bold]", spinner="dots"):
        changes = get_pr_changes(pr_number, fetch_full_files=True)

    if not changes:
        step_warn("No file changes found")
        return ReviewSummary(pr_number=pr_number)

    if len(changes) > config.max_files:
        changes = changes[:config.max_files]
        step_warn("Diff parsed", f"{len(changes)} files (capped)")
    else:
        full_count = sum(1 for c in changes if c.full_content)
        step_ok("Diff parsed", f"{len(changes)} files ({full_count} with full context)")

    # Phase 3 — route
    with console.status("  [bold]Routing files to lenses…[/bold]", spinner="dots"):
        routing = route_all(changes)
    lens_names = ", ".join(lt.value.capitalize() for lt in routing)
    step_ok("Files routed", lens_names)

    # Build PR change summary for cross-file awareness
    change_summary_lines = []
    for c in changes:
        added = c.lines_added
        removed = c.lines_removed
        change_summary_lines.append(f"- {c.path} (+{added}/-{removed})")
    pr_change_summary = "\n".join(change_summary_lines)

    # Phase 4 — run lenses (per-file, with cross-file summary for context)
    llm_engine = LLMEngine(config)
    lens_instances = {cls.lens_type: cls() for cls in ALL_LENSES}
    summary = ReviewSummary(pr_number=pr_number, files_reviewed=len(changes))

    total_work = sum(len(files) for files in routing.values())

    console.print()
    with Progress(
        SpinnerColumn("dots"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30, complete_style="cyan", finished_style="green"),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[current_file]}[/dim]"),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(
            "  [bold]Running lenses…[/bold]",
            total=total_work,
            current_file="",
        )

        for lens_type, files in routing.items():
            lens = lens_instances.get(lens_type)
            if lens is None:
                progress.advance(task, advance=len(files))
                continue

            summary.lenses_applied.append(lens_type.value)

            for change in files:
                short = change.path.split("/")[-1]
                progress.update(
                    task,
                    description=f"  [bold]{lens_type.value.capitalize()}[/bold] lens",
                    current_file=short,
                )

                if lens.should_review(change):
                    pre = lens.pre_check(change)
                    summary.findings.extend(pre)

                    related = ""
                    if collection:
                        try:
                            from .codebase_index import query_context
                            chunks = query_context(collection, change.diff_text, change.path)
                            related = "\n\n---\n\n".join(chunks)
                        except Exception:
                            pass

                    rules_text = lens.get_rules_text()
                    llm = llm_engine.review_file(
                        change, lens_type, rules_text,
                        pr_description=pr_description,
                        related_context=related,
                        pr_change_summary=pr_change_summary,
                    )
                    summary.findings.extend(llm)

                progress.advance(task)

        progress.update(task, description="  [green]✓[/green] Lenses complete", current_file="")

    console.print()
    raw_count = len(summary.findings)
    step_ok("Raw findings", str(raw_count))

    # Phase 5 — self-critique
    if summary.findings:
        with console.status("  [bold]Validating findings…[/bold]", spinner="dots"):
            all_diff = "\n".join(c.diff_text for c in changes)
            all_full = "\n".join(c.full_content or "" for c in changes if c.full_content)
            summary.findings = llm_engine.critique_findings(
                summary.findings, all_diff, all_full, pr_description,
            )
        validated_count = len(summary.findings)
        if validated_count < raw_count:
            step_ok("Validated", f"{raw_count} → {validated_count}")
        else:
            step_ok("All findings validated")

    # Phase 6 — deduplicate
    with console.status("  [bold]Deduplicating…[/bold]", spinner="dots"):
        before = len(summary.findings)
        summary.findings = deduplicate(summary.findings)
    if before != len(summary.findings):
        step_ok("Deduplicated", f"{before} → {len(summary.findings)}")
    else:
        step_ok("No duplicates")

    console.print()

    from .cli_formatter import print_summary
    print_summary(summary)

    if summary.findings:
        _prompt_post_to_pr(console, config, summary)

    return summary


def _ordered_findings(summary: ReviewSummary) -> list:
    from .models import Severity

    order = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.SUGGESTION: 2, Severity.INFO: 3}
    return sorted(summary.findings, key=lambda f: order.get(f.severity, 99))


def _parse_selection(choice: str, total: int) -> list[int] | None:
    choice = choice.strip().lower()
    if choice in ("q", ""):
        return None
    if choice == "a":
        return list(range(total))

    indices: list[int] = []
    for part in choice.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            num = int(part)
            if 1 <= num <= total:
                indices.append(num - 1)
        except ValueError:
            pass
    return indices if indices else None


def _prompt_post_to_pr(console, config: Config, summary: ReviewSummary) -> None:
    from rich.prompt import Prompt

    from .comment_builder import build_github_review
    from .github_client import get_repo_name, post_comment, submit_review

    choice = Prompt.ask(
        "  [bold]Post findings to PR?[/bold] [dim](a=all, 1,3,5=pick, q=skip)[/dim]",
        default="q",
        show_default=False,
    )

    ordered = _ordered_findings(summary)
    selected_indices = _parse_selection(choice, len(ordered))

    if selected_indices is None:
        console.print("  [dim]Skipped — no comments posted.[/dim]\n")
        return

    selected = [ordered[i] for i in selected_indices]

    filtered_summary = ReviewSummary(
        pr_number=summary.pr_number,
        findings=selected,
        files_reviewed=summary.files_reviewed,
        lenses_applied=summary.lenses_applied,
    )
    review_payload = build_github_review(filtered_summary)
    repo = config.repo or get_repo_name()

    with console.status("  [bold]Posting to PR…[/bold]", spinner="dots"):
        try:
            submit_review(config, review_payload)
        except Exception:
            try:
                post_comment(config, review_payload["body"])
            except Exception as exc:
                console.print(f"  [red]✗[/red] Failed to post: {exc}\n")
                return
            console.print(
                f"  [green]✓[/green] Posted summary comment to PR "
                f"[cyan]#{summary.pr_number}[/cyan]  "
                f"[dim](inline annotations skipped)[/dim]"
            )
            console.print(f"  [dim]https://github.com/{repo}/pull/{summary.pr_number}[/dim]\n")
            return

    console.print(
        f"  [green]✓[/green] Posted [bold]{len(selected)}[/bold] finding(s) to PR "
        f"[cyan]#{summary.pr_number}[/cyan]"
    )
    console.print(f"  [dim]https://github.com/{repo}/pull/{summary.pr_number}[/dim]\n")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
