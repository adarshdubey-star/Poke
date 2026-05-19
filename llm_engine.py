"""Provider-agnostic LLM wrapper for structured code review."""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import Config
from .knowledge.prompts import CRITIQUE_PROMPT, FINDINGS_SCHEMA, LENS_PROMPTS, SYSTEM_PROMPT
from .models import FileChange, LensType, ReviewFinding, Severity

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 3.5


def _truncate(text: str, max_tokens: int) -> str:
    max_chars = int(max_tokens * CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated] ..."


def _build_client(provider: str, api_key: str) -> Any:
    if provider in ("github", "openai"):
        from openai import OpenAI

        base_url = "https://models.inference.ai.azure.com" if provider == "github" else None
        return OpenAI(base_url=base_url, api_key=api_key)
    elif provider == "anthropic":
        from anthropic import Anthropic

        return Anthropic(api_key=api_key)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def _call(client: Any, provider: str, model: str, system: str, user: str) -> str:
    if provider in ("openai", "github"):
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "{}"
    elif provider == "anthropic":
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.1,
        )
        return response.content[0].text
    raise ValueError(f"Unsupported provider: {provider}")


class LLMEngine:
    def __init__(self, config: Config):
        self.config = config
        self.provider = config.llm_provider
        self.model = config.resolved_model
        self.critique_provider = config.resolved_critique_provider
        self.critique_model = config.resolved_critique_model
        self._client: Any = None
        self._critique_client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = _build_client(self.provider, self.config.llm_api_key)
        return self._client

    def _get_critique_client(self) -> Any:
        if self._critique_client is None:
            if self.critique_provider == self.provider:
                self._critique_client = self._get_client()
            else:
                self._critique_client = _build_client(
                    self.critique_provider, self.config.resolved_critique_api_key
                )
        return self._critique_client

    def _parse_findings(
        self, raw: str, lens_type: LensType, default_file: str,
    ) -> list[ReviewFinding]:
        try:
            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM JSON: %s", raw[:200])
            return []

        severity_map = {
            "critical": Severity.CRITICAL,
            "warning": Severity.WARNING,
            "suggestion": Severity.SUGGESTION,
            "info": Severity.INFO,
        }
        threshold = self.config.confidence_threshold

        findings = []
        for item in data.get("findings", []):
            confidence = item.get("confidence", 10)
            if isinstance(confidence, (int, float)) and confidence < threshold:
                logger.info("Dropping low-confidence (%s) finding: %s", confidence, item.get("message", "")[:60])
                continue

            severity_str = item.get("severity", "info").lower()
            file_path = item.get("file", default_file) or default_file
            findings.append(
                ReviewFinding(
                    file=file_path,
                    line=item.get("line"),
                    severity=severity_map.get(severity_str, Severity.INFO),
                    message=item.get("message", ""),
                    suggestion=item.get("suggestion"),
                    lens=lens_type,
                )
            )

        return findings

    def review_file(
        self,
        change: FileChange,
        lens_type: LensType,
        rules_text: str,
        pr_description: str = "",
        related_context: str = "",
        pr_change_summary: str = "",
    ) -> list[ReviewFinding]:
        """Review a single file with cross-file awareness via PR change summary."""
        prompt_template = LENS_PROMPTS.get(lens_type.value)
        if not prompt_template:
            return []

        diff = _truncate(change.diff_text, self.config.max_diff_tokens)
        full_file = _truncate(change.full_content or "", 8000) if change.full_content else "(not available)"
        context = _truncate(related_context, 2000) if related_context else "(none)"

        desc = pr_description or "(no description)"
        if pr_change_summary:
            desc += f"\n\nOTHER FILES CHANGED IN THIS PR:\n{pr_change_summary}"

        user_prompt = prompt_template.format(
            file_path=change.path,
            rules=rules_text,
            diff=diff,
            full_file=full_file,
            related_context=context,
            pr_description=desc,
            schema=FINDINGS_SCHEMA,
        )

        logger.info("Reviewing %s with %s lens (%d chars)", change.path, lens_type.value, len(user_prompt))

        try:
            raw = _call(
                self._get_client(), self.provider, self.model,
                SYSTEM_PROMPT, user_prompt,
            )
            findings = self._parse_findings(raw, lens_type, change.path)
            logger.info("  -> %d finding(s)", len(findings))
            return findings[:self.config.max_findings_per_lens]
        except Exception:
            logger.exception("LLM call failed for %s / %s", change.path, lens_type.value)
            return []

    def critique_findings(
        self,
        findings: list[ReviewFinding],
        diff_text: str,
        full_file: str = "",
        pr_description: str = "",
    ) -> list[ReviewFinding]:
        """Self-critique pass using a separate model to filter false positives."""
        if not findings:
            return []

        findings_json = json.dumps([f.to_dict() for f in findings], indent=2)
        diff = _truncate(diff_text, 12000)
        file_ctx = _truncate(full_file, 6000) if full_file else "(not available)"

        user_prompt = CRITIQUE_PROMPT.format(
            findings_json=findings_json,
            diff=diff,
            full_file=file_ctx,
            pr_description=pr_description or "(no description)",
        )

        try:
            raw = _call(
                self._get_critique_client(), self.critique_provider,
                self.critique_model, SYSTEM_PROMPT, user_prompt,
            )

            text = raw.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

            data = json.loads(text)
            kept_indices = set(data.get("keep", []))

            if not kept_indices:
                return []

            return [f for i, f in enumerate(findings) if i in kept_indices]
        except Exception:
            logger.debug("Critique pass failed, keeping all findings", exc_info=True)
            return findings
