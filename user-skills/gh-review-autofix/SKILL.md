---
name: gh-review-autofix
description: Inspect a GitHub pull request URL with `gh`, fetch PR review bodies, inline review threads, and PR conversation comments, judge whether each review request is actually correct, reply with concrete reasons, implement accepted fixes, and commit/push the result. Use when Codex is asked to review a PR URL end-to-end, such as "review this PR URL", "PRのレビュー指摘を見て妥当なものだけ直して", or "gh で review comment を取って判断して返信して".
---

# GH Review Autofix

Use this skill when the user gives a GitHub PR URL and wants an end-to-end review-response workflow driven by `gh`.

For local diffs, staged changes, unstaged changes, or named files without GitHub PR review comments, use the `review` skill instead.

## Workflow

1. Resolve the PR from the provided URL.
   Run `python scripts/fetch_review_context.py <pr-url>` to fetch review bodies, inline review threads, and PR conversation comments into one JSON payload.
2. Normalize the review inputs.
   Run `python scripts/summarize_actions.py <payload.json>` to convert raw review data into actionable units.
3. Judge each item with code evidence first.
   Read [references/decision-policy.md](references/decision-policy.md) before deciding whether to accept or reject a comment.
4. Reply with reasons.
   Read [references/reply-policy.md](references/reply-policy.md) before posting anything.
   Use `python scripts/post_review_reply.py` for actual posting or `--dry-run` to inspect the payload first.
5. Implement only accepted feedback.
   Keep changes minimal, align with existing style, and fail fast instead of adding fallbacks.
6. Validate before posting the final outcome.
   Run only the checks that directly support the accepted fixes. Do not hide failures, skip tests, or delete failing tests.
7. Commit and push only after the accepted fixes and replies are ready.
   Use one commit per PR unless the user explicitly asks otherwise.

## Judgment Rules

- Prefer repository evidence over reviewer preference.
- Accept a comment when the current code, tests, diff, or documented conventions support it.
- Reject a comment when it conflicts with actual repository behavior, duplicates an already-addressed point, or asks for a broader product decision that is not settled.
- Mark an item as needing clarification instead of guessing when the request would change product behavior, architecture, or requirements beyond the visible PR scope.
- Treat resolved or outdated inline threads as context, not as new actionable work.
- Review-body comments are judgment targets, but do not reply to them directly with this skill.

## Reply Rules

- Inline review threads:
  Reply on the specific review comment with a short judgment, reason, action, and validation result.
- PR conversation comments:
  Post a new PR top-level comment that references the source comment URL and explains the judgment.
- Review bodies:
  Do not reply directly. If the same issue also appears in inline comments, answer there instead.
- Always dry-run the payload first when the user did not explicitly ask for immediate write actions.

## Scripts

- `scripts/fetch_review_context.py`
  Fetch the PR review context from a PR URL with `gh api graphql`.
- `scripts/summarize_actions.py`
  Convert raw review payloads into actionable items and annotate how each item can be answered.
- `scripts/post_review_reply.py`
  Post an inline review reply or a PR top-level comment, or emit the exact payload with `--dry-run`.

## References

- [references/decision-policy.md](references/decision-policy.md)
  Use for accept / reject / clarify decisions.
- [references/reply-policy.md](references/reply-policy.md)
  Use for reply formatting and channel selection.

## 出力ルール
コメントなど返信に用いる言語は必ずUTF-8の日本語を用いること
