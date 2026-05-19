"""Route changed files to the appropriate review lenses."""

from __future__ import annotations

import logging

from .config import AUTH_KEYWORDS, ROUTE_PATTERNS
from .models import FileChange, LensType

logger = logging.getLogger(__name__)


def _matches_patterns(path: str, patterns: list[str]) -> bool:
    return any(pattern in path for pattern in patterns)


def _has_auth_keywords(change: FileChange) -> bool:
    """Check if the added content contains auth-related keywords."""
    content = change.added_content
    return any(kw in content for kw in AUTH_KEYWORDS)


def route_file(change: FileChange) -> set[LensType]:
    """Determine which lenses should review a given file change.

    A single file can be routed to multiple lenses. Security always runs
    on every file.
    """
    lenses: set[LensType] = set()
    path = change.path

    # Skip non-Python and non-YAML files for most lenses
    is_reviewable = path.endswith((".py", ".yaml", ".yml", ".json"))

    if is_reviewable:
        if _matches_patterns(path, ROUTE_PATTERNS["migration"]):
            lenses.add(LensType.MIGRATION)

        if _matches_patterns(path, ROUTE_PATTERNS["auth"]) or _has_auth_keywords(change):
            lenses.add(LensType.AUTH)

        if _matches_patterns(path, ROUTE_PATTERNS["kafka"]):
            lenses.add(LensType.KAFKA)

        if _matches_patterns(path, ROUTE_PATTERNS["api"]):
            lenses.add(LensType.API)

        if _matches_patterns(path, ROUTE_PATTERNS["test"]):
            lenses.add(LensType.TEST)

    if is_reviewable:
        lenses.add(LensType.SECURITY)

    return lenses


def route_all(changes: list[FileChange]) -> dict[LensType, list[FileChange]]:
    """Route all file changes to their applicable lenses.

    Returns a mapping from lens type to the list of files that lens should review.
    """
    routing: dict[LensType, list[FileChange]] = {}

    for change in changes:
        assigned = route_file(change)
        for lens_type in assigned:
            routing.setdefault(lens_type, []).append(change)

    # Check if source files changed without corresponding test files
    source_files = [c for c in changes if not c.path.startswith("tests/") and c.path.endswith(".py")]
    test_files = [c for c in changes if c.path.startswith("tests/")]

    if source_files and not test_files:
        # Route source files to the test lens so it can flag missing tests
        for change in source_files:
            routing.setdefault(LensType.TEST, []).append(change)

    for lens_type, files in routing.items():
        logger.info("  %s -> %d file(s)", lens_type.value, len(files))

    return routing
