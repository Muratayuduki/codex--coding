# Review Policy

## Evidence Priority

1. Repository-local instructions and guidelines
2. Current diff and surrounding implementation
3. Existing tests and documented behavior
4. General engineering principles

Never let a general preference override a project-specific documented rule.

## Scope Selection

Review targets in this order:

1. `git diff --cached`
2. `git diff`
3. Files, directories, or code snippets explicitly named by the user

If the user asks for a review of a PR or commit range, use that exact scope instead.

## Severity Mapping

### `[must]`

Use `[must]` when the change has a concrete correctness or release risk.

- Broken behavior, runtime error, compile error, or failing requirement
- Security issue or secrets exposure
- Clear regression against existing behavior or tests
- Fail Fast violation that hides an error without explicit justification
- Missing validation that is required to prove the change is complete
- Documented rule violation in repo instructions

### `[recommend]`

Use `[recommend]` when the code can ship but should be improved.

- Readability or maintainability issues
- Non-trivial duplication
- Design that is broader than necessary
- Missing non-essential edge-case coverage
- Naming or structure that slows review and future edits

### `[nits]`

Use `[nits]` only for low-impact polish.

- Small naming improvements
- Comment wording
- Formatting or local consistency tweaks

Do not downgrade a real bug into `[recommend]` or `[nits]`.

## Checklist

Review the diff against these categories when relevant:

1. Correctness and regressions
2. Security and secret handling
3. Error handling and Fail Fast behavior
4. Edge cases and boundary values
5. Performance and unnecessary complexity
6. DRY, KISS, YAGNI, SOLID, and consistency with existing patterns
7. Testability and validation coverage
8. Comments, docs, config, and schema alignment

Only report a category when the diff actually presents evidence for it.

## Parallel Review Policy

When subagents are available and allowed, split non-trivial reviews into independent passes for:

- correctness and regressions
- security and secret handling
- performance and resource usage
- repository rules, tests, docs, and release workflow

Combine results only after each pass cites concrete evidence. Do not count duplicate reports as stronger evidence.

## Adversarial Verification

Before final output, try to disprove every finding:

- Is the cited line actually in the active path?
- Does existing behavior or a local rule already justify the change?
- Is the finding based on a real failure mode rather than preference?
- Is the severity consistent with the release risk?

Remove findings that fail this verification. Keep uncertainty in `前提・未確認事項`.

## Hook and Memo Guard Changes

When reviewing `codex_memo_guard.py`, hook configuration, or `memo.md` enforcement, treat these as required checks:

- `stop`, `summarize`, and `pre-commit` modes still agree on memo path resolution and marker validation.
- `stop` never blocks the turn and finishes within its timeout; heavy work stays in the detached `summarize` child.
- Plan Mode and subagent payloads still bypass memo writes.
- The summarizer `codex exec` call keeps recursion prevention (`-c features.hooks=false`) and `--ephemeral`.
- The per-turn state file prevents double spawn and double append; appending is also idempotent via the `done` marker check.
- Change-turn classification keeps unknown commands and unknown tools on the change side, and `#nomemo` always wins over `#memo`.
- Summarizer failures are raised and land in the per-turn log with a `failed:` state; they are never silently swallowed.
- `config.toml` hook entries match the script path, timeout, and enabled state, and `[hooks.state]` entries match the registered hooks.
- Tests cover any changed marker, state, classification, or memo path behavior.

Use `[must]` when a hook change blocks the main turn, breaks recursion prevention, lets a failed summary look successful, disables the configured hook by accident, or lacks tests for changed enforcement behavior.

## Finding Requirements

Every finding must include:

- severity
- file path and line reference
- concrete problem statement
- evidence source
- minimal fix direction

Avoid vague comments such as `気になる`, `可能性があります`, or `検討してください`.

## No-Finding Case

If no findings exist, say so explicitly.

Recommended wording:

```text
指摘はありません。
未実施の確認: <あれば記載。なければ「なし」>
```

## Self-Review Completion Rule

When reviewing your own implementation, the review is complete only when:

- `[must]` is 0
- accepted fixes are reflected in the diff
- relevant checks were run or the exact missing check is stated
