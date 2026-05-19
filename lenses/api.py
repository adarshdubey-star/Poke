"""API review lens — OpenAPI spec sync, breaking changes, endpoint conventions."""

from __future__ import annotations

from ..models import FileChange, LensType, ReviewFinding, Severity
from .base import ReviewLens


class APILens(ReviewLens):
    lens_type = LensType.API

    def should_review(self, file_change: FileChange) -> bool:
        path = file_change.path
        return path.startswith("api/") or path.startswith("swagger/")

    def _get_rules_section(self) -> dict | None:
        return self._rules.get("api")

    def _get_discovered_section(self) -> dict | None:
        endpoints = self._discovered.get("api_endpoints")
        if not endpoints:
            return None
        op_ids = [e["operationId"] for e in endpoints if e.get("operationId")]
        return {
            "total_endpoints": len(endpoints),
            "existing_operationIds": op_ids,
        }

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        return []
