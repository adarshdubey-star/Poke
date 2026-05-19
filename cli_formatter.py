"""Rich terminal output for Poke review results."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import LensType, ReviewFinding, ReviewSummary, Severity

console = Console()

SEVERITY_STYLES: dict[Severity, tuple[str, str]] = {
    Severity.CRITICAL: ("red bold", "CRITICAL"),
    Severity.WARNING: ("yellow bold", "WARNING"),
    Severity.SUGGESTION: ("cyan", "SUGGESTION"),
    Severity.INFO: ("dim", "INFO"),
}

LENS_STYLES: dict[LensType, tuple[str, str]] = {
    LensType.MIGRATION: ("magenta bold", "Migration"),
    LensType.AUTH: ("red bold", "Auth"),
    LensType.KAFKA: ("blue bold", "Kafka"),
    LensType.API: ("cyan bold", "API"),
    LensType.TEST: ("yellow bold", "Test"),
    LensType.SECURITY: ("red", "Security"),
}

RISK_STYLES: dict[str, str] = {
    "High": "red bold",
    "Medium": "yellow bold",
    "Low": "cyan",
    "None": "green bold",
}


def print_finding(finding: ReviewFinding, index: int) -> None:
    sev_style, sev_label = SEVERITY_STYLES.get(finding.severity, ("dim", "?"))
    lens_style, lens_label = LENS_STYLES.get(finding.lens, ("dim", finding.lens.value))

    location = finding.file
    if finding.line:
        location += f":{finding.line}"

    console.print(
        f"  [bold]{index}.[/bold]  "
        f"[{sev_style}]{sev_label}[/]  "
        f"[dim]\\[[/dim][{lens_style}]{lens_label}[/][dim]][/dim]"
    )
    console.print(f"      [dim]{location}[/dim]")
    console.print(f"      {finding.message}", width=74, highlight=False)

    if finding.suggestion:
        console.print(f"      [green]↳ {finding.suggestion}[/green]", width=74, highlight=False)
    console.print()


def print_summary(summary: ReviewSummary) -> None:
    console.print()

    # Stats panel
    risk_style = RISK_STYLES.get(summary.risk_level, "dim")
    lenses_str = ", ".join(summary.lenses_applied) or "none"

    stats = Text.assemble(
        ("Risk: ", "bold"),
        (summary.risk_level, risk_style),
        ("    Files: ", "bold"),
        (str(summary.files_reviewed), ""),
        ("    Findings: ", "bold"),
        (str(len(summary.findings)), ""),
        ("\n", ""),
        ("Lenses: ", "bold"),
        (lenses_str, "dim"),
    )
    console.print(Panel(stats, title="[bold cyan]Poke Review[/bold cyan]", subtitle=f"[dim]PR #{summary.pr_number}[/dim]", border_style="cyan", width=74))

    if not summary.findings:
        console.print()
        console.print(Panel("[green bold]  No issues found. Looks good!  [/]", border_style="green", width=74))
        console.print()
        return

    console.print()

    # Category breakdown table
    by_lens = summary.findings_by_lens
    cat_table = Table(
        show_header=True,
        header_style="bold",
        border_style="dim",
        title="[bold]Breakdown[/bold]",
        width=74,
        pad_edge=True,
    )
    cat_table.add_column("Lens", width=12)
    cat_table.add_column("Critical", style="red bold", justify="center", width=10)
    cat_table.add_column("Warning", style="yellow", justify="center", width=10)
    cat_table.add_column("Suggestion", style="cyan", justify="center", width=10)
    cat_table.add_column("Info", style="dim", justify="center", width=8)

    for lens_key in sorted(by_lens.keys()):
        findings = by_lens[lens_key]
        lens_type = LensType(lens_key)
        ls, ll = LENS_STYLES.get(lens_type, ("dim", lens_key))
        c = sum(1 for f in findings if f.severity == Severity.CRITICAL)
        w = sum(1 for f in findings if f.severity == Severity.WARNING)
        s = sum(1 for f in findings if f.severity == Severity.SUGGESTION)
        i = sum(1 for f in findings if f.severity == Severity.INFO)
        cat_table.add_row(
            f"[{ls}]{ll}[/]",
            str(c) if c else "[dim]—[/dim]",
            str(w) if w else "[dim]—[/dim]",
            str(s) if s else "[dim]—[/dim]",
            str(i) if i else "[dim]—[/dim]",
        )

    console.print(cat_table)
    console.print()

    severity_groups = [
        (Severity.CRITICAL, "Critical Issues", "red bold"),
        (Severity.WARNING, "Warnings", "yellow bold"),
        (Severity.SUGGESTION, "Suggestions", "cyan"),
        (Severity.INFO, "Info", "dim"),
    ]

    global_idx = 1
    for severity, heading, style in severity_groups:
        group = [f for f in summary.findings if f.severity == severity]
        if not group:
            continue
        console.rule(f"[{style}]{heading}[/]", style="dim")
        console.print()
        for finding in group:
            print_finding(finding, global_idx)
            global_idx += 1

    # Footer
    action = summary.review_action
    if action == "REQUEST_CHANGES":
        action_tag = "[red bold]REQUEST CHANGES[/]"
    else:
        action_tag = "[green]COMMENT[/]"

    console.print(
        Panel(
            f"[dim]Poked by[/dim] [bold cyan]Poke[/bold cyan] "
            f"[dim]|[/dim] {len(summary.findings)} finding(s) "
            f"[dim]|[/dim] Action: {action_tag}",
            border_style="cyan",
            width=74,
        )
    )
    console.print()
