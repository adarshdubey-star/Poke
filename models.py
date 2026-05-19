from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum


class ChangeType(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class Severity(Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    SUGGESTION = "suggestion"
    INFO = "info"


class LensType(Enum):
    MIGRATION = "migration"
    AUTH = "auth"
    KAFKA = "kafka"
    API = "api"
    TEST = "test"
    SECURITY = "security"


@dataclass
class DiffHunk:
    """A single hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]

    @property
    def added_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for added lines."""
        result = []
        line_num = self.new_start
        for line in self.lines:
            if line.startswith("+"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("-"):
                continue
            else:
                line_num += 1
        return result

    @property
    def removed_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for removed lines."""
        result = []
        line_num = self.old_start
        for line in self.lines:
            if line.startswith("-"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("+"):
                continue
            else:
                line_num += 1
        return result


@dataclass
class FileChange:
    """A single file's changes in a PR."""

    path: str
    change_type: ChangeType
    hunks: list[DiffHunk] = field(default_factory=list)
    old_path: str | None = None
    full_content: str | None = None

    @property
    def lines_added(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def lines_removed(self) -> int:
        return sum(len(h.removed_lines) for h in self.hunks)

    @property
    def diff_text(self) -> str:
        """Reconstruct the diff text for this file."""
        parts = []
        for hunk in self.hunks:
            parts.append(f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@")
            parts.extend(hunk.lines)
        return "\n".join(parts)

    @property
    def added_content(self) -> str:
        """All added lines concatenated."""
        lines = []
        for hunk in self.hunks:
            for _, content in hunk.added_lines:
                lines.append(content)
        return "\n".join(lines)


@dataclass
class ReviewFinding:
    """A single review finding from a lens."""

    file: str
    line: int | None
    severity: Severity
    message: str
    suggestion: str | None = None
    lens: LensType = LensType.SECURITY

    @property
    def is_critical(self) -> bool:
        return self.severity == Severity.CRITICAL

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity.value,
            "message": self.message,
            "suggestion": self.suggestion,
            "lens": self.lens.value,
        }


@dataclass
class ReviewSummary:
    """Overall review summary for a PR."""

    pr_number: int
    findings: list[ReviewFinding] = field(default_factory=list)
    files_reviewed: int = 0
    lenses_applied: list[str] = field(default_factory=list)

    @property
    def risk_level(self) -> str:
        if any(f.severity == Severity.CRITICAL for f in self.findings):
            return "High"
        if any(f.severity == Severity.WARNING for f in self.findings):
            return "Medium"
        if self.findings:
            return "Low"
        return "None"

    @property
    def has_critical(self) -> bool:
        return any(f.is_critical for f in self.findings)

    @property
    def findings_by_lens(self) -> dict[str, list[ReviewFinding]]:
        grouped: dict[str, list[ReviewFinding]] = {}
        for f in self.findings:
            grouped.setdefault(f.lens.value, []).append(f)
        return grouped

    @property
    def review_action(self) -> str:
        """COMMENT or REQUEST_CHANGES based on findings."""
        return "REQUEST_CHANGES" if self.has_critical else "COMMENT"
