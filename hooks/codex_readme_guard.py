#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import difflib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path


DEFAULT_MAX_CHANGED_LINES = 50
DEFAULT_BINARY_FILE_WEIGHT = 1000
DEFAULT_STATE_DIR = Path(tempfile.gettempdir()) / "codex-readme-guard"
DEFAULT_STATE_MAX_AGE_SECONDS = 24 * 60 * 60
TEMPLATE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")
README_NO_UPDATE_DECISION = "README更新判断: 不要"
README_NO_UPDATE_REASON_PREFIX = "理由:"
README_NO_UPDATE_MIN_REASON_LENGTH = 12
README_NO_UPDATE_REASON_PLACEHOLDER = "<READMEを更新しない具体的な理由>"
README_NO_UPDATE_GENERIC_REASONS = frozenset(
    {
        "readme更新は必要ありません",
        "readmeの更新は必要ありません",
        "readmeを更新する必要はありません",
        "readme更新不要",
        "readme更新不要です",
        "readmeの更新は不要です",
        "readme更新は不要です",
    }
)


class GuardError(RuntimeError):
    pass


def main() -> int:
    usage = "usage: codex_readme_guard.py start|stop"
    if len(sys.argv) != 2 or sys.argv[1] not in {"start", "stop"}:
        raise SystemExit(usage)

    payload = read_payload()
    if sys.argv[1] == "start":
        return record_turn_start(payload)
    return check_turn_stop(payload)


def record_turn_start(payload: dict[str, object]) -> int:
    require_event(payload, "UserPromptSubmit")
    if is_subagent_payload(payload) or is_plan_mode_payload(payload):
        return 0

    cleanup_expired_states(ensure_state_directory())
    discard_turn_state(payload)
    repo_root = resolve_repo_root(Path(str(payload["cwd"])))
    state = {
        "repo_root": None if repo_root is None else str(repo_root),
        "base_head": None,
        "initial_dirty_files": {},
        "excluded_dirty_paths": [],
    }
    if repo_root is not None:
        state["base_head"] = current_head(repo_root)
        initial_dirty_paths = dirty_paths(repo_root)
        snapshot_paths = {
            path for path in initial_dirty_paths if not is_secret_env_path(path)
        }
        state["excluded_dirty_paths"] = sorted(initial_dirty_paths - snapshot_paths)
        state["initial_dirty_files"] = capture_dirty_files(
            payload,
            repo_root,
            snapshot_paths,
        )

    write_state(payload, state)
    return 0


def check_turn_stop(payload: dict[str, object]) -> int:
    require_event(payload, "Stop")
    reason = evaluate_turn_stop(payload)
    if reason is not None:
        print_block_decision(reason)
    return 0


def evaluate_turn_stop(payload: dict[str, object]) -> str | None:
    if is_subagent_payload(payload):
        return None

    state = read_state(payload)
    if state is None:
        return None

    repo_root_raw = state["repo_root"]
    if repo_root_raw is None:
        remove_state(payload)
        return None

    repo_root = Path(str(repo_root_raw))
    current_root = resolve_repo_root(Path(str(payload["cwd"])))
    if current_root != repo_root:
        raise GuardError(
            f"repository changed during turn: start={repo_root} stop={current_root}"
        )

    initial_dirty_files = state["initial_dirty_files"]
    if not isinstance(initial_dirty_files, dict):
        raise GuardError("initial_dirty_files must be an object")
    excluded_paths = {str(path) for path in initial_dirty_files}
    excluded_paths.update(required_string_list(state, "excluded_dirty_paths"))
    binary_weight = positive_int_from_env(
        "README_HOOK_BINARY_FILE_WEIGHT",
        DEFAULT_BINARY_FILE_WEIGHT,
    )
    changes = changed_files_since_start(
        repo_root,
        None if state["base_head"] is None else str(state["base_head"]),
        excluded_paths,
        binary_weight,
    )
    add_dirty_path_changes_since_start(
        changes,
        payload,
        repo_root,
        initial_dirty_files,
        binary_weight,
    )
    changed_lines = sum(changes.values())
    threshold = positive_int_from_env(
        "README_HOOK_MAX_CHANGED_LINES",
        DEFAULT_MAX_CHANGED_LINES,
    )

    if (
        changed_lines < threshold
        or any(is_readme_path(path) for path in changes)
        or explicit_no_readme_update_reason(payload) is not None
    ):
        remove_state(payload)
        return None

    changed_preview = "\n".join(f"- {path}" for path in sorted(changes)[:10])
    reason = (
        "README freshness check failed at main-agent completion. "
        f"This turn changed {changed_lines} lines (threshold: {threshold}) "
        "without changing a README. Review the full turn diff. Update the relevant "
        "README when behavior, usage, or documented project status changed. If no "
        "README update is needed, use the two lines below as the final two lines of "
        "the response, outside a code block. Replace the placeholder with a specific "
        "reason of at least 12 characters:\n"
        f"{README_NO_UPDATE_DECISION}\n"
        f"{README_NO_UPDATE_REASON_PREFIX} {README_NO_UPDATE_REASON_PLACEHOLDER}\n"
        f"Changed paths:\n{changed_preview}"
    )
    return reason


def explicit_no_readme_update_reason(payload: dict[str, object]) -> str | None:
    raw_message = payload.get("last_assistant_message")
    if raw_message is None:
        return None
    if not isinstance(raw_message, str):
        raise GuardError("last_assistant_message must be a string or null")

    lines = raw_message.rstrip().splitlines()
    if len(lines) < 2 or lines[-2] != README_NO_UPDATE_DECISION:
        return None
    if is_inside_markdown_fence(lines, len(lines) - 2):
        return None

    reason_line = lines[-1]
    if not reason_line.startswith(README_NO_UPDATE_REASON_PREFIX):
        return None
    reason = reason_line.removeprefix(README_NO_UPDATE_REASON_PREFIX).strip()
    if (
        len(reason) < README_NO_UPDATE_MIN_REASON_LENGTH
        or reason == README_NO_UPDATE_REASON_PLACEHOLDER
        or normalize_no_update_reason(reason) in README_NO_UPDATE_GENERIC_REASONS
    ):
        return None
    return reason


def is_inside_markdown_fence(lines: list[str], target_index: int) -> bool:
    open_fence: tuple[str, int] | None = None
    for line in lines[:target_index]:
        fence = markdown_fence_run(line)
        if fence is None:
            continue
        marker, length, remainder = fence
        if open_fence is None:
            open_fence = (marker, length)
            continue
        open_marker, open_length = open_fence
        if marker == open_marker and length >= open_length and remainder.strip() == "":
            open_fence = None
    return open_fence is not None


def markdown_fence_run(line: str) -> tuple[str, int, str] | None:
    indentation = len(line) - len(line.lstrip(" "))
    if indentation > 3:
        return None
    stripped = line[indentation:]
    if stripped == "" or stripped[0] not in {"`", "~"}:
        return None
    marker = stripped[0]
    length = len(stripped) - len(stripped.lstrip(marker))
    if length < 3:
        return None
    return marker, length, stripped[length:]


def normalize_no_update_reason(reason: str) -> str:
    normalized = unicodedata.normalize("NFKC", reason).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def print_block_decision(reason: str) -> None:
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )


def read_payload() -> dict[str, object]:
    raw = sys.stdin.read()
    if raw.strip() == "":
        raise GuardError("hook payload is empty")
    payload = json.loads(raw)
    for key in ("cwd", "session_id", "turn_id", "hook_event_name"):
        if key not in payload:
            raise GuardError(f"hook payload missing required key: {key}")
    return payload


def require_event(payload: dict[str, object], expected: str) -> None:
    actual = str(payload["hook_event_name"])
    if actual != expected:
        raise GuardError(f"expected hook event {expected}, got {actual}")


def is_subagent_payload(payload: dict[str, object]) -> bool:
    return payload.get("agent_id") is not None or payload.get("agent_type") is not None


def is_plan_mode_payload(payload: dict[str, object]) -> bool:
    return payload.get("permission_mode") == "plan"


def resolve_repo_root(cwd: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        if "not a git repository" in result.stderr:
            return None
        raise GuardError(f"git rev-parse failed: {result.stderr.strip()}")
    root = result.stdout.strip()
    if root == "":
        raise GuardError("git returned an empty repository root")
    return Path(root).resolve()


def current_head(repo_root: Path) -> str | None:
    result = run_git_allow_failure(["rev-parse", "--verify", "HEAD"], repo_root)
    if result.returncode != 0:
        return None
    head = result.stdout.strip()
    if head == "":
        raise GuardError("git returned an empty HEAD")
    return head


def dirty_paths(repo_root: Path) -> set[str]:
    paths = set(git_paths(["diff", "--name-only", "-z"], repo_root))
    paths.update(git_paths(["diff", "--cached", "--name-only", "-z"], repo_root))
    paths.update(
        git_paths(["ls-files", "--others", "--exclude-standard", "-z"], repo_root)
    )
    return paths


def capture_dirty_files(
    payload: dict[str, object],
    repo_root: Path,
    paths: set[str],
) -> dict[str, dict[str, object]]:
    snapshots: dict[str, dict[str, object]] = {}
    snapshot_dir = ensure_snapshot_directory(payload)
    for path in sorted(paths):
        kind, executable, data = path_state(repo_root / path)
        snapshot_name: str | None = None
        if data is not None:
            snapshot_name = hashlib.sha256(os.fsencode(path)).hexdigest() + ".bin"
            snapshot_path = snapshot_dir / snapshot_name
            write_private_bytes(snapshot_path, data, "snapshot file")
        snapshots[path] = {
            "kind": kind,
            "executable": executable,
            "snapshot": snapshot_name,
        }
    return snapshots


def path_state(path: Path) -> tuple[str, bool, bytes | None]:
    if not os.path.lexists(path):
        return "missing", False, None
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode):
        return "symlink", False, os.fsencode(os.readlink(path))
    if stat.S_ISREG(file_stat.st_mode):
        executable = bool(file_stat.st_mode & 0o111)
        return "file", executable, path.read_bytes()
    return "other", False, None


def changed_files_since_start(
    repo_root: Path,
    base_head: str | None,
    excluded_paths: set[str],
    binary_weight: int,
) -> dict[str, int]:
    changes: dict[str, int] = {}
    if base_head is None:
        add_numstat_changes(
            changes,
            git_numstat(
                ["diff", "--cached", "--numstat", "--no-renames", "-z"],
                repo_root,
            ),
            excluded_paths,
            binary_weight,
        )
        add_numstat_changes(
            changes,
            git_numstat(["diff", "--numstat", "--no-renames", "-z"], repo_root),
            excluded_paths,
            binary_weight,
        )
    else:
        add_numstat_changes(
            changes,
            git_numstat(
                ["diff", "--numstat", "--no-renames", "-z", base_head, "--"],
                repo_root,
            ),
            excluded_paths,
            binary_weight,
        )

    for path in git_paths(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        repo_root,
    ):
        if path in excluded_paths:
            continue
        changes[path] = untracked_file_weight(repo_root / path, binary_weight)
    return changes


def add_numstat_changes(
    changes: dict[str, int],
    records: list[tuple[str, str, str]],
    excluded_paths: set[str],
    binary_weight: int,
) -> None:
    for added, deleted, path in records:
        if path in excluded_paths:
            continue
        if added == "-" or deleted == "-":
            weight = binary_weight
        else:
            try:
                weight = int(added) + int(deleted)
            except ValueError as error:
                raise GuardError(
                    f"unexpected git numstat counts: {added!r}, {deleted!r}, {path!r}"
                ) from error
        changes[path] = changes.get(path, 0) + weight


def add_dirty_path_changes_since_start(
    changes: dict[str, int],
    payload: dict[str, object],
    repo_root: Path,
    initial_dirty_files: dict[object, object],
    binary_weight: int,
) -> None:
    for raw_path, raw_snapshot in initial_dirty_files.items():
        path = str(raw_path)
        if not isinstance(raw_snapshot, dict):
            raise GuardError(f"dirty snapshot must be an object: {path!r}")
        before_kind = required_string(raw_snapshot, "kind", path)
        before_executable = required_bool(raw_snapshot, "executable", path)
        before_data = read_snapshot(payload, raw_snapshot, path)
        after_kind, after_executable, after_data = path_state(repo_root / path)
        weight = path_change_weight(
            before_kind,
            before_executable,
            before_data,
            after_kind,
            after_executable,
            after_data,
            binary_weight,
        )
        if weight > 0:
            changes[path] = weight


def required_string(snapshot: dict[object, object], key: str, path: str) -> str:
    if key not in snapshot or not isinstance(snapshot[key], str):
        raise GuardError(f"dirty snapshot {path!r} has invalid {key}")
    return str(snapshot[key])


def required_bool(snapshot: dict[object, object], key: str, path: str) -> bool:
    if key not in snapshot or not isinstance(snapshot[key], bool):
        raise GuardError(f"dirty snapshot {path!r} has invalid {key}")
    return bool(snapshot[key])


def required_string_list(state: dict[str, object], key: str) -> list[str]:
    if key not in state or not isinstance(state[key], list):
        raise GuardError(f"state missing required string list: {key}")
    values = state[key]
    if not all(isinstance(value, str) for value in values):
        raise GuardError(f"state has invalid string list: {key}")
    return [str(value) for value in values]


def read_snapshot(
    payload: dict[str, object],
    snapshot: dict[object, object],
    path: str,
) -> bytes | None:
    if "snapshot" not in snapshot:
        raise GuardError(f"dirty snapshot {path!r} is missing snapshot")
    snapshot_name = snapshot["snapshot"]
    if snapshot_name is None:
        return None
    if not isinstance(snapshot_name, str) or Path(snapshot_name).name != snapshot_name:
        raise GuardError(f"dirty snapshot {path!r} has invalid snapshot filename")
    snapshot_dir = existing_snapshot_directory(payload)
    snapshot_path = snapshot_dir / snapshot_name
    if not os.path.lexists(snapshot_path):
        raise GuardError(f"dirty snapshot file is missing: {snapshot_path}")
    ensure_private_file(snapshot_path, "snapshot file")
    return snapshot_path.read_bytes()


def path_change_weight(
    before_kind: str,
    before_executable: bool,
    before_data: bytes | None,
    after_kind: str,
    after_executable: bool,
    after_data: bytes | None,
    binary_weight: int,
) -> int:
    if (
        before_kind == after_kind
        and before_executable == after_executable
        and before_data == after_data
    ):
        return 0
    if before_data is None:
        before_data = b""
    if after_data is None:
        after_data = b""
    if b"\x00" in before_data[:8192] or b"\x00" in after_data[:8192]:
        return binary_weight

    before_lines = before_data.splitlines()
    after_lines = after_data.splitlines()
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    changed_lines = 0
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_lines += (before_end - before_start) + (after_end - after_start)
    return max(changed_lines, 1)


def untracked_file_weight(path: Path, binary_weight: int) -> int:
    _kind, _executable, data = path_state(path)
    if data is None:
        return binary_weight
    if b"\x00" in data[:8192]:
        return binary_weight
    return len(data.splitlines())


def is_readme_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name == "readme" or name.startswith("readme.")


def is_secret_env_path(path: str) -> bool:
    name = Path(path).name.lower()
    if not (name.startswith(".env") or name.startswith(".dev.vars")):
        return False
    return not any(name.endswith(suffix) for suffix in TEMPLATE_ENV_SUFFIXES)


def positive_int_from_env(name: str, default: int) -> int:
    if name in os.environ:
        raw = os.environ[name]
        if raw == "":
            raise GuardError(f"{name} must not be empty")
        try:
            value = int(raw)
        except ValueError as error:
            raise GuardError(f"{name} must be an integer: {raw!r}") from error
    else:
        value = default
    if value <= 0:
        raise GuardError(f"{name} must be greater than 0: {value}")
    return value


def state_directory() -> Path:
    if "CODEX_README_GUARD_STATE_DIR" in os.environ:
        raw = os.environ["CODEX_README_GUARD_STATE_DIR"]
        if raw == "":
            raise GuardError("CODEX_README_GUARD_STATE_DIR must not be empty")
        return Path(raw)
    return DEFAULT_STATE_DIR


def turn_state_directory(payload: dict[str, object]) -> Path:
    key = f"{payload['session_id']}\0{payload['turn_id']}".encode()
    digest = hashlib.sha256(key).hexdigest()
    return state_directory() / digest


def state_path(payload: dict[str, object]) -> Path:
    return turn_state_directory(payload) / "state.json"


def write_state(payload: dict[str, object], state: dict[str, object]) -> None:
    ensure_turn_state_directory(payload)
    path = state_path(payload)
    if os.path.lexists(path):
        ensure_private_file(path, "state file")
    temporary = path.with_suffix(".tmp")
    write_private_bytes(
        temporary,
        json.dumps(state, sort_keys=True).encode("utf-8"),
        "temporary state file",
    )
    temporary.replace(path)
    ensure_private_file(path, "state file")


def read_state(payload: dict[str, object]) -> dict[str, object] | None:
    if not os.path.lexists(state_directory()):
        return None
    ensure_state_directory()
    if not os.path.lexists(turn_state_directory(payload)):
        return None
    ensure_turn_state_directory(payload)
    path = state_path(payload)
    if not os.path.lexists(path):
        return None
    ensure_private_file(path, "state file")
    state = json.loads(path.read_text(encoding="utf-8"))
    for key in (
        "repo_root",
        "base_head",
        "initial_dirty_files",
        "excluded_dirty_paths",
    ):
        if key not in state:
            raise GuardError(f"state missing required key: {key}")
    if not isinstance(state["initial_dirty_files"], dict):
        raise GuardError("initial_dirty_files must be an object")
    required_string_list(state, "excluded_dirty_paths")
    return state


def remove_state(payload: dict[str, object]) -> None:
    discard_turn_state(payload)


def discard_turn_state(payload: dict[str, object]) -> None:
    directory = turn_state_directory(payload)
    if not os.path.lexists(directory):
        return
    ensure_turn_state_directory(payload)
    if directory.is_dir():
        shutil.rmtree(directory)


def ensure_state_directory() -> Path:
    directory = state_directory()
    return ensure_private_directory(directory, "state directory", create=True)


def ensure_turn_state_directory(payload: dict[str, object]) -> Path:
    ensure_state_directory()
    return ensure_private_directory(
        turn_state_directory(payload),
        "turn state directory",
        create=True,
    )


def ensure_snapshot_directory(payload: dict[str, object]) -> Path:
    ensure_turn_state_directory(payload)
    return ensure_private_directory(
        turn_state_directory(payload) / "snapshots",
        "snapshot directory",
        create=True,
    )


def existing_snapshot_directory(payload: dict[str, object]) -> Path:
    ensure_turn_state_directory(payload)
    directory = turn_state_directory(payload) / "snapshots"
    if not os.path.lexists(directory):
        raise GuardError(f"snapshot directory is missing: {directory}")
    return ensure_private_directory(directory, "snapshot directory", create=False)


def ensure_private_directory(path: Path, description: str, create: bool) -> Path:
    if not os.path.lexists(path):
        if not create:
            raise GuardError(f"{description} is missing: {path}")
        path.mkdir(parents=True, mode=0o700)
    path_stat = path.lstat()
    if stat.S_ISLNK(path_stat.st_mode):
        raise GuardError(f"{description} must not be a symlink: {path}")
    if not stat.S_ISDIR(path_stat.st_mode):
        raise GuardError(f"{description} is not a directory: {path}")
    ensure_current_user_owner(path_stat, description, path)
    path.chmod(0o700)
    return path


def ensure_private_file(path: Path, description: str) -> None:
    path_stat = path.lstat()
    if stat.S_ISLNK(path_stat.st_mode):
        raise GuardError(f"{description} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise GuardError(f"{description} is not a regular file: {path}")
    ensure_current_user_owner(path_stat, description, path)
    path.chmod(0o600)


def ensure_current_user_owner(
    path_stat: os.stat_result,
    description: str,
    path: Path,
) -> None:
    if hasattr(os, "getuid") and path_stat.st_uid != os.getuid():
        raise GuardError(f"{description} is not owned by the current user: {path}")


def write_private_bytes(path: Path, data: bytes, description: str) -> None:
    if os.path.lexists(path):
        ensure_private_file(path, description)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = os.open(path, flags, 0o600)
    try:
        file_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise GuardError(f"{description} is not a regular file: {path}")
        ensure_current_user_owner(file_stat, description, path)
        if hasattr(os, "fchmod"):
            os.fchmod(file_descriptor, 0o600)
    except BaseException:
        os.close(file_descriptor)
        raise
    with os.fdopen(file_descriptor, "wb") as output:
        output.write(data)


def cleanup_expired_states(state_root: Path) -> None:
    expiry = time.time() - DEFAULT_STATE_MAX_AGE_SECONDS
    for child in state_root.iterdir():
        child_stat = child.lstat()
        if stat.S_ISLNK(child_stat.st_mode):
            raise GuardError(f"turn state directory must not be a symlink: {child}")
        if not stat.S_ISDIR(child_stat.st_mode):
            raise GuardError(f"state directory contains an invalid entry: {child}")
        ensure_current_user_owner(child_stat, "turn state directory", child)
        if child_stat.st_mtime < expiry:
            shutil.rmtree(child)


def git_paths(args: list[str], cwd: Path) -> list[str]:
    result = run_git_bytes(args, cwd)
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path != b""]


def git_numstat(args: list[str], cwd: Path) -> list[tuple[str, str, str]]:
    result = run_git_bytes(args, cwd)
    records: list[tuple[str, str, str]] = []
    for raw_record in result.stdout.split(b"\0"):
        if raw_record == b"":
            continue
        fields = raw_record.split(b"\t", 2)
        if len(fields) != 3:
            raise GuardError(f"unexpected git numstat record: {raw_record!r}")
        records.append(
            (
                fields[0].decode("ascii"),
                fields[1].decode("ascii"),
                os.fsdecode(fields[2]),
            )
        )
    return records


def run_git_bytes(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        command = " ".join(shlex.quote(part) for part in ["git", *args])
        stderr = os.fsdecode(result.stderr).strip()
        if stderr == "":
            stderr = "(no stderr)"
        raise GuardError(f"command failed: {command}\n{stderr}")
    return result


def run_git_allow_failure(
    args: list[str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (GuardError, json.JSONDecodeError, OSError) as error:
        print(f"codex-readme-guard: {error}", file=sys.stderr)
        sys.exit(1)
