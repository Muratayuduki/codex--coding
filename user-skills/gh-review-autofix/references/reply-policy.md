# Reply Policy

Use these rules before posting to GitHub.

## Inline Review Comment Reply

Reply to the exact review comment with this structure:

```text
Judgment: accept|reject|clarify
Reason: <repository-backed reason>
Action: <fixed | no code change | need clarification>
Validation: <test/check name or "not run">
```

Keep the reply short and specific. Mention the changed file or check only when it materially supports the judgment.

## PR Conversation Comment Reply

PR conversation comments are not threaded like inline review comments. Post a new PR top-level comment with this structure:

```text
Replying to <source-comment-url>

Judgment: accept|reject|clarify
Reason: <repository-backed reason>
Action: <fixed | no code change | need clarification>
Validation: <test/check name or "not run">
```

## Review Body

- Judge review bodies, but do not reply directly from this skill.
- If the same issue appears in inline comments, answer on the inline thread.

## Dry Run First

- Use `scripts/post_review_reply.py --dry-run` before write actions unless the user explicitly requests immediate posting.
- Do not post placeholder text.
