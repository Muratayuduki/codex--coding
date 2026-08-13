#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import codex_readme_guard

if os.name == "nt":
    import msvcrt
else:
    import fcntl


JST = timezone(timedelta(hours=9))


# 既存 memo.md の8項目 done ブロックにも検証を通すため、8項目の部分集合を保つこと
REQUIRED_FIELDS = [
    "要件・完了条件",
    "調査ログ",
    "判断までの経緯",
    "実装・変更内容",
    "確認結果",
    "残課題・次回引き継ぎ",
]

PLACEHOLDER_VALUES = {
    "",
    "todo",
    "tbd",
    "未記入",
    "未定",
    "あとで書く",
    "なし",
    "該当なし",
    "特になし",
}

MIN_FIELD_VALUE_CHARS = 20
MEMO_RELATIVE_PATHS = (
    "memo.md",
    "docs/memo.md",
    "plan/memo.md",
)

# gpt-5.x の mini 系は ChatGPT アカウントでは利用不可（2026-07 時点で疎通確認済み）
SUMMARY_MODEL = "gpt-5.5"
SUMMARY_REASONING_EFFORT = "low"
SUMMARY_TIMEOUT_SECONDS = 240

MAX_EXCERPT_CHARS = 120_000
MAX_USER_MESSAGE_CHARS = 4_000
MAX_FINAL_AGENT_MESSAGE_CHARS = 4_000
MAX_AGENT_MESSAGE_CHARS = 1_000
MAX_COMMAND_CHARS = 500

REDACTED_VALUE = "[REDACTED]"
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b
    (?P<key>[A-Za-z_][A-Za-z0-9_]*(?:api[_-]?key|key|token|secret|password|passwd|credential|auth|cookie)[A-Za-z0-9_]*)
    \s*(?P<separator>[:=])\s*
    (?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&]+)
    """
)
SENSITIVE_QUOTED_FIELD_RE = re.compile(
    r"""(?ix)
    (?P<quote>[\"'])
    (?P<key>[A-Za-z_][A-Za-z0-9_]*(?:api[_-]?key|key|token|secret|password|passwd|credential|auth|cookie)[A-Za-z0-9_]*)
    (?P=quote)\s*:\s*
    (?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,;&}]+)
    """
)
SENSITIVE_FLAG_RE = re.compile(
    r"""(?ix)
    (?P<prefix>--(?:api[-_]?key|token|secret|password|passwd|credential|auth|cookie)(?:=|\s+))
    (?P<value>\"[^\"\n]*\"|'[^'\n]*'|\S+)
    """
)
AUTHORIZATION_RE = re.compile(
    r"(?i)\b(?P<prefix>(?:proxy-)?authorization\s*:\s*)(?:bearer|basic|token)\s+[^\s,;]+"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
KNOWN_CREDENTIAL_RE = re.compile(
    r"""(?ix)
    \b(?:
        sk(?:-proj)?-[A-Za-z0-9_-]{8,}
        |gh[pousr]_[A-Za-z0-9]{20,}
        |github_pat_[A-Za-z0-9_]{20,}
        |AKIA[0-9A-Z]{16}
        |AIza[A-Za-z0-9_-]{20,}
    )\b
    """
)
PATCH_TARGET_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
PATCH_MOVE_TARGET_RE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)

MEMO_TAG = "#memo"
NOMEMO_TAG = "#nomemo"

READ_ONLY_TOOL_NAMES = {
    "fetch_openai_doc",
    "notion_fetch",
    "notion_search",
    "request_user_input",
    "search_openai_docs",
    "update_plan",
    "view_image",
}
READ_ONLY_COMMANDS = {
    "basename",
    "cat",
    "diff",
    "dirname",
    "du",
    "echo",
    "fd",
    "file",
    "grep",
    "head",
    "jq",
    "ls",
    "nl",
    "printf",
    "pwd",
    "readlink",
    "realpath",
    "rg",
    "stat",
    "tail",
    "tr",
    "tree",
    "uniq",
    "wc",
    "which",
}
READ_ONLY_GIT_SUBCOMMANDS = {
    "blame",
    "cat-file",
    "check-ignore",
    "describe",
    "diff",
    "grep",
    "log",
    "ls-files",
    "ls-remote",
    "ls-tree",
    "rev-parse",
    "shortlog",
    "show",
    "status",
}

PENDING_OPEN_RE = re.compile(r"<!--\s*codex-memo:pending\b(?P<meta>[^>]*)-->")
PENDING_CLOSE = "<!-- /codex-memo:pending -->"
DONE_OPEN_RE = re.compile(r"<!--\s*codex-memo:done\b(?P<meta>[^>]*)-->")
DONE_OPEN_PREFIX = "<!-- codex-memo:done"
DONE_CLOSE = "<!-- /codex-memo:done -->"

PLAN_MODE_BOOL_KEYS = {
    "is_plan_mode",
    "isPlanMode",
    "plan_mode",
    "planMode",
}
PLAN_MODE_CONTAINER_KEYS = {
    "active_mode",
    "activeMode",
    "collaboration_mode",
    "collaborationMode",
    "collaboration_mode_kind",
    "collaborationModeKind",
    "conversation_mode",
    "conversationMode",
    "current_mode",
    "currentMode",
    "mode",
    "mode_kind",
    "modeKind",
    "permission_mode",
    "permissionMode",
    "selected_mode",
    "selectedMode",
}
PLAN_MODE_VALUE_KEYS = {
    "active",
    "current",
    "id",
    "kind",
    "mode",
    "name",
    "selected",
    "type",
    "value",
}
MAX_PLAN_MODE_SCAN_DEPTH = 6


@dataclass(frozen=True)
class PendingBlock:
    meta: str
    body: str
    malformed: bool = False


def main() -> int:
    usage = "usage: codex_memo_guard.py stop|summarize <job.json>|pre-commit"
    if len(sys.argv) < 2:
        raise SystemExit(usage)

    mode = sys.argv[1]
    if mode == "stop":
        if len(sys.argv) != 2:
            raise SystemExit(usage)
        return run_stop()
    if mode == "summarize":
        if len(sys.argv) != 3:
            raise SystemExit(usage)
        return run_summarize(Path(sys.argv[2]))
    if mode == "pre-commit":
        if len(sys.argv) != 2:
            raise SystemExit(usage)
        return run_pre_commit()
    raise SystemExit(f"unknown mode: {mode}")


def run_stop() -> int:
    payload = read_payload()
    if is_subagent_payload(payload):
        return 0
    if is_plan_mode_payload(payload):
        codex_readme_guard.discard_turn_state(payload)
        return 0
    readme_stop_reason = codex_readme_guard.evaluate_turn_stop(payload)
    if readme_stop_reason is not None:
        codex_readme_guard.print_block_decision(readme_stop_reason)
        return 0
    memo_path = memo_path_for_payload(payload)
    if not memo_path.is_file():
        return 0

    session_id = str(payload["session_id"])
    turn_id = str(payload["turn_id"])
    state = read_state(session_id, turn_id)
    if state is not None and state.get("summary_status") is not None:
        return 0

    session_path = session_file_for_payload(payload)
    if session_path is None:
        raise FileNotFoundError(f"session file not found for session_id={session_id}")

    records = extract_turn_slice(session_path, turn_id)
    messages = turn_user_messages(records)
    if has_tag(messages, NOMEMO_TAG):
        write_state(session_id, turn_id, memo_path, summary_status="skipped:nomemo")
        return 0
    change_reason = change_turn_reason(records)
    if change_reason is None and not has_tag(messages, MEMO_TAG):
        write_state(
            session_id, turn_id, memo_path, summary_status="skipped:read-only-turn"
        )
        return 0

    job_path = write_job_file(payload, memo_path, records, change_reason)
    spawn_summarizer(job_path, log_path(session_id, turn_id))
    write_state(session_id, turn_id, memo_path, summary_status="spawned")
    return 0


def run_summarize(job_path: Path) -> int:
    out_path: Path | None = None
    managed_job = False
    session_id: str | None = None
    turn_id: str | None = None
    try:
        ensure_managed_job_path(job_path)
        ensure_private_file(job_path, create=False)
        managed_job = True
        job = json.loads(job_path.read_text(encoding="utf-8"))
        session_id = str(job["session_id"])
        turn_id = str(job["turn_id"])
        memo_path = Path(str(job["memo_path"]))
        cwd = str(job["cwd"])
        print(f"summarize start: turn={turn_id} reason={job['change_reason']}")

        prompt = build_summary_prompt(str(job["excerpt"]))
        out_path = job_path.with_suffix(".out.md")
        ensure_private_file(out_path, create=True)
        body = summarize_with_retry(prompt, cwd, out_path)
    except Exception as error:
        if session_id is not None and turn_id is not None:
            write_state(
                session_id,
                turn_id,
                Path(str(job["memo_path"])),
                summary_status=f"failed:{type(error).__name__}",
            )
        raise
    else:
        with memo_lock(memo_path):
            ensure_memo_gitignored(memo_path)
            text = read_text_or_empty(memo_path)
            if not has_done_marker(text, session_id, turn_id):
                now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
                entry = build_done_entry(now, session_id, turn_id, body)
                if text and not text.endswith("\n"):
                    text += "\n"
                if not text:
                    text = "# 作業メモ\n\n"
                memo_path.write_text(text + entry, encoding="utf-8")

        write_state(session_id, turn_id, memo_path, summary_status="done")
        print(f"summarize done: turn={turn_id} memo={memo_path}")
        return 0
    finally:
        if managed_job:
            remove_private_file(job_path)
            if out_path is not None:
                remove_private_file(out_path)


def run_pre_commit() -> int:
    memo_path = memo_path_for_cwd(Path.cwd())
    if not memo_path.is_file():
        return 0

    with memo_lock(memo_path):
        reason = validation_failure_reason(memo_path)
    if reason is None:
        return 0

    print(reason, file=sys.stderr)
    return 1


def extract_turn_slice(session_path: Path, turn_id: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    in_turn = False
    completed = False
    with session_path.open("r", encoding="utf-8") as session_file:
        for line in session_file:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            record_payload = record.get("payload")
            if record.get("type") == "event_msg" and isinstance(record_payload, dict):
                event_type = record_payload.get("type")
                if (
                    event_type == "task_started"
                    and record_payload.get("turn_id") == turn_id
                ):
                    in_turn = True
                    completed = False
                    records = [record]
                    continue
                if (
                    in_turn
                    and event_type in {"task_complete", "turn_aborted"}
                    and record_payload.get("turn_id") == turn_id
                ):
                    records.append(record)
                    in_turn = False
                    completed = True
                    continue
            if in_turn:
                records.append(record)
    if not records:
        raise ValueError(f"turn {turn_id} not found in {session_path}")
    if not completed:
        print(
            f"warning: turn {turn_id} has no terminal event; using slice to EOF",
            file=sys.stderr,
        )
    return records


def turn_user_messages(records: list[dict[str, object]]) -> list[str]:
    messages: list[str] = []
    for record in records:
        record_payload = record.get("payload")
        if (
            record.get("type") == "event_msg"
            and isinstance(record_payload, dict)
            and record_payload.get("type") == "user_message"
        ):
            messages.append(str(record_payload["message"]))
    return messages


def has_tag(messages: list[str], tag: str) -> bool:
    pattern = re.compile(rf"(^|\s){re.escape(tag)}\b")
    return any(pattern.search(message) for message in messages)


def change_turn_reason(records: list[dict[str, object]]) -> str | None:
    for record in records:
        record_payload = record.get("payload")
        if not isinstance(record_payload, dict):
            continue
        record_type = record.get("type")
        payload_type = record_payload.get("type")
        if record_type == "event_msg" and payload_type == "patch_apply_end":
            return "patch_apply_end"
        if record_type != "response_item":
            continue
        if payload_type == "custom_tool_call":
            return f"custom_tool_call:{record_payload.get('name')}"
        if payload_type != "function_call":
            continue
        name = str(record_payload["name"])
        if name in READ_ONLY_TOOL_NAMES:
            continue
        if name == "exec_command":
            cmd = str(json.loads(str(record_payload["arguments"]))["cmd"])
            if is_read_only_command(cmd):
                continue
            return f"exec_command:{cmd[:120]}"
        return f"tool:{name}"
    return None


def is_read_only_command(cmd: str) -> bool:
    if ">" in cmd or "<<" in cmd:
        return False
    for segment in re.split(r"&&|\|\||[;|]", cmd):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        head = tokens[0].rsplit("/", 1)[-1]
        if head == "git":
            if not is_read_only_git_command(tokens[1:]):
                return False
        elif head == "find":
            if {"-delete", "-exec", "-execdir", "-ok", "-okdir"} & set(tokens):
                return False
        elif head == "sed":
            if "-n" not in tokens or any(
                token.startswith("-i") for token in tokens
            ):
                return False
        elif head not in READ_ONLY_COMMANDS:
            return False
    return True


def is_read_only_git_command(tokens: list[str]) -> bool:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {"-C", "-c"}:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token in READ_ONLY_GIT_SUBCOMMANDS
    return False


def build_transcript_excerpt(records: list[dict[str, object]]) -> str:
    final_agent_index = -1
    for index, record in enumerate(records):
        record_payload = record.get("payload")
        if (
            record.get("type") == "event_msg"
            and isinstance(record_payload, dict)
            and record_payload.get("type") == "agent_message"
        ):
            final_agent_index = index

    # priority: 0=最後まで保持, 1=途中経過メッセージ, 2=ツール出力（超過時は古い順に削る）
    entries: list[tuple[int, str]] = []
    for index, record in enumerate(records):
        record_payload = record.get("payload")
        if not isinstance(record_payload, dict):
            continue
        record_type = record.get("type")
        payload_type = record_payload.get("type")
        if record_type == "event_msg":
            if payload_type == "user_message":
                message = clip_middle(
                    redact_secrets(str(record_payload["message"])),
                    MAX_USER_MESSAGE_CHARS,
                )
                entries.append((0, f"[user]\n{message}"))
            elif payload_type == "agent_message":
                if index == final_agent_index:
                    message = clip_middle(
                        redact_secrets(str(record_payload["message"])),
                        MAX_FINAL_AGENT_MESSAGE_CHARS,
                    )
                    entries.append((0, f"[assistant final]\n{message}"))
                else:
                    message = clip_middle(
                        redact_secrets(str(record_payload["message"])),
                        MAX_AGENT_MESSAGE_CHARS,
                    )
                    entries.append((1, f"[assistant]\n{message}"))
            elif payload_type == "patch_apply_end":
                paths = patch_result_paths(record_payload)
                entries.append(
                    (
                        0,
                        f"[patch result success={record_payload['success']} paths={paths}]",
                    )
                )
        elif record_type == "response_item":
            if payload_type == "function_call":
                name = str(record_payload["name"])
                arguments = str(record_payload["arguments"])
                if name == "exec_command":
                    cmd = str(json.loads(arguments)["cmd"])
                    entries.append(
                        (
                            0,
                            f"[command]\n{clip_middle(redact_secrets(cmd), MAX_COMMAND_CHARS)}",
                        )
                    )
                else:
                    entries.append(
                        (
                            0,
                            f"[tool {name}]\n"
                            f"{clip_middle(redact_secrets(arguments), MAX_COMMAND_CHARS)}",
                        )
                    )
            elif payload_type == "custom_tool_call":
                name = str(record_payload["name"])
                tool_input = str(record_payload["input"])
                if name == "apply_patch":
                    paths = patch_paths(tool_input)
                    entries.append(
                        (
                            0,
                            f"[patch files: {paths}]",
                        )
                    )
                else:
                    entries.append(
                        (
                            0,
                            f"[tool {name}]\n"
                            f"{clip_middle(redact_secrets(tool_input), MAX_COMMAND_CHARS)}",
                        )
                    )
            elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                entries.append((2, "[tool output omitted]"))
    return join_entries_within_budget(entries, MAX_EXCERPT_CHARS)


def redact_secrets(text: str) -> str:
    redacted = AUTHORIZATION_RE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_VALUE}", text
    )
    redacted = BEARER_TOKEN_RE.sub(f"Bearer {REDACTED_VALUE}", redacted)
    redacted = SENSITIVE_FLAG_RE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED_VALUE}", redacted
    )
    redacted = SENSITIVE_QUOTED_FIELD_RE.sub(
        lambda match: (
            f"{match.group('quote')}{match.group('key')}{match.group('quote')}: "
            f"{REDACTED_VALUE}"
        ),
        redacted,
    )
    redacted = SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group('key')}{match.group('separator')}{REDACTED_VALUE}"
        ),
        redacted,
    )
    return KNOWN_CREDENTIAL_RE.sub(REDACTED_VALUE, redacted)


def patch_paths(patch: str) -> str:
    paths = [
        *PATCH_TARGET_RE.findall(patch),
        *PATCH_MOVE_TARGET_RE.findall(patch),
    ]
    return redact_secrets(", ".join(paths)) if paths else "unknown"


def patch_result_paths(payload: dict[str, object]) -> str:
    raw_paths = payload.get("paths")
    if isinstance(raw_paths, list) and all(
        isinstance(path, str) for path in raw_paths
    ):
        return redact_secrets(", ".join(raw_paths)) if raw_paths else "unknown"
    raw_path = payload.get("path")
    if isinstance(raw_path, str):
        return redact_secrets(raw_path)
    return "unknown"


def join_entries_within_budget(
    entries: list[tuple[int, str]], budget: int
) -> str:
    def total(items: list[tuple[int, str]]) -> int:
        return sum(len(text) + 2 for _, text in items)

    kept = list(entries)
    for drop_priority in (2, 1):
        while total(kept) > budget:
            drop_index = next(
                (
                    index
                    for index, (priority, _) in enumerate(kept)
                    if priority == drop_priority
                ),
                None,
            )
            if drop_index is None:
                break
            del kept[drop_index]
    excerpt = "\n\n".join(text for _, text in kept)
    if len(excerpt) > budget:
        excerpt = clip_middle(excerpt, budget)
    return excerpt


def clip_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    keep = limit // 2
    omitted = len(text) - keep * 2
    return f"{text[:keep]}…[truncated {omitted} chars]…{text[-keep:]}"


def build_summary_prompt(excerpt: str) -> str:
    field_lines = "\n".join(f"- {field}: " for field in REQUIRED_FIELDS)
    return (
        "あなたは開発セッションの判断ログを作成する記録係です。\n"
        "以下は Codex セッション1ターン分のトランスクリプト抜粋です。"
        "ファイルの調査やコマンドの実行は行わず、抜粋の内容だけを根拠に、"
        "次の6項目の箇条書きを日本語で作成してください。\n\n"
        "出力形式（この6行のみを出力し、見出し・コードフェンス・前置きは書かない）:\n"
        f"{field_lines}\n\n"
        "制約:\n"
        f"- 各項目は{MIN_FIELD_VALUE_CHARS}文字以上とし、"
        "トランスクリプト中の事実（ファイル名、コマンド、結果）を具体的に含める\n"
        "- 比較した候補・選定理由・試行錯誤は「判断までの経緯」に含める\n"
        "- 推測で書く場合は推測であることを明記する\n"
        "- 該当がない項目は「なし」ではなく、該当しない理由を一文で書く\n\n"
        "=== トランスクリプト抜粋 ===\n"
        f"{excerpt}\n"
    )


def summarize_with_retry(prompt: str, cwd: str, out_path: Path) -> str:
    body = run_codex_exec(prompt, cwd, out_path)
    missing = missing_required_fields(body)
    if not missing:
        return body
    print(
        f"summary validation failed ({', '.join(missing)}); retrying",
        file=sys.stderr,
    )
    retry_prompt = (
        f"{prompt}\n"
        f"前回の出力は次の項目が不足していました: {', '.join(missing)}。"
        "6項目すべてを制約どおりに出力し直してください。\n"
    )
    body = run_codex_exec(retry_prompt, cwd, out_path)
    missing = missing_required_fields(body)
    if missing:
        raise RuntimeError(
            f"summary is missing required fields after retry: {', '.join(missing)}"
        )
    return body


def run_codex_exec(prompt: str, cwd: str, out_path: Path) -> str:
    default_codex = "codex.cmd" if os.name == "nt" else "codex"
    codex_bin = os.environ.get("CODEX_MEMO_GUARD_CODEX", default_codex)
    command_prefix = [sys.executable, codex_bin] if codex_bin.endswith(".py") else [codex_bin]
    command = [
        *command_prefix,
        "exec",
        "-",
        "-C",
        cwd,
        "-s",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "-c",
        "features.hooks=false",
        "-m",
        SUMMARY_MODEL,
        "-c",
        f'model_reasoning_effort="{SUMMARY_REASONING_EFFORT}"',
        "--color",
        "never",
        "-o",
        str(out_path),
    ]
    result = subprocess.run(
        command,
        input=prompt,
        text=True,
        timeout=SUMMARY_TIMEOUT_SECONDS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"codex exec failed with code {result.returncode}")
    if not out_path.is_file():
        raise RuntimeError("codex exec did not write the output file")
    ensure_private_file(out_path, create=False)
    body = strip_code_fence(out_path.read_text(encoding="utf-8").strip())
    if not body:
        raise RuntimeError("codex exec returned an empty summary")
    return body


def strip_code_fence(text: str) -> str:
    match = re.fullmatch(r"```[^\n]*\n(.*)\n```", text, re.DOTALL)
    if match is None:
        return text
    return match.group(1).strip()


def build_done_entry(
    timestamp: str, session_id: str, turn_id: str, body: str
) -> str:
    if not body.endswith("\n"):
        body += "\n"
    return (
        f"## {timestamp} - Codex turn {turn_id}\n"
        f"<!-- codex-memo:done session={session_id} turn={turn_id} -->\n"
        f"{body}"
        f"{DONE_CLOSE}\n\n"
    )


def write_job_file(
    payload: dict[str, object],
    memo_path: Path,
    records: list[dict[str, object]],
    change_reason: str | None,
) -> Path:
    session_id = str(payload["session_id"])
    turn_id = str(payload["turn_id"])
    path = job_path(session_id, turn_id)
    write_private_text(
        path,
        json.dumps(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": str(payload["cwd"]),
                "memo_path": str(memo_path),
                "change_reason": change_reason or "forced-by-#memo",
                "excerpt": build_transcript_excerpt(records),
            },
            ensure_ascii=False,
        ),
    )
    return path


def spawn_summarizer(job_path: Path, log_path: Path) -> None:
    ensure_private_file(log_path, create=True)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    with log_path.open("a", encoding="utf-8") as log_file:
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "summarize",
                str(job_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=child_env,
            start_new_session=True,
        )


def read_payload() -> dict[str, object]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("hook payload is empty")
    payload = json.loads(raw)
    for key in ("cwd", "session_id", "turn_id"):
        if key not in payload:
            raise KeyError(f"hook payload missing required key: {key}")
    return payload


def memo_path_for_payload(payload: dict[str, object]) -> Path:
    return memo_path_for_cwd(Path(str(payload["cwd"])))


def memo_path_for_cwd(cwd: Path) -> Path:
    root = repo_root(cwd)
    candidates = [root / relative_path for relative_path in MEMO_RELATIVE_PATHS]
    if cwd != root:
        candidates.extend(cwd / relative_path for relative_path in MEMO_RELATIVE_PATHS)

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return candidate
    return root / "memo.md"


def is_subagent_payload(payload: dict[str, object]) -> bool:
    return payload.get("agent_id") is not None or payload.get("agent_type") is not None


def is_plan_mode_payload(payload: dict[str, object]) -> bool:
    if payload_has_plan_mode_marker(payload):
        return True
    return session_turn_is_plan_mode(payload)


def payload_has_plan_mode_marker(payload: dict[str, object]) -> bool:
    for key in PLAN_MODE_BOOL_KEYS:
        if is_truthy_plan_flag(payload.get(key)):
            return True

    for key in PLAN_MODE_CONTAINER_KEYS:
        if key in payload and contains_plan_mode_marker(payload[key]):
            return True
    return False


def session_turn_is_plan_mode(payload: dict[str, object]) -> bool:
    session_path = session_file_for_payload(payload)
    if session_path is None:
        return False

    turn_id = str(payload["turn_id"])
    with session_path.open("r", encoding="utf-8") as session_file:
        for line in session_file:
            if turn_id not in line:
                continue
            record = json.loads(line)
            if record.get("type") not in {"event_msg", "turn_context"}:
                continue
            record_payload = record.get("payload")
            if not isinstance(record_payload, dict):
                continue
            if record_payload.get("turn_id") != turn_id:
                continue
            if payload_has_plan_mode_marker(record_payload):
                return True
    return False


def session_file_for_payload(payload: dict[str, object]) -> Path | None:
    configured_codex_home = os.environ.get("CODEX_HOME")
    if configured_codex_home:
        codex_home = Path(configured_codex_home)
    elif os.environ.get("HOME"):
        codex_home = Path(os.environ["HOME"]) / ".codex"
    else:
        codex_home = Path.home() / ".codex"
    sessions_dir = codex_home / "sessions"
    session_id = str(payload["session_id"])
    matches = sorted(sessions_dir.rglob(f"*{session_id}.jsonl"))
    if not matches:
        return None
    return matches[-1]


def is_truthy_plan_flag(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return False


def contains_plan_mode_marker(value: object, depth: int = 0) -> bool:
    if depth > MAX_PLAN_MODE_SCAN_DEPTH:
        return False
    if isinstance(value, str):
        return is_plan_mode_string(value)
    if isinstance(value, list):
        return any(contains_plan_mode_marker(item, depth + 1) for item in value)
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key in PLAN_MODE_BOOL_KEYS and is_truthy_plan_flag(nested_value):
                return True
            if key in PLAN_MODE_VALUE_KEYS and contains_plan_mode_marker(
                nested_value,
                depth + 1,
            ):
                return True
            if key in PLAN_MODE_CONTAINER_KEYS and contains_plan_mode_marker(
                nested_value,
                depth + 1,
            ):
                return True
    return False


def is_plan_mode_string(value: str) -> bool:
    normalized = re.sub(r"[\s_-]+", " ", value.strip().casefold())
    return normalized in {"plan", "plan mode"}


def repo_root(cwd: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        root = result.stdout.strip()
        if root:
            return Path(root)
    return cwd


def ensure_memo_gitignored(memo_path: Path) -> None:
    root = repo_root(memo_path.parent)
    gitignore_path = root / ".gitignore"
    pattern = memo_path.relative_to(root).as_posix()
    text = read_text_or_empty(gitignore_path)
    entries = {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if pattern in entries:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    gitignore_path.write_text(text + f"{pattern}\n", encoding="utf-8")


def state_dir() -> Path:
    configured = os.environ.get("CODEX_MEMO_GUARD_STATE_DIR")
    if configured == "":
        raise RuntimeError("CODEX_MEMO_GUARD_STATE_DIR must not be empty")
    if configured is not None:
        return ensure_private_directory(Path(configured))

    configured_home = os.environ.get("HOME")
    if configured_home:
        root = Path(configured_home) / ".cache"
    elif os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "Codex"
    else:
        root = Path(tempfile.gettempdir())
    return ensure_private_directory(root / "codex-memo-guard")


def jobs_dir() -> Path:
    return ensure_private_directory(state_dir() / "jobs")


def logs_dir() -> Path:
    return ensure_private_directory(state_dir() / "logs")


def locks_dir() -> Path:
    return ensure_private_directory(state_dir() / "locks")


def ensure_private_directory(path: Path) -> Path:
    if os.path.lexists(path):
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise RuntimeError(f"private state directory must not be a symlink: {path}")
        if not stat.S_ISDIR(file_stat.st_mode):
            raise RuntimeError(f"private state path is not a directory: {path}")
        ensure_current_user_owner(file_stat, "private state directory", path)
    else:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        file_stat = path.lstat()
        if not stat.S_ISDIR(file_stat.st_mode):
            raise RuntimeError(f"private state path is not a directory: {path}")
        ensure_current_user_owner(file_stat, "private state directory", path)
    path.chmod(0o700)
    return path


def ensure_private_file(path: Path, *, create: bool) -> None:
    if os.path.lexists(path):
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise RuntimeError(f"private state file must not be a symlink: {path}")
        if not stat.S_ISREG(file_stat.st_mode):
            raise RuntimeError(f"private state path is not a regular file: {path}")
        ensure_current_user_owner(file_stat, "private state file", path)
    elif create:
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)
    else:
        raise FileNotFoundError(f"private state file is missing: {path}")
    path.chmod(0o600)


def write_private_text(path: Path, text: str) -> None:
    ensure_private_file(path, create=True)
    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
        state_file.write(text)
    path.chmod(0o600)


def remove_private_file(path: Path) -> None:
    if not os.path.lexists(path):
        return
    ensure_private_file(path, create=False)
    path.unlink()


def ensure_current_user_owner(
    file_stat: os.stat_result,
    description: str,
    path: Path,
) -> None:
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise PermissionError(f"{description} is not owned by this user: {path}")


@contextmanager
def memo_lock(memo_path: Path):
    lock_dir = locks_dir()
    key = hashlib.sha256(str(memo_path.resolve()).encode("utf-8")).hexdigest()
    lock_path = lock_dir / f"{key}.lock"
    ensure_private_file(lock_path, create=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0)
        if os.name == "nt":
            if lock_file.read(1) == b"":
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock_file.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def state_key(session_id: str, turn_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{session_id}-{turn_id}")


def state_path(session_id: str, turn_id: str) -> Path:
    return state_dir() / f"{state_key(session_id, turn_id)}.json"


def job_path(session_id: str, turn_id: str) -> Path:
    return jobs_dir() / f"{state_key(session_id, turn_id)}.json"


def log_path(session_id: str, turn_id: str) -> Path:
    return logs_dir() / f"{state_key(session_id, turn_id)}.log"


def ensure_managed_job_path(path: Path) -> None:
    if path.parent != jobs_dir():
        raise ValueError(f"summary job path is outside the private jobs directory: {path}")


def write_state(
    session_id: str,
    turn_id: str,
    memo_path: Path,
    *,
    summary_status: str,
) -> None:
    write_private_text(
        state_path(session_id, turn_id),
        json.dumps(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "memo_path": str(memo_path),
                "summary_status": summary_status,
            },
            ensure_ascii=False,
        ),
    )


def read_state(session_id: str, turn_id: str) -> dict[str, object] | None:
    path = state_path(session_id, turn_id)
    if not os.path.lexists(path):
        return None
    ensure_private_file(path, create=False)
    return json.loads(path.read_text(encoding="utf-8"))


def validation_failure_reason(
    memo_path: Path,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    require_done: bool = False,
) -> str | None:
    if not memo_path.exists():
        if require_done:
            return "memo.md がありません。今回の作業メモを戻してから終了してください。"
        return None

    text = memo_path.read_text(encoding="utf-8")
    malformed_failure = malformed_marker_failure_reason(text)
    if malformed_failure is not None:
        return malformed_failure

    if session_id is not None and turn_id is not None:
        done_blocks = find_done_blocks(text, session_id=session_id, turn_id=turn_id)
        if done_blocks:
            done_failure = done_blocks_failure_reason(done_blocks)
            if done_failure is None:
                return None
            return done_failure

        blocks = find_pending_blocks(text, session_id=session_id, turn_id=turn_id)
        if blocks:
            return pending_blocks_failure_reason(blocks)

        if require_done:
            return (
                "memo.md に今回の turn のメモ枠がありません。"
                "今回の要件、時系列の調査、候補、選定理由、判断までの経緯、推測判断時の追加確認点、試行錯誤、"
                "実装、確認、残課題を追記し、"
                f"`<!-- codex-memo:done session={session_id} turn={turn_id} -->` "
                f"から `{DONE_CLOSE}` までの完了ブロックとして残してください。"
            )
        return None

    done_blocks = list(find_done_blocks(text))
    done_failure = done_blocks_failure_reason(done_blocks)
    if done_failure is not None:
        return done_failure

    blocks = list(find_pending_blocks(text))
    if not blocks:
        if has_pending_marker(text):
            return (
                "memo.md に codex-memo:pending が残っていますが、開始/終了マーカーの対応が壊れています。"
                "該当箇所を整理し、完了済みなら done マーカーへ変更してください。"
            )
        return None

    return pending_blocks_failure_reason(blocks)


def malformed_marker_failure_reason(text: str) -> str | None:
    done_malformed = [block for block in find_done_blocks(text) if block.malformed]
    if done_malformed:
        return done_blocks_failure_reason(done_malformed)

    pending_malformed = [block for block in find_pending_blocks(text) if block.malformed]
    if pending_malformed:
        return pending_blocks_failure_reason(pending_malformed)
    return None


def done_blocks_failure_reason(blocks: list[PendingBlock]) -> str | None:
    problems: list[str] = []
    for index, block in enumerate(blocks, start=1):
        if block.malformed:
            problems.append(f"{index}件目の done メモに終了マーカーがありません")
            continue
        missing = missing_required_fields(block.body)
        if missing:
            problems.append(f"{index}件目の未完了項目: {', '.join(missing)}")

    if not problems:
        return None

    detail = " / ".join(problems)
    return (
        f"memo.md の今回分は done ですが詳細が不足しています。{detail}。"
        "各項目に根拠、候補、選定理由、時系列の経緯、推測判断時の追加確認点、失敗、想定と違った結果、"
        "方針変更、手戻り、ユーザーに確認した事項、確認結果を具体的に残してください。"
        "`なし` だけではなく、該当しない理由まで一文で記録してください。"
    )


def pending_blocks_failure_reason(blocks: list[PendingBlock]) -> str:
    problems: list[str] = []
    for index, block in enumerate(blocks, start=1):
        if block.malformed:
            problems.append(f"{index}件目の pending メモに終了マーカーがありません")
            continue
        missing = missing_required_fields(block.body)
        if missing:
            problems.append(f"{index}件目の未完了項目: {', '.join(missing)}")
        else:
            problems.append(
                f"{index}件目は項目入力済みですが pending マーカーが残っています"
            )

    detail = " / ".join(problems)
    return (
        f"memo.md の今回分が未完了です。{detail}。"
        "時系列の調査、推測判断時の追加確認点、想定と違った結果、方針変更、手戻り、"
        "ユーザーに確認した事項を含めて各項目を埋め、該当ブロックの開始マーカーを "
        f"`{DONE_OPEN_PREFIX} ... -->`、終了マーカーを `{DONE_CLOSE}` に変更してください。"
        "`なし` だけではなく、該当しない理由まで一文で記録してください。"
    )


def has_pending_marker(text: str) -> bool:
    return "codex-memo:pending" in text


def has_done_marker(text: str, session_id: str, turn_id: str) -> bool:
    return bool(find_done_blocks(text, session_id=session_id, turn_id=turn_id))


def marker_meta(raw_meta: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in raw_meta.split():
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key] = value
    return values


def find_pending_blocks(
    text: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> list[PendingBlock]:
    blocks: list[PendingBlock] = []
    for match in PENDING_OPEN_RE.finditer(text):
        meta = match.group("meta").strip()
        if session_id is not None or turn_id is not None:
            values = marker_meta(meta)
            if session_id is not None and values.get("session") != session_id:
                continue
            if turn_id is not None and values.get("turn") != turn_id:
                continue

        close_index = text.find(PENDING_CLOSE, match.end())
        next_open = PENDING_OPEN_RE.search(text, match.end())
        if close_index == -1 or (
            next_open is not None and next_open.start() < close_index
        ):
            blocks.append(PendingBlock(meta=meta, body="", malformed=True))
            continue
        blocks.append(
            PendingBlock(
                meta=meta,
                body=text[match.end() : close_index],
            )
        )
    return blocks


def find_done_blocks(
    text: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> list[PendingBlock]:
    blocks: list[PendingBlock] = []
    for match in DONE_OPEN_RE.finditer(text):
        meta = match.group("meta").strip()
        if session_id is not None or turn_id is not None:
            values = marker_meta(meta)
            if session_id is not None and values.get("session") != session_id:
                continue
            if turn_id is not None and values.get("turn") != turn_id:
                continue

        close_index = text.find(DONE_CLOSE, match.end())
        next_open = DONE_OPEN_RE.search(text, match.end())
        if close_index == -1 or (
            next_open is not None and next_open.start() < close_index
        ):
            blocks.append(PendingBlock(meta=meta, body="", malformed=True))
            continue
        blocks.append(
            PendingBlock(
                meta=meta,
                body=text[match.end() : close_index],
            )
        )
    return blocks


def missing_required_fields(body: str) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_FIELDS:
        pattern = re.compile(
            rf"(?m)^[ \t]*[-*][ \t]*{re.escape(field)}[ \t]*[:：][ \t]*(?P<value>.*)$"
        )
        match = pattern.search(body)
        if match is None:
            missing.append(field)
            continue
        value = normalize_value(match.group("value"))
        if is_placeholder(value):
            missing.append(field)
            continue
        if len(value) < MIN_FIELD_VALUE_CHARS:
            missing.append(f"{field}（具体性不足）")
    return missing


def normalize_value(value: str) -> str:
    return value.strip().strip("`").strip()


def is_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return lowered in PLACEHOLDER_VALUES or lowered.startswith("todo")


def read_text_or_empty(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if len(sys.argv) > 1 and sys.argv[1] == "stop":
            print(f"memo guard failed: {error}", file=sys.stderr)
            raise SystemExit(2)
        raise
