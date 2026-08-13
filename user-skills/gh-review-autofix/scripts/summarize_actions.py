#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from github_review_common import print_json

REVIEW_STATES = {"CHANGES_REQUESTED", "COMMENTED"}


def load_payload(path: str | None) -> dict[str, Any]:
    if path is None:
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_pr_author(author: dict[str, Any] | None, pr_author_login: str) -> bool:
    if author is None:
        return False
    return author["login"] == pr_author_login


def has_substantive_text(body: str) -> bool:
    return any(character.isalnum() for character in body)


def summarize_conversation_comments(payload: dict[str, Any], pr_author_login: str) -> list[dict[str, Any]]:
    actions = []
    pull_request = payload["pull_request"]
    for comment in payload["conversation_comments"]:
        actionable = not is_pr_author(comment["author"], pr_author_login)
        actions.append(
            {
                "category": "conversation_comment",
                "actionable": actionable,
                "reply_supported": True,
                "reply_channel": "pr_comment",
                "target_comment_id": comment["databaseId"],
                "target_comment_url": comment["url"],
                "reply_pr_number": pull_request["number"],
                "author": comment["author"]["login"],
                "body": comment["body"],
            }
        )
    return actions


def summarize_reviews(payload: dict[str, Any], pr_author_login: str) -> list[dict[str, Any]]:
    actions = []
    for review in payload["reviews"]:
        review_body = review["body"].strip()
        if not review_body or not has_substantive_text(review_body):
            continue
        actionable = (
            review["state"] in REVIEW_STATES and not is_pr_author(review["author"], pr_author_login)
        )
        actions.append(
            {
                "category": "review_body",
                "actionable": actionable,
                "reply_supported": False,
                "reply_channel": None,
                "target_comment_id": None,
                "target_comment_url": review["url"],
                "reply_pr_number": payload["pull_request"]["number"],
                "author": review["author"]["login"],
                "review_state": review["state"],
                "body": review_body,
            }
        )
    return actions


def select_latest_reviewer_comment(
    comments: list[dict[str, Any]],
    pr_author_login: str,
) -> dict[str, Any]:
    reviewer_comments = [comment for comment in comments if not is_pr_author(comment["author"], pr_author_login)]
    return (reviewer_comments or comments)[-1]


def summarize_review_threads(payload: dict[str, Any], pr_author_login: str) -> list[dict[str, Any]]:
    actions = []
    for thread in payload["review_threads"]:
        comments = thread["comments"]["nodes"] or []
        if not comments:
            continue
        latest_comment = select_latest_reviewer_comment(comments, pr_author_login)
        actionable = (
            not thread["isResolved"]
            and not thread["isOutdated"]
            and not is_pr_author(latest_comment["author"], pr_author_login)
        )
        actions.append(
            {
                "category": "review_thread",
                "actionable": actionable,
                "reply_supported": True,
                "reply_channel": "review_comment",
                "target_comment_id": latest_comment["databaseId"],
                "target_comment_url": latest_comment["url"],
                "reply_pr_number": payload["pull_request"]["number"],
                "author": latest_comment["author"]["login"],
                "body": latest_comment["body"],
                "path": thread["path"],
                "line": thread["line"],
                "is_resolved": thread["isResolved"],
                "is_outdated": thread["isOutdated"],
            }
        )
    return actions


def summarize(payload: dict[str, Any]) -> dict[str, Any]:
    pr_author_login = payload["pull_request"]["author"]["login"]
    actions = []
    actions.extend(summarize_conversation_comments(payload, pr_author_login))
    actions.extend(summarize_reviews(payload, pr_author_login))
    actions.extend(summarize_review_threads(payload, pr_author_login))
    return {
        "pull_request": payload["pull_request"],
        "counts": {
            "conversation_comments": len(payload["conversation_comments"]),
            "reviews": len(payload["reviews"]),
            "review_threads": len(payload["review_threads"]),
            "actions": len(actions),
            "actionable": sum(1 for action in actions if action["actionable"]),
        },
        "actions": actions,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize fetched PR review context into actions.")
    parser.add_argument("input", nargs="?", help="Path to fetch_review_context.py JSON output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print_json(summarize(load_payload(args.input)))


if __name__ == "__main__":
    main()
