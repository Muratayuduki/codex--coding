---
name: review
description: Review local code changes, staged diffs, unstaged diffs, or named files against repository guidelines and current behavior, then report findings as `[must]`, `[recommend]`, and `[nits]` with concrete evidence and fixes. Use when the user asks for a local code review, when a repository requires a `review` skill for self-review, or before commit after implementing changes. For GitHub PR review comments that need fetching, judging, replying, fixing, committing, or pushing, use `gh-review-autofix` instead.
---

# Review

Use this skill for local code review. Prefer changed lines and staged diffs over broad codebase review unless the user explicitly asks for a wider pass.

For GitHub PR review comments, do not use this skill as the primary workflow. Use `gh-review-autofix` to fetch review threads, judge comments, post replies, and implement accepted fixes.

## Workflow

1. Resolve the review scope first.
   Prefer `git diff --cached`, then `git diff`, then explicit files or ranges named by the user.
   If there is no diff and no explicit target, stop and ask what should be reviewed.
2. Read repository-specific rules before judging code.
   Search for `AGENTS.md`, `CLAUDE.md`, `CODING_GUIDELINE.md`, `PR_REVIEW_GUIDELINE.md`, `README.md`, and test-related docs that apply to the touched files.
3. Read only the minimal surrounding context needed to judge the changed code.
   Use `git diff --name-only`, `git diff --cached --name-only`, `rg`, `sed`, and `nl -ba` to keep the review evidence precise.
4. Apply the severity mapping and checklist from [references/review-policy.md](references/review-policy.md).
5. For non-trivial changes, use the parallel review workflow below when subagents are available and allowed by the current session.
6. Run the adversarial verification step below before finalizing findings.
7. Report findings first.
   Order them as `[must]`, `[recommend]`, `[nits]`.
8. When no findings remain, say that explicitly and mention any residual verification gaps such as tests not run.

## Review Rules

- Base every finding on repository evidence, current code behavior, or an explicit project rule.
- Prefer diff-focused review.
  Do not expand scope into unrelated files unless the change forces it.
- Be decisive.
  Do not write speculative review comments. If context is missing, state the exact missing context.
- Keep findings actionable.
  Every finding must include the file, line, problem, reason, and a concrete fix direction.
- Respect existing patterns.
  When judging style or design, verify the local convention first.

## Parallel Review

Use this only when the current session allows subagents and the scope is large enough to benefit from independent passes.

Use the `reviewer_deep` custom agent profile for every delegated review pass. Do not substitute an unnamed or default agent that inherits the parent model.

Run focused reviewers in parallel by concern:

- Correctness and regressions
- Security and secret handling
- Performance and resource usage
- Repository rules, tests, docs, and release workflow

Each reviewer must cite exact files, lines, commands, and local rules. Merge duplicate findings by evidence, not by vote count.

## Adversarial Verification

Before reporting, perform a second pass that tries to disprove each finding.

- Drop findings that depend on assumptions not supported by code, tests, docs, or local rules.
- Downgrade severity only when the evidence proves the release risk is lower.
- Keep `[must]` when the behavior is broken, a required check is missing, or a documented rule is violated.
- Record any remaining uncertainty as `前提・未確認事項`, not as a speculative finding.

## Output Format

Produce the review in Japanese with findings first.

```text
[must] path/to/file.ext:123
問題点を簡潔に記載
根拠: 参照した規約名、実装、テスト、差分の事実
修正案: 最小変更での修正方針
```

- `[must]`:
  バグ、仕様逸脱、セキュリティ問題、回帰、明確な規約違反、必須テスト不足
- `[recommend]`:
  保守性、可読性、設計整理、追加テスト提案など
- `[nits]`:
  軽微な命名、コメント、表記ゆれなど

After the findings, include only these optional sections when needed:

- `前提・未確認事項`
- `短い総括`

## Self-Review Loop

Use this loop when the skill is invoked as part of your own implementation work:

1. Review the current diff.
2. Fix every `[must]`.
3. Run the narrowest relevant validation.
4. Review the updated diff again.
5. Do not treat the work as complete until `[must]` is 0.

## Validation Expectations

- Do not hide failing checks.
- Do not recommend skipping or deleting failing tests to make the review pass.
- If a rule conflict exists, cite both sources and explain which one is more specific.
