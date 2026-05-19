"""Auto-discover codebase facts at runtime.

Scans the HBI codebase to extract live data (Kafka topics, partitioned tables,
API endpoints, auth patterns) so the agent's knowledge stays current without
manual rules.yaml updates.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (contains app/ and swagger/)."""
    candidate = Path(__file__).resolve().parent.parent.parent.parent
    if (candidate / "app").is_dir():
        return candidate
    # Fallback: use cwd (works in GitHub Actions after checkout)
    return Path.cwd()


def discover_kafka_topics(root: Path) -> list[dict[str, str]]:
    """Extract Kafka topic names and env vars from app/config.py."""
    config_path = root / "app" / "config.py"
    if not config_path.exists():
        logger.debug("app/config.py not found, skipping Kafka discovery")
        return []

    content = config_path.read_text()
    topics: list[dict[str, str]] = []
    seen: set[str] = set()

    # Match patterns like: os.environ.get("KAFKA_*_TOPIC", "platform.inventory.host-ingress")
    for match in re.finditer(
        r"""os\.environ\.get\(\s*["'](KAFKA_\w+_TOPIC|PAYLOAD_TRACKER_KAFKA_TOPIC)["']\s*,\s*["']([^"']+)["']\s*\)""",
        content,
    ):
        env_var = match.group(1)
        default_topic = match.group(2)
        if default_topic not in seen:
            seen.add(default_topic)
            topics.append({"name": default_topic, "env_var": env_var})

    # Also match self.*_topic = topic(os.environ.get("KAFKA_*"))
    for match in re.finditer(r"""self\.(\w+_topic)\s*=""", content):
        attr = match.group(1)
        # Already captured via defaults above, just log
        logger.debug("Found topic attribute: %s", attr)

    logger.info("Discovered %d Kafka topic(s) from app/config.py", len(topics))
    return topics


def discover_partitioned_tables(root: Path) -> list[dict[str, str]]:
    """Extract partitioned table names from migration files."""
    migrations_dir = root / "migrations" / "versions"
    if not migrations_dir.is_dir():
        logger.debug("migrations/versions/ not found, skipping partition discovery")
        return []

    tables: set[str] = set()

    for migration_file in migrations_dir.glob("*.py"):
        content = migration_file.read_text()
        if "TABLE_NUM_PARTITIONS" not in content and "partitioned_table_index_helper" not in content:
            continue

        # Extract table names from create/drop_partitioned_table_index calls
        for match in re.finditer(r"""(?:create|drop)_partitioned_table_index\(\s*[^,]*,\s*["'](\w+)["']""", content):
            tables.add(match.group(1))

        # Extract from table_name= keyword args
        for match in re.finditer(r"""table_name\s*=\s*["'](\w+)["']""", content):
            tables.add(match.group(1))

        # Extract from op.batch_alter_table / op.alter_column with partitioned references
        if "TABLE_NUM_PARTITIONS" in content:
            for match in re.finditer(r"""["'](hosts|system_profiles_\w+)["']""", content):
                tables.add(match.group(1))

    # Also check the helpers file for partition count
    helpers_path = root / "migrations" / "helpers.py"
    partition_count = "unknown"
    if helpers_path.exists():
        helpers_content = helpers_path.read_text()
        count_match = re.search(r"TABLE_NUM_PARTITIONS\s*=\s*int\(.+?,\s*(\d+)\)", helpers_content)
        if count_match:
            partition_count = count_match.group(1)

    result = [{"table": t, "default_partitions": partition_count} for t in sorted(tables)]
    logger.info("Discovered %d partitioned table(s) (default %s partitions)", len(result), partition_count)
    return result


def discover_api_endpoints(root: Path) -> list[dict[str, str]]:
    """Extract API endpoints and operationIds from swagger/openapi.json."""
    spec_path = root / "swagger" / "openapi.json"
    if not spec_path.exists():
        logger.debug("swagger/openapi.json not found, skipping API discovery")
        return []

    try:
        spec = json.loads(spec_path.read_text())
    except json.JSONDecodeError:
        logger.warning("Failed to parse openapi.json")
        return []

    endpoints: list[dict[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method in ("get", "post", "put", "patch", "delete"):
                op_id = details.get("operationId", "")
                summary = details.get("summary", "")
                endpoints.append({
                    "path": path,
                    "method": method.upper(),
                    "operationId": op_id,
                    "summary": summary,
                })

    logger.info("Discovered %d API endpoint(s) from openapi.json", len(endpoints))
    return endpoints


def discover_auth_decorators(root: Path) -> dict[str, list[str]]:
    """Discover which API files use @access vs @rbac decorators."""
    api_dir = root / "api"
    if not api_dir.is_dir():
        logger.debug("api/ not found, skipping auth discovery")
        return {}

    result: dict[str, list[str]] = {"access": [], "rbac": [], "neither": []}

    for py_file in api_dir.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name in ("spec.py", "metrics.py"):
            continue

        content = py_file.read_text()
        has_access = "@access" in content
        has_rbac = "@rbac" in content

        relative = str(py_file.relative_to(root))
        if has_access:
            result["access"].append(relative)
        elif has_rbac:
            result["rbac"].append(relative)
        else:
            # Only flag files with route functions
            if re.search(r"def\s+(get_|post_|put_|patch_|delete_|create_|update_|list_)", content):
                result["neither"].append(relative)

    logger.info(
        "Auth decorators: %d @access, %d @rbac, %d unprotected",
        len(result["access"]), len(result["rbac"]), len(result["neither"]),
    )
    return result


def discover_feature_flags(root: Path) -> list[str]:
    """Discover Unleash feature flag names used in the codebase."""
    flags: set[str] = set()

    for py_file in (root / "app").rglob("*.py"):
        content = py_file.read_text()
        for match in re.finditer(r"""(FLAG_\w+)""", content):
            flags.add(match.group(1))

    for py_file in (root / "lib").rglob("*.py"):
        content = py_file.read_text()
        for match in re.finditer(r"""(FLAG_\w+)""", content):
            flags.add(match.group(1))

    logger.info("Discovered %d feature flag(s)", len(flags))
    return sorted(flags)


def discover_all(repo_root: Path | None = None) -> dict:
    """Run all discovery passes and return a merged knowledge dict."""
    root = repo_root or _repo_root()
    logger.info("Auto-discovering codebase facts from %s", root)

    discovered = {
        "kafka_topics": discover_kafka_topics(root),
        "partitioned_tables": discover_partitioned_tables(root),
        "api_endpoints": discover_api_endpoints(root),
        "auth_decorators": discover_auth_decorators(root),
        "feature_flags": discover_feature_flags(root),
    }

    logger.info(
        "Discovery complete: %d topics, %d partitioned tables, %d endpoints, %d flags",
        len(discovered["kafka_topics"]),
        len(discovered["partitioned_tables"]),
        len(discovered["api_endpoints"]),
        len(discovered["feature_flags"]),
    )

    return discovered
