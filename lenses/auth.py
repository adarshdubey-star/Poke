"""Auth review lens — RBAC v1/v2, Kessel, tenant isolation."""

from __future__ import annotations

from ..models import FileChange, LensType, ReviewFinding, Severity
from .base import ReviewLens


class AuthLens(ReviewLens):
    lens_type = LensType.AUTH

    def should_review(self, file_change: FileChange) -> bool:
        path = file_change.path
        if path.startswith("tests/"):
            return False
        auth_paths = ("lib/middleware.py", "lib/kessel.py", "lib/rbac", "api/")
        if any(p in path for p in auth_paths):
            return True
        content = file_change.added_content
        return any(kw in content for kw in ("@access", "@rbac", "resolve_permission", "check_access"))

    def _get_rules_section(self) -> dict | None:
        return self._rules.get("auth")

    def _get_discovered_section(self) -> dict | None:
        auth = self._discovered.get("auth_decorators")
        flags = self._discovered.get("feature_flags")
        if not auth and not flags:
            return None
        section: dict = {}
        if auth:
            section["files_using_access"] = auth.get("access", [])
            section["files_using_rbac"] = auth.get("rbac", [])
            if auth.get("neither"):
                section["files_with_unprotected_routes"] = auth["neither"]
        if flags:
            section["feature_flags_in_use"] = flags
        return section

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        content = file_change.added_content
        path = file_change.path

        for flag in ("bypass_rbac", "bypass_kessel"):
            if flag in content and "config" not in path.lower():
                findings.append(
                    ReviewFinding(
                        file=path,
                        line=None,
                        severity=Severity.WARNING,
                        message=f"'{flag}' referenced in production code. This flag is forced True only in TEST.",
                        suggestion=f"Ensure '{flag}' is not used in production logic.",
                        lens=LensType.AUTH,
                    )
                )

        return findings
