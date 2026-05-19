"""GitHub API client for submitting PR reviews."""

from __future__ import annotations

import json
import logging
import subprocess

from .config import Config

logger = logging.getLogger(__name__)


def _gh_api(method: str, endpoint: str, data: dict | None = None) -> dict | list:
    """Call the GitHub API via the gh CLI."""
    cmd = ["gh", "api", "-X", method, endpoint]

    if data is not None:
        cmd.extend(["-H", "Accept: application/vnd.github+json", "--input", "-"])
        result = subprocess.run(
            cmd,
            input=json.dumps(data),
            capture_output=True,
            text=True,
        )
    else:
        cmd.extend(["-H", "Accept: application/vnd.github+json"])
        result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        detail = result.stderr.strip()
        # Try to extract a more useful message from the JSON response
        try:
            err_body = json.loads(result.stdout)
            if "message" in err_body:
                detail = err_body["message"]
            if "errors" in err_body:
                detail += " — " + "; ".join(
                    e.get("message", str(e)) for e in err_body["errors"]
                )
        except (json.JSONDecodeError, TypeError):
            pass
        logger.error("GitHub API error: %s", detail)
        raise RuntimeError(detail)

    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def get_pr_info(pr_number: int) -> dict:
    """Fetch basic PR metadata."""
    result = subprocess.run(
        [
            "gh", "pr", "view", str(pr_number),
            "--json", "number,title,author,headRefOid,baseRefName,headRefName,isDraft",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def get_repo_name() -> str:
    """Get the current repo in owner/name format."""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def submit_review(config: Config, review_payload: dict) -> None:
    """Submit a PR review via the GitHub API.

    review_payload should have keys: body, event, comments
    """
    repo = config.repo or get_repo_name()
    endpoint = f"/repos/{repo}/pulls/{config.pr_number}/reviews"

    # GitHub's create-review API expects commit_id for inline comments
    pr_info = get_pr_info(config.pr_number)
    commit_id = pr_info.get("headRefOid", "")

    payload: dict = {
        "body": review_payload["body"],
        "event": review_payload["event"],
        "commit_id": commit_id,
    }

    comments = review_payload.get("comments", [])
    if comments:
        # GitHub API expects 'side' for multi-line diffs
        api_comments = []
        for c in comments:
            api_comment: dict = {
                "path": c["path"],
                "body": c["body"],
                "side": "RIGHT",
            }
            if c.get("line"):
                api_comment["line"] = c["line"]
            api_comments.append(api_comment)
        payload["comments"] = api_comments

    logger.info("Submitting review to %s PR #%d (%d inline comments)", repo, config.pr_number, len(comments))

    _gh_api("POST", endpoint, payload)

    logger.info("Review submitted successfully")


def post_comment(config: Config, body: str) -> None:
    """Post a standalone comment on the PR (not a review)."""
    repo = config.repo or get_repo_name()
    endpoint = f"/repos/{repo}/issues/{config.pr_number}/comments"
    _gh_api("POST", endpoint, {"body": body})


def dismiss_stale_reviews(config: Config, bot_login: str) -> None:
    """Dismiss previous reviews by this bot to avoid clutter."""
    repo = config.repo or get_repo_name()
    endpoint = f"/repos/{repo}/pulls/{config.pr_number}/reviews"

    try:
        reviews = _gh_api("GET", endpoint)
        if not isinstance(reviews, list):
            return

        for review in reviews:
            if review.get("user", {}).get("login") == bot_login and review.get("state") in (
                "COMMENTED",
                "CHANGES_REQUESTED",
            ):
                review_id = review["id"]
                dismiss_endpoint = f"{endpoint}/{review_id}/dismissals"
                _gh_api("PUT", dismiss_endpoint, {"message": "Superseded by new review"})
                logger.info("Dismissed stale review %d", review_id)
    except Exception:
        logger.debug("Could not dismiss stale reviews (may lack permissions)", exc_info=True)
