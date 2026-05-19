"""Base class for review lenses."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

from ..models import FileChange, LensType, ReviewFinding

logger = logging.getLogger(__name__)

_rules_cache: dict | None = None
_discovered_cache: dict | None = None


def load_rules() -> dict:
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache

    rules_path = Path(__file__).parent.parent / "knowledge" / "rules.yaml"
    with open(rules_path) as f:
        _rules_cache = yaml.safe_load(f)
    return _rules_cache


def set_discovered(discovered: dict) -> None:
    """Store auto-discovered facts for lenses to access."""
    global _discovered_cache
    _discovered_cache = discovered


def get_discovered() -> dict:
    """Return auto-discovered codebase facts (or empty dict if not run yet)."""
    return _discovered_cache or {}


class ReviewLens(ABC):
    """Base interface for domain-specific review lenses."""

    lens_type: LensType

    def __init__(self) -> None:
        self._rules = load_rules()
        self._discovered = get_discovered()

    @abstractmethod
    def should_review(self, file_change: FileChange) -> bool:
        """Return True if this lens should review the given file."""

    def get_rules_text(self) -> str:
        """Return static rules + auto-discovered facts as formatted text for LLM prompts."""
        section = self._get_rules_section()
        parts = []
        if section:
            parts.append(yaml.dump(section, default_flow_style=False))

        discovered_section = self._get_discovered_section()
        if discovered_section:
            parts.append("--- Auto-discovered from codebase ---")
            parts.append(yaml.dump(discovered_section, default_flow_style=False))

        return "\n".join(parts)

    @abstractmethod
    def _get_rules_section(self) -> dict | list | None:
        """Return the rules.yaml section relevant to this lens."""

    def _get_discovered_section(self) -> dict | list | None:
        """Return auto-discovered facts relevant to this lens. Override in subclasses."""
        return None

    def build_context(self, file_change: FileChange) -> dict[str, Any]:
        """Gather additional context for the review. Override in subclasses."""
        return {}

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        """Run deterministic checks before sending to LLM. Override in subclasses.

        These checks don't require an LLM and catch obvious issues fast.
        """
        return []
