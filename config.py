from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field


@dataclass(frozen=True)
class Config:
    """Agent configuration, populated from environment variables."""

    # GitHub
    gh_token: str = field(default_factory=lambda: os.environ.get("GH_TOKEN", ""))
    repo: str = field(default_factory=lambda: os.environ.get("GITHUB_REPOSITORY", ""))
    pr_number: int = field(default_factory=lambda: int(os.environ.get("PR_NUMBER", "0")))

    # LLM — defaults to GitHub Models (free, uses GH_TOKEN, no extra key needed)
    llm_api_key: str = field(
        default_factory=lambda: os.environ.get("LLM_API_KEY", os.environ.get("GH_TOKEN", ""))
    )
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "github"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", ""))

    # Critique pass — defaults to Claude Sonnet via Anthropic for best false-positive filtering
    llm_critique_provider: str = field(
        default_factory=lambda: os.environ.get("LLM_CRITIQUE_PROVIDER", "")
    )
    llm_critique_model: str = field(default_factory=lambda: os.environ.get("LLM_CRITIQUE_MODEL", ""))
    llm_critique_api_key: str = field(
        default_factory=lambda: os.environ.get("LLM_CRITIQUE_API_KEY", "")
    )

    # Behaviour
    confidence_threshold: int = 6
    dry_run: bool = field(default_factory=lambda: os.environ.get("DRY_RUN", "false").lower() == "true")
    max_findings_per_lens: int = 10
    max_files: int = 50
    max_diff_tokens: int = 12000

    # Bot identity — used to avoid re-reviewing own comments
    bot_login: str = "github-actions[bot]"

    @property
    def resolved_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        defaults = {
            "github": "gpt-4o",
            "openai": "gpt-4o",
            "anthropic": "claude-sonnet-4-20250514",
        }
        return defaults.get(self.llm_provider, "gpt-4o")

    @property
    def resolved_critique_model(self) -> str:
        if self.llm_critique_model:
            return self.llm_critique_model
        if self.resolved_critique_provider == "anthropic":
            return "claude-sonnet-4-20250514"
        return "o3-mini"

    @property
    def resolved_critique_provider(self) -> str:
        return self.llm_critique_provider or self.llm_provider

    @property
    def resolved_critique_api_key(self) -> str:
        return self.llm_critique_api_key or self.llm_api_key

    def validate(self) -> list[str]:
        errors = []
        if not self.gh_token:
            errors.append("GH_TOKEN is required")
        if not self.pr_number:
            errors.append("PR_NUMBER is required")
        if not self.llm_api_key:
            if self.llm_provider == "github":
                errors.append("GH_TOKEN is required (used as LLM key for GitHub Models)")
            else:
                errors.append("LLM_API_KEY is required")
        return errors


# File-type routing patterns
ROUTE_PATTERNS: dict[str, list[str]] = {
    "migration": ["migrations/versions/"],
    "auth": [
        "lib/middleware.py",
        "lib/kessel.py",
        "lib/rbac.py",
        "api/rbac",
    ],
    "kafka": [
        "app/queue/",
        "lib/kafka",
        "kafka",
        "event",
        "inv_mq_service",
        "inv_export_service",
    ],
    "api": [
        "api/",
        "swagger/",
    ],
    "test": [
        "tests/",
    ],
}

# Files where auth keywords trigger the auth lens even outside the auth paths
AUTH_KEYWORDS = {"@access", "@rbac", "resolve_permission", "check_access", "bypass_rbac", "bypass_kessel"}
