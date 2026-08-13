from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

import fetch_review_context


def connection(nodes: list[dict[str, Any]], has_next_page: bool, end_cursor: str | None) -> dict[str, Any]:
    return {
        "nodes": nodes,
        "pageInfo": {
            "hasNextPage": has_next_page,
            "endCursor": end_cursor,
        },
    }


def payload(
    comments: dict[str, Any],
    reviews: dict[str, Any],
    threads: dict[str, Any],
) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "number": 123,
                    "title": "Review me",
                    "url": "https://github.com/o/r/pull/123",
                    "state": "OPEN",
                    "baseRefName": "main",
                    "headRefName": "feature",
                    "isCrossRepository": False,
                    "author": {"login": "author"},
                    "headRepositoryOwner": {"login": "author"},
                    "headRepository": {"name": "r"},
                    "comments": comments,
                    "reviews": reviews,
                    "reviewThreads": threads,
                }
            }
        }
    }


def thread(
    thread_id: str,
    comments: list[dict[str, Any]],
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "comments": connection(comments, has_next_page, end_cursor),
    }


def thread_comments_payload(
    comments: list[dict[str, Any]],
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "data": {
            "node": {
                "comments": connection(comments, has_next_page, end_cursor),
            }
        }
    }


class FetchReviewContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original_parse_pr_url = fetch_review_context.parse_pr_url
        self.original_gh_api_graphql = fetch_review_context.gh_api_graphql
        fetch_review_context.parse_pr_url = lambda _url: SimpleNamespace(
            owner="o",
            repo="r",
            number=123,
        )

    def tearDown(self) -> None:
        fetch_review_context.parse_pr_url = self.original_parse_pr_url
        fetch_review_context.gh_api_graphql = self.original_gh_api_graphql

    def test_completed_connections_are_not_extended_again(self) -> None:
        responses = [
            payload(
                connection([{"id": "comment-1"}], False, None),
                connection([{"id": "review-1"}], True, "review-cursor-1"),
                connection([thread("thread-1", [{"id": "thread-comment-1"}])], False, None),
            ),
            payload(
                connection([{"id": "comment-1"}], False, None),
                connection([{"id": "review-2"}], False, None),
                connection([thread("thread-1", [{"id": "thread-comment-1"}])], False, None),
            ),
        ]
        calls: list[dict[str, Any]] = []

        def fake_graphql(_query: str, variables: dict[str, Any]) -> dict[str, Any]:
            calls.append(variables)
            return responses.pop(0)

        fetch_review_context.gh_api_graphql = fake_graphql
        result = fetch_review_context.fetch_all("https://github.com/o/r/pull/123")

        self.assertEqual([item["id"] for item in result["conversation_comments"]], ["comment-1"])
        self.assertEqual([item["id"] for item in result["reviews"]], ["review-1", "review-2"])
        self.assertEqual([item["id"] for item in result["review_threads"]], ["thread-1"])
        self.assertEqual(
            [item["id"] for item in result["review_threads"][0]["comments"]["nodes"]],
            ["thread-comment-1"],
        )
        self.assertEqual(calls[1]["reviewsCursor"], "review-cursor-1")

    def test_single_page_fetch_returns_each_connection_once(self) -> None:
        fetch_review_context.gh_api_graphql = lambda _query, _variables: payload(
            connection([{"id": "comment-1"}], False, None),
            connection([{"id": "review-1"}], False, None),
            connection([thread("thread-1", [{"id": "thread-comment-1"}])], False, None),
        )

        result = fetch_review_context.fetch_all("https://github.com/o/r/pull/123")

        self.assertEqual(len(result["conversation_comments"]), 1)
        self.assertEqual(len(result["reviews"]), 1)
        self.assertEqual(len(result["review_threads"]), 1)

    def test_thread_comments_fetches_additional_pages(self) -> None:
        responses = [
            payload(
                connection([], False, None),
                connection([], False, None),
                connection(
                    [
                        thread(
                            "thread-1",
                            [{"id": "thread-comment-1"}],
                            True,
                            "thread-comment-cursor-1",
                        )
                    ],
                    False,
                    None,
                ),
            ),
            thread_comments_payload([{"id": "thread-comment-2"}]),
        ]
        calls: list[dict[str, Any]] = []

        def fake_graphql(_query: str, variables: dict[str, Any]) -> dict[str, Any]:
            calls.append(variables)
            return responses.pop(0)

        fetch_review_context.gh_api_graphql = fake_graphql
        result = fetch_review_context.fetch_all("https://github.com/o/r/pull/123")

        self.assertEqual(
            [item["id"] for item in result["review_threads"][0]["comments"]["nodes"]],
            ["thread-comment-1", "thread-comment-2"],
        )
        self.assertEqual(calls[1]["threadId"], "thread-1")
        self.assertEqual(calls[1]["commentsCursor"], "thread-comment-cursor-1")


if __name__ == "__main__":
    unittest.main()
