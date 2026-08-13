---
name: bird
description: Use the bird CLI for read-only X/Twitter search when web searches, latest-news checks, reputation checks, outage checks, social reactions, or explicit X/Twitter searches need current social context.
homepage: https://bird.fast
metadata:
  openclaw:
    requires:
      bins:
        - bird
    install:
      - id: npm
        kind: node
        package: "@steipete/bird"
        bins:
          - bird
        label: "Install bird with npm"
---

# bird

Use `bird` to add read-only X/Twitter context to web research.

## When To Use

Use this skill when the user asks for any of the following:

- X/Twitter search, tweet search, SNS reactions, or social sentiment.
- Latest information, breaking news, outages, incidents, release reactions, or reputation checks where posts on X may add useful primary reactions.
- Web search tasks where X/Twitter results are explicitly requested.

For ordinary reference questions, use normal web search first. Add `bird search` only when current social context is relevant to the answer.

## Required Checks

Run this before the first X/Twitter query in a session:

```bash
bird check
```

If credentials are missing, fail fast and tell the user that `bird` needs one of these:

- An active browser login to `x.com` with readable cookies.
- `AUTH_TOKEN` and `CT0` environment variables.
- Explicit `--auth-token` and `--ct0` flags.

Do not invent X/Twitter results when `bird check` fails.

## Search Workflow

Use search-focused, read-only commands only.

```bash
bird search "<query>" -n 5 --json --plain
```

For focused searches, use X search operators in the query:

```bash
bird search "from:openai codex" -n 5 --json --plain
bird search "\"OpenAI Codex\" lang:ja" -n 5 --json --plain
```

Keep pagination conservative:

```bash
bird search "<query>" --all --max-pages 2 --delay 1000 --json --plain
```

When combining with web search:

- Present web sources and X/Twitter posts separately.
- Treat X/Twitter posts as social signals, not verified facts.
- Cite tweet URLs, authors, timestamps, and the query used when available.
- If web sources and X/Twitter posts conflict, say so directly and prefer primary web sources for factual claims.
- Never print cookies, `auth_token`, `ct0`, `AUTH_TOKEN`, or `CT0`.
- Avoid `--json-full` unless raw API fields are explicitly needed for a user-approved diagnostic.

## Allowed Commands

- `bird check`
- `bird whoami`
- `bird query-ids`
- `bird search`
- `bird read`
- `bird thread`
- `bird replies`
- `bird user-tweets`
- `bird news`
- `bird trending`

## Forbidden Commands

Do not run commands that write to X/Twitter or alter account state:

- `bird tweet`
- `bird reply`
- `bird follow`
- `bird unfollow`
- `bird unbookmark`
- `bird bookmarks`
- `bird likes`
- `bird home`
- `bird mentions`
- `bird following`
- `bird followers`

Do not use likes, follows, posting, replies, or bookmark changes with this skill.
Do not use account-private timeline, bookmark, like, mention, follower, or following commands for general web research.

## Troubleshooting

- If GraphQL query IDs are stale or a command returns a 404 from X internals, run `bird query-ids --fresh`, then retry once.
- If cookie extraction fails on Linux, ask the user to log in to `x.com` in Chrome/Chromium or provide `AUTH_TOKEN` and `CT0`.
- The installed npm package may be deprecated. Always verify behavior with `bird --help` and `bird check` before relying on it.
