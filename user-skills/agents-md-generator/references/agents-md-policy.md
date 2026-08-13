# AGENTS.md Policy

## Include

- Repository purpose and important directory layout when it changes day-to-day work.
- Build, test, lint, format, migration, and local development commands that are actually present.
- Language, framework, package manager, and runtime constraints that differ from global defaults.
- Coding conventions that are visible in current code or documented locally.
- Test expectations, acceptance checks, and review workflow that the repository requires.
- Secrets, deployment, data, or destructive-operation cautions that affect implementation safety.
- Memo or planning rules only when the repository has a specific file path or format.

## Exclude

- Global Codex rules that already live in `~/.codex/AGENTS.md`.
- Long examples that belong in a skill, README, or docs page.
- Tool installation manuals unless the repository itself requires a nonstandard setup.
- Aspirational best practices with no local evidence.
- Old Claude global configuration. Read repository-local `CLAUDE.md` only as project guidance when no `AGENTS.md` equivalent exists.

## Conflict Handling

- Prefer `AGENTS.md` closest to the edited file.
- Prefer documented repository commands over inferred commands.
- Prefer current manifests and config files over stale prose.
- If two local docs conflict and neither is clearly more specific, do not guess. Ask for the intended rule or record the conflict in the output.

## Recommended Shape

Use short sections. Avoid nesting unless the repository has multiple independent packages.

```markdown
# AGENTS.md

## Project
- <purpose and important boundaries>

## Commands
- `<command>`: <when to run it>

## Coding Rules
- <repo-specific rule>

## Testing
- <required checks>

## Notes
- <security, deployment, memo, or review constraints>
```

## Verification

Before finishing, verify these facts from the repository:

- Mentioned files and directories exist.
- Commands match package scripts, Makefile targets, task files, or documented tooling.
- Generated instructions do not duplicate global Codex guidance.
- The file is concise enough to be read every turn.
