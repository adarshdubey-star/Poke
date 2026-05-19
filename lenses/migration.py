"""Migration review lens — partitioned tables, replica identity, downgrade safety."""

from __future__ import annotations

import re

from ..models import FileChange, LensType, ReviewFinding, Severity
from .base import ReviewLens

FALLBACK_PARTITIONED_TABLES = {"hosts", "system_profiles_static", "system_profiles_dynamic"}


class MigrationLens(ReviewLens):
    lens_type = LensType.MIGRATION

    @property
    def _partitioned_tables(self) -> set[str]:
        """Get partitioned table names from auto-discovery, with fallback."""
        discovered = self._discovered.get("partitioned_tables", [])
        if discovered:
            return {t["table"] for t in discovered}
        return FALLBACK_PARTITIONED_TABLES

    @property
    def _partition_count(self) -> str:
        discovered = self._discovered.get("partitioned_tables", [])
        if discovered:
            return discovered[0].get("default_partitions", "32")
        return "32"

    def should_review(self, file_change: FileChange) -> bool:
        return "migrations/versions/" in file_change.path and file_change.path.endswith(".py")

    def _get_rules_section(self) -> dict | None:
        return self._rules.get("migrations")

    def _get_discovered_section(self) -> dict | None:
        tables = self._discovered.get("partitioned_tables")
        if not tables:
            return None
        return {"discovered_partitioned_tables": tables}

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        content = file_change.added_content
        diff = file_change.diff_text

        # Check for missing downgrade
        if "def downgrade" not in diff:
            findings.append(
                ReviewFinding(
                    file=file_change.path,
                    line=None,
                    severity=Severity.WARNING,
                    message="Migration has no downgrade() function. Every migration should be reversible.",
                    suggestion="Add a downgrade() function that reverses the upgrade() changes.",
                    lens=LensType.MIGRATION,
                )
            )
        elif re.search(r"def downgrade\(\):\s*\n\s*pass", diff):
            findings.append(
                ReviewFinding(
                    file=file_change.path,
                    line=None,
                    severity=Severity.WARNING,
                    message="downgrade() is empty (just 'pass'). This migration cannot be rolled back.",
                    suggestion="Implement downgrade() to reverse the upgrade() operations.",
                    lens=LensType.MIGRATION,
                )
            )

        # Check for hardcoded 'hbi' schema instead of INVENTORY_SCHEMA
        hbi_matches = list(re.finditer(r"""schema\s*=\s*['"]hbi['"]""", content))
        if hbi_matches:
            findings.append(
                ReviewFinding(
                    file=file_change.path,
                    line=None,
                    severity=Severity.WARNING,
                    message="Hardcoded schema='hbi' found. Use INVENTORY_SCHEMA from app.models.constants instead.",
                    suggestion="Import INVENTORY_SCHEMA from app.models.constants and use schema=INVENTORY_SCHEMA.",
                    lens=LensType.MIGRATION,
                )
            )

        # Check for partitioned table operations without helper usage
        partitioned = self._partitioned_tables
        part_count = self._partition_count
        touches_partitioned = any(table in content for table in partitioned)
        if touches_partitioned:
            matched = [t for t in partitioned if t in content]
            uses_helper = "partitioned_table_index_helper" in content or "TABLE_NUM_PARTITIONS" in content
            has_index_op = any(kw in content for kw in ("create_index", "drop_index", "op.create_index", "op.drop_index"))

            if has_index_op and not uses_helper:
                findings.append(
                    ReviewFinding(
                        file=file_change.path,
                        line=None,
                        severity=Severity.CRITICAL,
                        message=(
                            f"Index operation on partitioned table(s) {matched} without using "
                            f"partitioned_table_index_helper. This will only affect the parent table, "
                            f"not the {part_count} partitions."
                        ),
                        suggestion="Use utils.partitioned_table_index_helper.{create,drop}_partitioned_table_index.",
                        lens=LensType.MIGRATION,
                    )
                )

            if "replica identity" in content.lower() or "REPLICA IDENTITY" in content:
                if "TABLE_NUM_PARTITIONS" not in content and "_p" not in content:
                    findings.append(
                        ReviewFinding(
                            file=file_change.path,
                            line=None,
                            severity=Severity.CRITICAL,
                            message=(
                                f"Replica identity change on partitioned table(s) {matched} without "
                                f"per-partition handling. Changes on parent do NOT cascade to "
                                f"{part_count} partitions."
                            ),
                            suggestion=(
                                f"Apply ALTER TABLE ... REPLICA IDENTITY to each partition "
                                f"({matched[0]}_p0..{matched[0]}_p{int(part_count)-1})."
                            ),
                            lens=LensType.MIGRATION,
                        )
                    )

        return findings
