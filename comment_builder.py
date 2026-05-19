"""Format review findings into GitHub PR review comments."""

from __future__ import annotations

import logging

from .models import LensType, ReviewFinding, ReviewSummary, Severity

logger = logging.getLogger(__name__)

SEVERITY_ICONS = {
    Severity.CRITICAL: "\U0001f6d1",  # stop sign
    Severity.WARNING: "\u26a0\ufe0f",  # warning
    Severity.SUGGESTION: "\U0001f4a1",  # lightbulb
    Severity.INFO: "\u2139\ufe0f",  # info
}

LENS_LABELS = {
    LensType.MIGRATION: "Migration Safety",
    LensType.AUTH: "Authorization",
    LensType.KAFKA: "Kafka / Events",
    LensType.API: "API Contract",
    LensType.TEST: "Test Coverage",
    LensType.SECURITY: "Security",
}


def build_inline_comment(finding: ReviewFinding) -> str:
    """Format a single finding as an inline comment body."""
    icon = SEVERITY_ICONS.get(finding.severity, "")
    lens_label = LENS_LABELS.get(finding.lens, finding.lens.value)
    parts = [f"{icon} **[{lens_label}]** {finding.message}"]

    if finding.suggestion:
        parts.append(f"\n**Suggestion:** {finding.suggestion}")

    return "\n".join(parts)


def build_review_body(summary: ReviewSummary) -> str:
    """Build the review summary body posted as the main review comment."""
    lines = [
        "## Poke Review Summary\n",
        f"**Risk Level:** {summary.risk_level}",
        f"**Files Reviewed:** {summary.files_reviewed}",
        f"**Lenses Applied:** {', '.join(summary.lenses_applied)}",
    ]

    if not summary.findings:
        lines.append("\nNo issues found. Looks good!")
        return "\n".join(lines)

    # Group findings by lens
    lines.append("\n### Findings by Category\n")
    by_lens = summary.findings_by_lens

    for lens_key, findings in sorted(by_lens.items()):
        label = LENS_LABELS.get(LensType(lens_key), lens_key)
        critical = sum(1 for f in findings if f.severity == Severity.CRITICAL)
        warnings = sum(1 for f in findings if f.severity == Severity.WARNING)
        suggestions = sum(1 for f in findings if f.severity == Severity.SUGGESTION)

        parts = []
        if critical:
            parts.append(f"{critical} critical")
        if warnings:
            parts.append(f"{warnings} warning(s)")
        if suggestions:
            parts.append(f"{suggestions} suggestion(s)")

        count_str = ", ".join(parts) if parts else f"{len(findings)} info"
        lines.append(f"- **{label}** ({count_str})")

    # Critical findings summary
    critical_findings = [f for f in summary.findings if f.severity == Severity.CRITICAL]
    if critical_findings:
        lines.append("\n### Critical Issues\n")
        for f in critical_findings:
            file_ref = f"`{f.file}`" + (f" (line {f.line})" if f.line else "")
            lines.append(f"- {SEVERITY_ICONS[Severity.CRITICAL]} {file_ref}: {f.message}")

    lines.append(f"\n---\n*Poked by Poke | {len(summary.findings)} finding(s) total*")

    return "\n".join(lines)


def build_github_review(summary: ReviewSummary) -> dict:
    """Build the full GitHub review payload.

    Returns a dict ready for the GitHub API:
    {
        "body": "review summary",
        "event": "COMMENT" or "REQUEST_CHANGES",
        "comments": [{"path": ..., "line": ..., "body": ...}, ...]
    }
    """
    body = build_review_body(summary)
    event = summary.review_action

    comments = []
    seen = set()

    for finding in summary.findings:
        if finding.line is None:
            continue

        # Deduplicate same file+line combos
        key = (finding.file, finding.line)
        if key in seen:
            continue
        seen.add(key)

        comment_body = build_inline_comment(finding)
        comment: dict = {
            "path": finding.file,
            "line": finding.line,
            "body": comment_body,
        }
        comments.append(comment)

    logger.info(
        "Built review: %s with %d inline comment(s), %d total finding(s)",
        event,
        len(comments),
        len(summary.findings),
    )

    return {
        "body": body,
        "event": event,
        "comments": comments,
    }
