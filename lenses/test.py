"""Test review lens — coverage gaps, pattern compliance, fixture usage."""

from __future__ import annotations

import re

from ..models import FileChange, LensType, ReviewFinding, Severity
from .base import ReviewLens


class TestLens(ReviewLens):
    lens_type = LensType.TEST

    def should_review(self, file_change: FileChange) -> bool:
        return file_change.path.endswith(".py")

    def _get_rules_section(self) -> dict | None:
        return self._rules.get("testing")

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        content = file_change.added_content
        path = file_change.path

        if path.startswith("tests/"):
            # Check for SQLite usage in tests (should use real PostgreSQL)
            if "sqlite" in content.lower():
                findings.append(
                    ReviewFinding(
                        file=path,
                        line=None,
                        severity=Severity.WARNING,
                        message="SQLite reference in tests. HBI tests must use a real PostgreSQL database.",
                        suggestion="Use the database fixture from tests.fixtures.db_fixtures.",
                        lens=LensType.TEST,
                    )
                )

            # Check for custom fixture definitions that duplicate existing ones
            custom_fixtures = re.findall(r"@pytest\.fixture.*\ndef\s+(\w+)", content)
            known_fixtures = {
                "flask_app", "flask_client", "database", "inventory_config",
                "api_get", "api_post", "api_patch", "api_put", "api_delete",
                "mq_create_or_update_host",
            }
            duplicates = [f for f in custom_fixtures if f in known_fixtures]
            if duplicates:
                findings.append(
                    ReviewFinding(
                        file=path,
                        line=None,
                        severity=Severity.WARNING,
                        message=f"Fixture(s) {duplicates} duplicate existing fixtures in tests/fixtures/.",
                        suggestion="Use the existing fixtures from tests.fixtures.* instead of redefining them.",
                        lens=LensType.TEST,
                    )
                )

        return findings
