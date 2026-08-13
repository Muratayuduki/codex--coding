#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from typing import Any

from github_review_common import ensure_gh_authenticated, run_json_command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Post a GitHub review reply or PR comment.")
    parser.add_argument("--repo", required=True, help="Repository in owner/repo format")
    parser.add_argument("--pr-number", required=True, type=int, help="Pull request number")
    parser.add_argument(
        "--mode",
        required=True,
        choices=("review-comment", "pr-comment"),
        help="review-comment replies to an inline review comment; pr-comment posts a PR top-level comment",
    )
    parser.add_argument("--comment-id", type=int, help="Target inline review comment ID")
    parser.add_argument("--source-url", help="Original comment URL for PR top-level comment replies")
    parser.add_argument("--body", required=True, help="Reply body")
    parser.add_argument("--dry-run", action="store_true", help="Print payload instead of posting")
    args = parser.parse_args()

    if args.mode == "review-comment" and args.comment_id is None:
        parser.error("--comment-id is required when --mode=review-comment")
    return args


def split_repo(repo_full_name: str) -> tuple[str, str]:
    owner, repo = repo_full_name.split("/", 1)
    return owner, repo


def build_payload(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    owner, repo = split_repo(args.repo)
    if args.mode == "review-comment":
        endpoint = f"repos/{owner}/{repo}/pulls/{args.pr_number}/comments/{args.comment_id}/replies"
        body = args.body
    else:
        endpoint = f"repos/{owner}/{repo}/issues/{args.pr_number}/comments"
        body = args.body
        if args.source_url:
            body = f"Replying to {args.source_url}\n\n{body}"
    return endpoint, {"body": body}


def post_reply(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    return run_json_command(
        ["gh", "api", endpoint, "--method", "POST", "--input", "-"],
        stdin=json.dumps(payload),
    )


def main() -> None:
    args = parse_args()
    endpoint, payload = build_payload(args)
    if args.dry_run:
        print(json.dumps({"endpoint": endpoint, "payload": payload}, indent=2, ensure_ascii=False))
        return
    ensure_gh_authenticated()
    response = post_reply(endpoint, payload)
    print(json.dumps(response, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
