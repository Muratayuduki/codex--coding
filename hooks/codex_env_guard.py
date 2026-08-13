#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROTECTED_PREFIXES = (".env", ".envrc", ".dev.vars")
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")
CODEX_ALLOWED_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "bootstrap.sh",
    "config.toml",
}
CODEX_ALLOWED_DIRECTORIES = (
    "agents",
    "git-hooks",
    "hooks",
    "rules",
    "tests",
    "user-skills",
)
CREDENTIAL_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "OpenAI/Anthropic secret key",
        re.compile(rb"\bsk-(?:proj-|svcacct-|ant-[A-Za-z0-9_-]*-)?[A-Za-z0-9_-]{20,}\b"),
    ),
    (
        "GitHub token",
        re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("GitLab token", re.compile(rb"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Google API key", re.compile(rb"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Stripe live secret", re.compile(rb"\bsk_live_[A-Za-z0-9]{20,}\b")),
    ("private key", re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
)
GUARDED_FILE_COMMANDS = {
    ".",
    "awk",
    "cat",
    "chmod",
    "chown",
    "code",
    "cp",
    "cursor",
    "emacs",
    "head",
    "install",
    "jq",
    "less",
    "ln",
    "more",
    "mv",
    "nano",
    "nvim",
    "perl",
    "python",
    "python3",
    "rm",
    "sed",
    "source",
    "tail",
    "tee",
    "touch",
    "truncate",
    "vi",
    "vim",
    "yq",
    "add-content",
    "clear-content",
    "copy-item",
    "get-content",
    "move-item",
    "new-item",
    "out-file",
    "remove-item",
    "rename-item",
    "set-content",
}
GUARDED_GIT_SUBCOMMANDS = {"add", "commit", "mv", "rm"}
SHELL_BREAKS = {";", "&&", "||", "|"}
REDIRECT_OPERATORS = {">", ">>", "<", "<<", "<>", ">|"}
WRAPPER_COMMANDS = {"builtin", "command", "noglob", "sudo", "time"}
PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (?P<path>.+)$")
PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to: (?P<path>.+)$")
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
ATTACHED_REDIRECT_RE = re.compile(r"^(?:\d+)?(?:>>?|<<?|<>|>\|)(?P<path>.+)$")
SHORT_COMMIT_BOOLEAN_OPTIONS = frozenset("apsevnqioz")
SHORT_COMMIT_VALUE_OPTIONS = frozenset("mFcCt")
SHORT_COMMIT_ATTACHED_VALUE_OPTIONS = frozenset("Su")
LONG_COMMIT_VALUE_OPTIONS = frozenset(
    {
        "--author",
        "--cleanup",
        "--date",
        "--file",
        "--fixup",
        "--message",
        "--pathspec-from-file",
        "--reedit-message",
        "--reuse-message",
        "--squash",
        "--template",
        "--trailer",
    }
)


@dataclass(frozen=True)
class BlockReason:
    paths: tuple[str, ...]
    operation: str

    def message(self) -> str:
        path_list = ", ".join(self.paths)
        if self.operation == "git commit hook bypass":
            return (
                f"env guard: git commit hook bypass blocked: {path_list}. "
                "Pre-commit検査を無効化しないでください。"
            )
        return (
            f"env guard: protected env file operation blocked ({self.operation}): {path_list}. "
            "実秘密ファイルではなく .env.example などのテンプレートを使ってください。"
        )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: codex_env_guard.py pre-tool-use|pre-commit")

    mode = sys.argv[1]
    if mode == "pre-tool-use":
        return run_pre_tool_use()
    if mode == "pre-commit":
        return run_pre_commit(Path.cwd())
    raise SystemExit(f"unknown mode: {mode}")


def run_pre_tool_use() -> int:
    payload = read_payload()
    cwd = Path(str(payload["cwd"]))
    tool_name = str(payload["tool_name"])
    tool_input = payload["tool_input"]
    if not isinstance(tool_input, dict):
        raise TypeError("tool_input must be an object")
    tool_cwd = cwd
    if "workdir" in tool_input and tool_input["workdir"] is not None:
        raw_workdir = tool_input["workdir"]
        if not isinstance(raw_workdir, str):
            raise TypeError("tool_input.workdir must be a string or null")
        workdir = Path(raw_workdir)
        tool_cwd = workdir if workdir.is_absolute() else cwd / workdir
    if tool_name == "apply_patch" and "patch" in tool_input:
        command = tool_input["patch"]
    elif "command" in tool_input:
        command = tool_input["command"]
    else:
        raise KeyError(f"unsupported tool_input schema for {tool_name}")
    if not isinstance(command, str):
        raise TypeError("tool input text must be a string")

    reason = pre_tool_use_block_reason(tool_name, command, tool_cwd)
    if reason is None:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason.message(),
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_pre_commit(cwd: Path) -> int:
    env_paths = protected_git_paths(cwd, cached=True, strict=True)
    rejected_paths: list[str] = []
    credential_findings: list[tuple[str, str]] = []
    if is_codex_config_repo(cwd):
        staged_paths = staged_git_paths(cwd)
        rejected_paths = [path for path in staged_paths if not is_allowed_codex_path(path)]
        credential_findings = staged_credential_findings(cwd)

    if not env_paths and not rejected_paths and not credential_findings:
        return 0

    messages: list[str] = []
    if env_paths:
        path_list = "\n  - ".join(env_paths)
        messages.append(
            "env guard: protected env files are staged and cannot be committed.\n"
            f"  - {path_list}\n"
            "Use a template such as .env.example for committed configuration."
        )
    if rejected_paths:
        path_list = "\n  - ".join(rejected_paths)
        messages.append(
            "codex config guard: unmanaged paths are staged and cannot be committed.\n"
            f"  - {path_list}"
        )
    if credential_findings:
        finding_list = "\n  - ".join(
            f"{path}: {credential_type}" for path, credential_type in credential_findings
        )
        messages.append(
            "codex config guard: credential-like staged content cannot be committed.\n"
            f"  - {finding_list}"
        )
    print("\n".join(messages), file=sys.stderr)
    return 1


def read_payload() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("hook payload is empty")
    payload = json.loads(raw)
    for key in ("cwd", "tool_name", "tool_input"):
        if key not in payload:
            raise KeyError(f"hook payload missing required key: {key}")
    return payload


def pre_tool_use_block_reason(tool_name: str, command: str, cwd: Path) -> BlockReason | None:
    if tool_name == "apply_patch":
        paths = protected_patch_paths(command)
        if paths:
            return BlockReason(paths=tuple(paths), operation="apply_patch")
        return None

    if tool_name not in {"Bash", "shell_command"}:
        return None

    return bash_block_reason(command, cwd)


def bash_block_reason(command: str, cwd: Path) -> BlockReason | None:
    tokens = shell_tokens(command)
    for segment in command_segments(tokens):
        reason = segment_block_reason(segment, cwd)
        if reason is not None:
            return reason
    return None


def segment_block_reason(tokens: list[str], cwd: Path) -> BlockReason | None:
    if not tokens:
        return None

    redirected = protected_redirection_paths(tokens)
    if redirected:
        return BlockReason(paths=tuple(redirected), operation="shell redirection")

    command_index = command_index_for_segment(tokens)
    if command_index is None:
        return None

    command_name = normalized_command_name(tokens[command_index])
    candidate_paths = protected_paths_from_tokens(tokens[command_index + 1 :])

    if command_name == "git":
        return git_block_reason(tokens, command_index, cwd, candidate_paths)

    if command_name in GUARDED_FILE_COMMANDS and candidate_paths:
        return BlockReason(paths=tuple(candidate_paths), operation=command_name)

    return None


def git_block_reason(
    tokens: list[str],
    git_index: int,
    cwd: Path,
    candidate_paths: list[str],
) -> BlockReason | None:
    subcommand_index = git_subcommand_index(tokens, git_index)
    if subcommand_index is None:
        return None
    subcommand = tokens[subcommand_index]

    if subcommand not in GUARDED_GIT_SUBCOMMANDS:
        return None

    git_cwd = git_working_directory(tokens, git_index, cwd)

    if subcommand == "commit":
        bypass_flags = git_commit_hook_bypass_flags(tokens, git_index, subcommand_index)
        if bypass_flags:
            return BlockReason(paths=tuple(bypass_flags), operation="git commit hook bypass")
        if not is_git_work_tree(git_cwd):
            if candidate_paths:
                return BlockReason(paths=tuple(candidate_paths), operation="git commit")
            return None
        paths = protected_git_paths(git_cwd, cached=True, strict=True)
        if git_commit_uses_all(tokens, subcommand_index):
            paths.extend(
                path
                for path in protected_git_paths(git_cwd, cached=False, strict=True)
                if path not in paths
            )
        paths.extend(path for path in candidate_paths if path not in paths)
        if paths:
            return BlockReason(paths=tuple(paths), operation="git commit")
        return None

    if candidate_paths:
        return BlockReason(paths=tuple(candidate_paths), operation=f"git {subcommand}")

    return None


def shell_tokens(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=os.name != "nt", punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    if os.name == "nt":
        return [strip_matching_quotes(token) for token in tokens]
    return tokens


def strip_matching_quotes(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"'", '"'}:
        return token[1:-1]
    return token


def normalized_command_name(token: str) -> str:
    name = Path(token).name.casefold()
    if Path(name).suffix in {".bat", ".cmd", ".exe", ".ps1"}:
        return Path(name).stem
    return name


def command_segments(tokens: list[str]) -> Iterable[list[str]]:
    segment: list[str] = []
    for token in tokens:
        if token in SHELL_BREAKS:
            if segment:
                yield segment
                segment = []
            continue
        segment.append(token)
    if segment:
        yield segment


def command_index_for_segment(tokens: list[str]) -> int | None:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if ASSIGNMENT_RE.match(token):
            index += 1
            continue
        command_name = normalized_command_name(token)
        if command_name == "env":
            index += 1
            while index < len(tokens) and (tokens[index].startswith("-") or ASSIGNMENT_RE.match(tokens[index])):
                if tokens[index] in {"-u", "-C", "-S"} and index + 1 < len(tokens):
                    index += 2
                    continue
                index += 1
            continue
        if command_name == "sudo":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                if tokens[index] in {"-u", "-g", "-h", "-p"} and index + 1 < len(tokens):
                    index += 2
                    continue
                index += 1
            continue
        if command_name in WRAPPER_COMMANDS:
            index += 1
            continue
        return index
    return None


def git_subcommand_index(tokens: list[str], git_index: int) -> int | None:
    index = git_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("--git-dir=") or token.startswith("--work-tree=") or token.startswith("--namespace="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index
    return None


def git_working_directory(tokens: list[str], git_index: int, cwd: Path) -> Path:
    directory = cwd
    index = git_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C" and index + 1 < len(tokens):
            candidate = Path(tokens[index + 1])
            directory = candidate if candidate.is_absolute() else directory / candidate
            index += 2
            continue
        if token.startswith("-C") and len(token) > 2:
            candidate = Path(token[2:])
            directory = candidate if candidate.is_absolute() else directory / candidate
            index += 1
            continue
        if token in {"-c", "--git-dir", "--work-tree", "--namespace"} and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("--git-dir=") or token.startswith("--work-tree=") or token.startswith("--namespace="):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return directory.resolve()


def git_commit_uses_all(tokens: list[str], commit_index: int) -> bool:
    for token in git_commit_option_tokens(tokens, commit_index):
        if token == "--all":
            return True
        if short_commit_option_has_flag(token, "a"):
            return True
    return False


def git_commit_hook_bypass_flags(
    tokens: list[str], git_index: int, commit_index: int
) -> list[str]:
    bypass_flags = git_environment_config_override_flags(tokens, git_index)
    for flag in git_hooks_path_override_flags(tokens, git_index, commit_index):
        if flag not in bypass_flags:
            bypass_flags.append(flag)
    for token in git_commit_option_tokens(tokens, commit_index):
        abbreviated_no_verify = (
            token.startswith("--no-v") and "--no-verify".startswith(token)
        )
        combined_no_verify = short_commit_option_has_flag(token, "n")
        if abbreviated_no_verify or combined_no_verify:
            if token not in bypass_flags:
                bypass_flags.append(token)
    return bypass_flags


def git_environment_config_override_flags(tokens: list[str], git_index: int) -> list[str]:
    overrides: list[str] = []
    for token in tokens[:git_index]:
        if ASSIGNMENT_RE.fullmatch(token) is None:
            continue
        key = token.split("=", 1)[0]
        if key == "GIT_CONFIG" or key.startswith("GIT_CONFIG_"):
            if key not in overrides:
                overrides.append(key)
    return overrides


def git_hooks_path_override_flags(
    tokens: list[str], git_index: int, subcommand_index: int
) -> list[str]:
    overrides: list[str] = []
    index = git_index + 1
    while index < subcommand_index:
        token = tokens[index]
        config_value: str | None = None
        display_value = token
        if token == "-c" and index + 1 < subcommand_index:
            config_value = tokens[index + 1]
            display_value = f"-c {config_value}"
            index += 2
        elif token.startswith("-c") and len(token) > 2:
            config_value = token[2:]
            index += 1
        elif token == "--config-env" and index + 1 < subcommand_index:
            config_value = tokens[index + 1]
            display_value = f"--config-env {config_value}"
            index += 2
        elif token.startswith("--config-env="):
            config_value = token.removeprefix("--config-env=")
            index += 1
        else:
            index += 1

        if config_value is None:
            continue
        config_key = config_value.split("=", 1)[0].casefold()
        if config_key == "core.hookspath" and display_value not in overrides:
            overrides.append(display_value)
    return overrides


def git_commit_option_tokens(tokens: list[str], commit_index: int) -> Iterable[str]:
    index = commit_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return
        if not token.startswith("-") or token == "-":
            index += 1
            continue

        yield token
        if git_commit_option_consumes_next(token):
            index += 2
        else:
            index += 1


def git_commit_option_consumes_next(token: str) -> bool:
    if token.startswith("--"):
        if "=" in token:
            return False
        return any(option.startswith(token) for option in LONG_COMMIT_VALUE_OPTIONS)
    if not token.startswith("-") or token == "-":
        return False

    short_options = token[1:]
    for index, option in enumerate(short_options):
        if option in SHORT_COMMIT_VALUE_OPTIONS:
            return index == len(short_options) - 1
        if option in SHORT_COMMIT_ATTACHED_VALUE_OPTIONS:
            return False
        if option not in SHORT_COMMIT_BOOLEAN_OPTIONS:
            return False
    return False


def short_commit_option_has_flag(token: str, expected: str) -> bool:
    if not token.startswith("-") or token.startswith("--"):
        return False
    for option in token[1:]:
        if option in SHORT_COMMIT_VALUE_OPTIONS or option in SHORT_COMMIT_ATTACHED_VALUE_OPTIONS:
            return False
        if option not in SHORT_COMMIT_BOOLEAN_OPTIONS:
            return False
        if option == expected:
            return True
    return False


def is_git_work_tree(cwd: Path) -> bool:
    result = run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    return result.returncode == 0 and result.stdout.strip() == "true"


def protected_redirection_paths(tokens: list[str]) -> list[str]:
    paths: list[str] = []
    for index, token in enumerate(tokens):
        if token in REDIRECT_OPERATORS and index + 1 < len(tokens):
            add_protected_path(paths, tokens[index + 1])
            continue
        match = ATTACHED_REDIRECT_RE.match(token)
        if match is not None:
            add_protected_path(paths, match.group("path"))
    return paths


def protected_paths_from_tokens(tokens: Iterable[str]) -> list[str]:
    paths: list[str] = []
    for token in tokens:
        add_protected_path(paths, token)
        if "=" in token and not token.startswith("="):
            add_protected_path(paths, token.split("=", 1)[1])
    return paths


def protected_patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        path = patch_path_from_line(line)
        if path is not None:
            add_protected_path(paths, path)
    return paths


def patch_path_from_line(line: str) -> str | None:
    for pattern in (PATCH_PATH_RE, PATCH_MOVE_RE):
        match = pattern.match(line)
        if match is not None:
            return match.group("path")
    return None


def add_protected_path(paths: list[str], value: str) -> None:
    cleaned = clean_path_candidate(value)
    if cleaned is None:
        return
    if is_protected_env_path(cleaned) and cleaned not in paths:
        paths.append(cleaned)


def clean_path_candidate(value: str) -> str | None:
    cleaned = value.strip()
    if cleaned == "" or cleaned.startswith("$"):
        return None
    cleaned = cleaned.strip("'\"")
    cleaned = cleaned.rstrip(",)")
    match = ATTACHED_REDIRECT_RE.match(cleaned)
    if match is not None:
        cleaned = match.group("path")
    if cleaned in REDIRECT_OPERATORS or cleaned == "":
        return None
    return cleaned


def is_protected_env_path(path: str) -> bool:
    name = Path(path).name.casefold()
    if not name.startswith(PROTECTED_PREFIXES):
        return False
    return not is_env_template_name(name)


def is_env_template_name(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in TEMPLATE_SUFFIXES)


def is_codex_config_repo(cwd: Path) -> bool:
    repo_root = git_repo_root(cwd)
    raw_codex_home = os.environ.get("CODEX_HOME")
    if raw_codex_home is None:
        home = os.environ.get("HOME")
        if home is None:
            raise RuntimeError("HOME is unset and CODEX_HOME is not configured")
        codex_home = Path(home) / ".codex"
    else:
        if raw_codex_home == "":
            raise RuntimeError("CODEX_HOME is empty")
        codex_home = Path(raw_codex_home).expanduser()
        if not codex_home.is_absolute():
            codex_home = cwd / codex_home
    return repo_root.resolve() == codex_home.resolve()


def git_repo_root(cwd: Path) -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], cwd)
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse --show-toplevel failed: {result.stderr.strip()}")
    root = result.stdout.strip()
    if root == "":
        raise RuntimeError("git rev-parse --show-toplevel returned an empty path")
    return Path(root)


def staged_git_paths(cwd: Path) -> list[str]:
    result = run_git(
        ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRD"],
        cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff --cached failed: {result.stderr.strip()}")
    return list(dict.fromkeys(path for path in result.stdout.split("\0") if path != ""))


def is_allowed_codex_path(path: str) -> bool:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    normalized = candidate.as_posix()
    if normalized in CODEX_ALLOWED_ROOT_FILES:
        return True
    return any(
        normalized == directory or normalized.startswith(f"{directory}/")
        for directory in CODEX_ALLOWED_DIRECTORIES
    )


def staged_credential_findings(cwd: Path) -> list[tuple[str, str]]:
    result = run_git(
        [
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACM",
        ],
        cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff --cached failed: {result.stderr.strip()}")

    findings: list[tuple[str, str]] = []
    paths = list(dict.fromkeys(path for path in result.stdout.split("\0") if path != ""))
    for path in paths:
        if not is_allowed_codex_path(path):
            continue
        content = staged_file_content(cwd, path)
        for credential_type, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(content) is not None:
                findings.append((path, credential_type))
    return findings


def staged_file_content(cwd: Path, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", "--no-textconv", f":{path}"],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git show failed for staged path {path!r}: {error}")
    return result.stdout


def protected_git_paths(cwd: Path, *, cached: bool, strict: bool) -> list[str]:
    args = ["diff"]
    if cached:
        args.append("--cached")
    args.extend(["--name-status", "-z", "--diff-filter=ACMRD"])

    result = run_git(args, cwd)
    if result.returncode != 0:
        if strict:
            command = " ".join(["git", *args])
            raise RuntimeError(f"{command} failed: {result.stderr.strip()}")
        return []

    paths: list[str] = []
    for path in parse_name_status_z(result.stdout):
        add_protected_path(paths, path)
    return paths


def parse_name_status_z(output: str) -> list[str]:
    fields = [field for field in output.split("\0") if field != ""]
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 > len(fields):
                break
            paths.append(fields[index])
            if index + 1 < len(fields):
                paths.append(fields[index + 1])
            index += 2
            continue
        if index >= len(fields):
            break
        paths.append(fields[index])
        index += 1
    return paths


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if len(sys.argv) > 1 and sys.argv[1] == "pre-tool-use":
            print(f"env guard failed closed: {error}", file=sys.stderr)
            raise SystemExit(2)
        print(f"env guard failed: {error}", file=sys.stderr)
        raise SystemExit(1)
