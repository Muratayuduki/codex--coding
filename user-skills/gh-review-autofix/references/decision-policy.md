# Decision Policy

Use these rules when deciding whether a review comment should be implemented.

## Accept

- The comment points to a real bug, inconsistency, missing test, or mismatch with established repository conventions.
- The current diff clearly shows the reviewer is correct.
- The fix stays within the PR's stated scope and does not require a product or architecture decision outside the visible context.

## Reject

- The current implementation already satisfies the requested behavior.
- The reviewer is asking for a subjective rewrite without repository evidence.
- The comment is duplicated elsewhere and has already been addressed.
- The suggested change would create a regression, hide an error, or violate fail-fast behavior.

## Need Clarification

- The request changes product requirements, data contracts, or security posture.
- The reviewer comment is ambiguous enough that multiple incompatible fixes are plausible.
- The PR does not contain enough context to prove the change is correct.

## Evidence Order

1. Current code and diff
2. Existing tests
3. Local docs and explicit repository rules
4. External official documentation only when the point depends on a third-party contract

## Response Discipline

- State the judgment explicitly: `accept`, `reject`, or `clarify`.
- Cite the concrete file, test, diff, or external contract that justifies the judgment.
- Never use vague language such as "might" or "maybe" in the final reviewer reply.
