"""Security review lens — secrets, injection, auth bypass, tenant isolation."""

from __future__ import annotations

import re

from ..models import FileChange, LensType, ReviewFinding, Severity
from .base import ReviewLens

SECRET_PATTERNS = [
    (r"""(?i)(password|secret|api_key|apikey)\s*=\s*['"][^'"]{8,}['"]""", "Possible hardcoded secret"),
    (r"(?i)BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY", "Private key in source code"),
    (r"(?i)AKIA[0-9A-Z]{16}", "Possible AWS access key"),
]

SQL_INJECTION_PATTERNS = [
    (r"""f['"](SELECT|INSERT|UPDATE|DELETE|ALTER|DROP)\b""", "f-string SQL query (injection risk)"),
    (r"""\.format\(.*\).*(?:SELECT|INSERT|UPDATE|DELETE)""", ".format() SQL query (injection risk)"),
]


class SecurityLens(ReviewLens):
    lens_type = LensType.SECURITY

    def should_review(self, file_change: FileChange) -> bool:
        path = file_change.path
        if path.startswith("tests/") or "test" in path.split("/")[-1].lower():
            return False
        return path.endswith((".py", ".yaml", ".yml", ".json"))

    def _get_rules_section(self) -> dict | None:
        rules = {}
        if "auth" in self._rules:
            rules["auth"] = self._rules["auth"]
        if "errors" in self._rules:
            rules["errors"] = self._rules["errors"]
        return rules

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        path = file_change.path

        for hunk in file_change.hunks:
            for line_num, line_content in hunk.added_lines:
                for pattern, desc in SECRET_PATTERNS:
                    if re.search(pattern, line_content):
                        if "example" in path.lower():
                            continue
                        findings.append(
                            ReviewFinding(
                                file=path,
                                line=line_num,
                                severity=Severity.CRITICAL,
                                message=f"{desc} detected on this line.",
                                suggestion="Use environment variables or a secrets manager instead.",
                                lens=LensType.SECURITY,
                            )
                        )
                        break

                if path.endswith(".py"):
                    for pattern, desc in SQL_INJECTION_PATTERNS:
                        if re.search(pattern, line_content):
                            findings.append(
                                ReviewFinding(
                                    file=path,
                                    line=line_num,
                                    severity=Severity.CRITICAL,
                                    message=f"{desc}. Use parameterized queries via SQLAlchemy.",
                                    suggestion="Use SQLAlchemy ORM or text() with bind parameters instead of string formatting.",
                                    lens=LensType.SECURITY,
                                )
                            )
                            break

        return findings
