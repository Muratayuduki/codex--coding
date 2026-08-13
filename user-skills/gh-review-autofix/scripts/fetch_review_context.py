#!/usr/bin/env python3

from __future__ import annotations

import argparse
from typing import Any

from github_review_common import ensure_gh_authenticated, gh_api_graphql, parse_pr_url, print_json

QUERY = """\
query(
  $owner: String!,
  $repo: String!,
  $number: Int!,
  $commentsCursor: String,
  $reviewsCursor: String,
  $threadsCursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number
      title
      url
      state
      baseRefName
      headRefName
      isCrossRepository
      author { login }
      headRepositoryOwner { login }
      headRepository { name }
      comments(first: 100, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          body
          createdAt
          updatedAt
          url
          author { login }
        }
      }
      reviews(first: 100, after: $reviewsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          state
          body
          submittedAt
          url
          author { login }
        }
      }
      reviewThreads(first: 100, after: $threadsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          diffSide
          startLine
          startDiffSide
          originalLine
          originalStartLine
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              databaseId
              body
              createdAt
              updatedAt
              url
              author { login }
            }
          }
        }
      }
    }
  }
}
"""

THREAD_COMMENTS_QUERY = """\
query(
  $threadId: ID!,
  $commentsCursor: String
) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          body
          createdAt
          updatedAt
          url
          author { login }
        }
      }
    }
  }
}
"""


def fetch_all(pr_url: str) -> dict[str, Any]:
    pr_ref = parse_pr_url(pr_url)
    conversation_comments: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    review_threads: list[dict[str, Any]] = []

    comments_cursor: str | None = None
    reviews_cursor: str | None = None
    threads_cursor: str | None = None
    comments_done = False
    reviews_done = False
    threads_done = False
    pull_request: dict[str, Any] | None = None

    while not (comments_done and reviews_done and threads_done):
        payload = gh_api_graphql(
            QUERY,
            {
                "owner": pr_ref.owner,
                "repo": pr_ref.repo,
                "number": pr_ref.number,
                "commentsCursor": comments_cursor,
                "reviewsCursor": reviews_cursor,
                "threadsCursor": threads_cursor,
            },
        )
        if payload.get("errors"):
            raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")

        pull_request_data = payload["data"]["repository"]["pullRequest"]
        if pull_request_data is None:
            raise RuntimeError(f"Pull request not found: {pr_url}")

        if pull_request is None:
            pull_request = {
                "number": pull_request_data["number"],
                "title": pull_request_data["title"],
                "url": pull_request_data["url"],
                "state": pull_request_data["state"],
                "base_ref_name": pull_request_data["baseRefName"],
                "head_ref_name": pull_request_data["headRefName"],
                "is_cross_repository": pull_request_data["isCrossRepository"],
                "author": pull_request_data["author"],
                "base_repository": {
                    "owner": pr_ref.owner,
                    "name": pr_ref.repo,
                },
                "head_repository_owner": pull_request_data["headRepositoryOwner"],
                "head_repository": pull_request_data["headRepository"],
            }

        comments = pull_request_data["comments"]
        review_nodes = pull_request_data["reviews"]
        threads = pull_request_data["reviewThreads"]

        if not comments_done:
            conversation_comments.extend(comments["nodes"] or [])
            comments_done = not comments["pageInfo"]["hasNextPage"]
            comments_cursor = None if comments_done else comments["pageInfo"]["endCursor"]

        if not reviews_done:
            reviews.extend(review_nodes["nodes"] or [])
            reviews_done = not review_nodes["pageInfo"]["hasNextPage"]
            reviews_cursor = None if reviews_done else review_nodes["pageInfo"]["endCursor"]

        if not threads_done:
            review_threads.extend(fetch_complete_review_threads(threads["nodes"] or []))
            threads_done = not threads["pageInfo"]["hasNextPage"]
            threads_cursor = None if threads_done else threads["pageInfo"]["endCursor"]

    return {
        "pull_request": pull_request,
        "conversation_comments": conversation_comments,
        "reviews": reviews,
        "review_threads": review_threads,
    }


def fetch_complete_review_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for thread in threads:
        comments = thread["comments"]
        cursor = comments["pageInfo"]["endCursor"]
        while comments["pageInfo"]["hasNextPage"]:
            payload = gh_api_graphql(
                THREAD_COMMENTS_QUERY,
                {
                    "threadId": thread["id"],
                    "commentsCursor": cursor,
                },
            )
            if payload.get("errors"):
                raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")

            node = payload["data"]["node"]
            if node is None:
                raise RuntimeError(f"Review thread not found: {thread['id']}")

            next_comments = node["comments"]
            comments["nodes"].extend(next_comments["nodes"] or [])
            comments["pageInfo"] = next_comments["pageInfo"]
            cursor = comments["pageInfo"]["endCursor"]
    return threads


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch GitHub PR review context from a PR URL.")
    parser.add_argument("pr_url", help="GitHub PR URL")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_gh_authenticated()
    print_json(fetch_all(args.pr_url))


if __name__ == "__main__":
    main()
