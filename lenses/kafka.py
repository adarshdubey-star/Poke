"""Kafka review lens — schema compatibility, topic usage, event contracts."""

from __future__ import annotations

from ..models import FileChange, LensType, ReviewFinding, Severity
from .base import ReviewLens


class KafkaLens(ReviewLens):
    lens_type = LensType.KAFKA

    def should_review(self, file_change: FileChange) -> bool:
        path = file_change.path
        kafka_paths = ("app/queue/", "lib/kafka", "inv_mq_service", "inv_export_service")
        if any(p in path for p in kafka_paths):
            return True
        content = file_change.added_content
        return any(kw in content for kw in ("kafka", "producer", "consumer", "EventProducer"))

    def _get_rules_section(self) -> dict | None:
        return self._rules.get("kafka")

    def _get_discovered_section(self) -> dict | None:
        topics = self._discovered.get("kafka_topics")
        if not topics:
            return None
        return {"discovered_kafka_topics": topics}

    @property
    def _known_topics(self) -> list[str]:
        """Get topic names from auto-discovery, with fallback."""
        discovered = self._discovered.get("kafka_topics", [])
        if discovered:
            return [t["name"] for t in discovered]
        return [
            "platform.inventory.host-ingress",
            "platform.inventory.events",
            "platform.notifications.ingress",
            "platform.inventory.host-apps",
        ]

    def pre_check(self, file_change: FileChange) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        content = file_change.added_content
        path = file_change.path

        # Check for event schema changes without spec updates
        event_files = ("app/queue/events.py", "app/models/schemas/outbox.py")
        if any(ef in path for ef in event_files):
            if "class " in content or "Schema" in content:
                findings.append(
                    ReviewFinding(
                        file=path,
                        line=None,
                        severity=Severity.WARNING,
                        message=(
                            "Event serialization schema modified. Verify swagger/host_events.spec.yaml "
                            "is also updated to match. platform.inventory.events is consumed by many "
                            "downstream services."
                        ),
                        suggestion="Cross-reference changes with swagger/host_events.spec.yaml.",
                        lens=LensType.KAFKA,
                    )
                )

        # Check for hardcoded topic names (auto-discovered from config.py)
        for topic in self._known_topics:
            if f'"{topic}"' in content or f"'{topic}'" in content:
                findings.append(
                    ReviewFinding(
                        file=path,
                        line=None,
                        severity=Severity.SUGGESTION,
                        message=f"Hardcoded topic name '{topic}'. Topics should come from config.py via Clowder.",
                        suggestion="Use the config variable from app/config.py instead of hardcoding topic names.",
                        lens=LensType.KAFKA,
                    )
                )
                break

        return findings
