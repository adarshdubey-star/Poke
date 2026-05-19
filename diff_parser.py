"""Parse PR diffs from GitHub into structured FileChange objects."""

from __future__ import annotations

import json
import logging
import re
import subprocess

from .models import ChangeType, DiffHunk, FileChange

logger = logging.getLogger(__name__)

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def fetch_pr_diff(pr_number: int) -> str:
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--color=never"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def fetch_pr_files(pr_number: int) -> list[dict]:
    """Fetch the list of changed files with metadata (status, additions, deletions)."""
    result = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--json", "files"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data.get("files", [])


def _classify_change_type(status: str) -> ChangeType:
    mapping = {
        "added": ChangeType.ADDED,
        "removed": ChangeType.DELETED,
        "modified": ChangeType.MODIFIED,
        "renamed": ChangeType.RENAMED,
        "copied": ChangeType.ADDED,
    }
    return mapping.get(status.lower(), ChangeType.MODIFIED)


def parse_diff(diff_text: str) -> list[FileChange]:
    """Parse a unified diff string into a list of FileChange objects."""
    files: list[FileChange] = []
    current_file: FileChange | None = None
    current_hunk: DiffHunk | None = None

    for line in diff_text.splitlines():
        # New file header
        if line.startswith("diff --git"):
            if current_hunk and current_file:
                current_file.hunks.append(current_hunk)
            if current_file:
                files.append(current_file)
            current_hunk = None
            current_file = None
            continue

        # Parse file paths from --- and +++ lines
        if line.startswith("--- a/"):
            old_path = line[6:]
            if current_file is None:
                current_file = FileChange(path=old_path, change_type=ChangeType.MODIFIED)
            current_file.old_path = old_path
            continue

        if line.startswith("+++ b/"):
            new_path = line[6:]
            if current_file is None:
                current_file = FileChange(path=new_path, change_type=ChangeType.ADDED)
            else:
                current_file.path = new_path
            continue

        if line.startswith("--- /dev/null"):
            if current_file is None:
                current_file = FileChange(path="", change_type=ChangeType.ADDED)
            continue

        if line.startswith("+++ /dev/null"):
            if current_file:
                current_file.change_type = ChangeType.DELETED
            continue

        # Hunk header
        match = HUNK_HEADER_RE.match(line)
        if match:
            if current_hunk and current_file:
                current_file.hunks.append(current_hunk)
            current_hunk = DiffHunk(
                old_start=int(match.group(1)),
                old_count=int(match.group(2) or "1"),
                new_start=int(match.group(3)),
                new_count=int(match.group(4) or "1"),
                lines=[],
            )
            continue

        # Diff content lines
        if current_hunk is not None and (line.startswith("+") or line.startswith("-") or line.startswith(" ")):
            current_hunk.lines.append(line)

    # Flush last hunk/file
    if current_hunk and current_file:
        current_file.hunks.append(current_hunk)
    if current_file:
        files.append(current_file)

    return files


def fetch_pr_metadata(pr_number: int) -> dict:
    """Fetch PR title, description, and head ref."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title,body,headRefName,headRefOid"],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return {}


def fetch_full_file(pr_number: int, file_path: str) -> str | None:
    """Fetch the full content of a file at the PR's HEAD ref."""
    try:
        meta = fetch_pr_metadata(pr_number) if not hasattr(fetch_full_file, "_meta") else fetch_full_file._meta
        fetch_full_file._meta = meta
        ref = meta.get("headRefOid", meta.get("headRefName", "HEAD"))

        result = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/contents/{file_path}?ref={ref}",
             "-H", "Accept: application/vnd.github.raw+json"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_pr_changes(pr_number: int, fetch_full_files: bool = False) -> list[FileChange]:
    """Fetch and parse a PR's diff into structured FileChange objects."""
    logger.info("Fetching diff for PR #%d", pr_number)
    diff_text = fetch_pr_diff(pr_number)

    if not diff_text.strip():
        logger.warning("PR #%d has an empty diff", pr_number)
        return []

    changes = parse_diff(diff_text)
    logger.info("Parsed %d file changes from PR #%d", len(changes), pr_number)

    # Enrich with file metadata from the API when available
    try:
        file_meta = fetch_pr_files(pr_number)
        meta_by_path = {f["path"]: f for f in file_meta}
        for change in changes:
            meta = meta_by_path.get(change.path)
            if meta and "status" in meta:
                change.change_type = _classify_change_type(meta["status"])
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        logger.debug("Could not fetch file metadata, using diff-inferred types")

    # Optionally fetch full file content for each changed file
    if fetch_full_files:
        for change in changes:
            if change.change_type != ChangeType.DELETED and change.path.endswith(".py"):
                change.full_content = fetch_full_file(pr_number, change.path)

    return changes
