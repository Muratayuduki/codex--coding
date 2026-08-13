---
name: agents-md-generator
description: Create or update repository-level AGENTS.md files for Codex. Use when the user asks to generate AGENTS.md, refresh repository instructions, consolidate local coding rules, remove duplicated global instructions, or convert repeated repository guidance into concise Codex rules.
---

# AGENTS.md Generator

Use this skill to create or update repository-local `AGENTS.md` files. Keep the result short, concrete, and specific to the repository.

## Workflow

1. Read existing repository guidance first.
   Check `AGENTS.md`, `CLAUDE.md`, `docs/`, `README.md`, package manifests, test configs, and obvious contribution docs.
2. Identify what belongs in the repository file.
   Keep repo-specific commands, architecture, tests, style rules, security constraints, and release workflow. Do not copy global Codex rules unless the repository needs a narrower override.
3. Read [references/agents-md-policy.md](references/agents-md-policy.md) before drafting or editing.
4. Write `AGENTS.md` in UTF-8.
   Prefer concise Japanese unless the repository already uses English instructions.
5. Verify the result.
   Confirm that paths, commands, package managers, and referenced docs actually exist.

## Rules

- Prefer existing local facts over generic best practices.
- Keep instructions action-oriented and testable.
- Delegate detailed procedures to skills or docs instead of embedding long runbooks.
- If local rules conflict, keep the more specific rule and state the conflict in the output.
- Do not mention Claude-specific global configuration when the task is scoped to Codex. Repository-local `CLAUDE.md` may be read only as project guidance.
