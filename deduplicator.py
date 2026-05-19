"""Deduplicate and consolidate similar findings across lenses and files."""

from __future__ import annotations

import logging
import re

from .models import ReviewFinding, Severity

logger = logging.getLogger(__name__)

SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.WARNING: 3,
    Severity.SUGGESTION: 2,
    Severity.INFO: 1,
}

# Domain concepts — when multiple findings flag the same underlying issue
# across different files, consolidate them into one.
CONCEPT_PATTERNS: dict[str, list[str]] = {
    "tenant_isolation": [
        "org_id", "cross-tenant", "tenant isolation", "data leakage",
        "unauthorized access", "tenant data", "multi-tenant",
    ],
    "test_fixtures": [
        "flask_client", "api_get", "api_post", "api_patch",
        "api helper", "testing conventions", "fixture",
    ],
    "test_mocking": [
        "mock external", "rbac service", "kafka producer",
        "external service", "external dependencies", "external http",
    ],
    "test_isolation": [
        "clean_tables", "shared state", "execution order",
        "flaky test", "test ordering", "database is cleaned",
    ],
    "test_coverage": [
        "happy path", "auth denied", "invalid input", "not found",
        "required scenarios", "comprehensive coverage",
    ],
    "schema_constant": [
        "inventory_schema", "hardcoded.*hbi", "schema.*hbi",
    ],
    "request_tracking": ["request_id"],
    "api_spec_sync": [
        "swagger.*openapi", "openapi.*swagger",
        "operationid", "api documentation",
    ],
}

MAX_FINDINGS_PER_LENS = 5


def _detect_concept(finding: ReviewFinding) -> str | None:
    text = finding.message.lower()
    for concept, patterns in CONCEPT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text):
                return concept
    return None


def _merge_group(group: list[ReviewFinding]) -> ReviewFinding:
    """Merge a group of similar findings into one, keeping the best."""
    group.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)
    best = group[0]

    other_files = sorted({f.file for f in group if f.file != best.file})
    other_lenses = sorted({f.lens.value for f in group if f.lens != best.lens})

    suffix_parts = []
    if other_files:
        suffix_parts.append(f"Also affects: {', '.join(other_files)}")
    if other_lenses:
        suffix_parts.append(f"Also flagged by: {', '.join(other_lenses)}")

    message = best.message
    if suffix_parts:
        message += " [" + ". ".join(suffix_parts) + "]"

    return ReviewFinding(
        file=best.file,
        line=best.line,
        severity=best.severity,
        message=message,
        suggestion=best.suggestion,
        lens=best.lens,
    )


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_keywords(text: str) -> set[str]:
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "this", "that", "these", "those", "it", "its", "and", "or", "but",
        "not", "no", "if", "which", "who", "whom", "what", "when", "where",
        "how", "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "than", "too", "very", "also", "just", "about",
        "ensure", "function", "code", "lead", "change", "new", "parameter",
    }
    words = set(_normalize(text).split())
    return words - stop_words


def _jaccard_similarity(a: ReviewFinding, b: ReviewFinding) -> float:
    kw_a = _extract_keywords(a.message)
    kw_b = _extract_keywords(b.message)
    if not kw_a or not kw_b:
        return 0.0
    overlap = kw_a & kw_b
    union = kw_a | kw_b
    score = len(overlap) / len(union)
    if a.file == b.file:
        score = min(score + 0.15, 1.0)
    return score


JACCARD_THRESHOLD = 0.40


def deduplicate(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Remove redundant findings using concept grouping and keyword similarity.

    Phase 1: Group by detected domain concept (per lens). Merge each group
             into a single finding listing all affected files.
    Phase 2: Jaccard-based dedup for remaining ungrouped findings.
    Phase 3: Cap total findings per lens.
    """
    if len(findings) <= 1:
        return findings

    # --- Phase 1: concept-based consolidation ---
    concept_groups: dict[tuple[str, str], list[ReviewFinding]] = {}
    ungrouped: list[ReviewFinding] = []

    for f in findings:
        concept = _detect_concept(f)
        if concept:
            key = (concept, f.lens.value)
            concept_groups.setdefault(key, []).append(f)
        else:
            ungrouped.append(f)

    consolidated: list[ReviewFinding] = []
    for group in concept_groups.values():
        consolidated.append(_merge_group(group))

    # --- Phase 2: Jaccard dedup on ungrouped ---
    assigned: set[int] = set()
    jaccard_groups: list[list[int]] = []

    for i in range(len(ungrouped)):
        if i in assigned:
            continue
        group = [i]
        assigned.add(i)
        for j in range(i + 1, len(ungrouped)):
            if j in assigned:
                continue
            if _jaccard_similarity(ungrouped[i], ungrouped[j]) >= JACCARD_THRESHOLD:
                group.append(j)
                assigned.add(j)
        jaccard_groups.append(group)

    for group_indices in jaccard_groups:
        group_findings = [ungrouped[i] for i in group_indices]
        consolidated.append(_merge_group(group_findings))

    # --- Phase 3: cap per lens ---
    by_lens: dict[str, list[ReviewFinding]] = {}
    for f in consolidated:
        by_lens.setdefault(f.lens.value, []).append(f)

    result: list[ReviewFinding] = []
    for lens_key, lens_findings in by_lens.items():
        lens_findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)
        result.extend(lens_findings[:MAX_FINDINGS_PER_LENS])

    removed = len(findings) - len(result)
    if removed:
        logger.info("Deduplicated %d finding(s) -> %d unique", len(findings), len(result))

    return result
