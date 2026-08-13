#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any

PR_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)(?:/.*)?$"
)


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def run_command(cmd: list[str], stdin: str | None = None) -> str:
    completed = subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(cmd)}\n{completed.stderr.strip()}"
        )
    return completed.stdout


def run_json_command(cmd: list[str], stdin: str | None = None) -> dict[str, Any]:
    output = run_command(cmd, stdin=stdin)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON output: {exc}\nRaw output:\n{output}") from exc


def ensure_gh_authenticated() -> None:
    run_command(["gh", "auth", "status"])


def parse_pr_url(pr_url: str) -> PullRequestRef:
    match = PR_URL_PATTERN.match(pr_url.strip())
    if match is None:
        raise ValueError(
            "PR URL must match https://github.com/<owner>/<repo>/pull/<number>"
        )
    return PullRequestRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(match.group("number")),
    )


def gh_api_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    cmd = ["gh", "api", "graphql", "-F", "query=@-"]
    for key, value in variables.items():
        if value is None:
            continue
        cmd.extend(["-F", f"{key}={value}"])
    return run_json_command(cmd, stdin=query)


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
