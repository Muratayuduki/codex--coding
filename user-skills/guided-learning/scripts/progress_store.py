#!/usr/bin/env python3
"""Local, revisioned progress storage for the guided-learning skill."""

from __future__ import annotations

import argparse
import copy
from contextvars import ContextVar
from dataclasses import dataclass
import datetime as dt
import errno
import fcntl
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import secrets
import stat
import sys
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator


SCHEMA_VERSION = 1
TRACK_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,39})-[0-9a-f]{8}$")
TRACK_FIELDS = {
    "schema_version",
    "id",
    "revision",
    "status",
    "topic",
    "goal",
    "learner_profile",
    "sources",
    "destination",
    "nearby_steps",
    "current_step_id",
    "recommended_route",
    "mastery",
    "current_task",
    "history",
    "next_action",
    "created_at",
    "updated_at",
}
CREATE_FIELDS = {
    "topic",
    "goal",
    "learner_profile",
    "sources",
    "destination",
    "nearby_steps",
    "current_step_id",
    "recommended_route",
    "mastery",
    "current_task",
    "history",
    "next_action",
}
UPDATE_FIELDS = CREATE_FIELDS - {"history"}
HISTORY_INPUT_FIELDS = {
    "task",
    "learner_response",
    "feedback",
    "attainment",
}
HISTORY_FIELDS = HISTORY_INPUT_FIELDS | {"recorded_at"}
INDEX_FIELDS = {
    "schema_version",
    "active_track_id",
    "tracks",
    "archived",
    "updated_at",
}
METADATA_FIELDS = {
    "topic",
    "goal",
    "status",
    "revision",
    "updated_at",
}
TRANSACTION_FIELDS = {
    "schema_version",
    "operation",
    "before",
    "writes",
    "deletes",
    "created_at",
}
TRANSACTION_BEFORE_FIELDS = {"index", "files"}
TRANSACTION_OPERATIONS = {
    "initialize",
    "create",
    "update",
    "archive",
    "restore",
    "delete",
}


class StoreError(Exception):
    """A known storage or input error safe to render as structured JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details is not None:
            error["details"] = self.details
        return {"ok": False, "error": error}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def default_state_dir() -> Path:
    if "CODEX_HOME" in os.environ and os.environ["CODEX_HOME"]:
        codex_home = Path(os.environ["CODEX_HOME"])
    else:
        codex_home = Path.home() / ".codex"
    return codex_home / "state" / "guided-learning"


def _normalize_state_dir(value: str | os.PathLike[str] | None) -> Path:
    raw = default_state_dir() if value is None else Path(value)
    return Path(os.path.abspath(os.path.expanduser(os.fspath(raw))))


@dataclass(frozen=True)
class _ActiveStateRoot:
    display_root: Path
    root_fd: int
    tracks_fd: int
    archive_fd: int


_ACTIVE_STATE_ROOT: ContextVar[_ActiveStateRoot | None] = ContextVar(
    "guided_learning_state_root",
    default=None,
)


def _state_path_binding(path: Path) -> tuple[int, str | None] | None:
    active = _ACTIVE_STATE_ROOT.get()
    if active is None:
        return None
    try:
        relative = path.relative_to(active.display_root)
    except ValueError:
        return None
    parts = relative.parts
    if not parts:
        return active.root_fd, None
    if len(parts) == 1:
        if parts[0] == "tracks":
            return active.tracks_fd, None
        if parts[0] == "archive":
            return active.archive_fd, None
        return active.root_fd, parts[0]
    if len(parts) == 2 and parts[0] == "tracks":
        return active.tracks_fd, parts[1]
    if len(parts) == 2 and parts[0] == "archive":
        return active.archive_fd, parts[1]
    raise StoreError(
        "invalid_state_path",
        "State operation attempted to leave the fixed state directories.",
        details={"path": str(path)},
    )


def _path_lstat(path: Path) -> os.stat_result:
    binding = _state_path_binding(path)
    if binding is None:
        return os.lstat(path)
    directory_fd, name = binding
    if name is None:
        return os.fstat(directory_fd)
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def _path_exists(path: Path) -> bool:
    try:
        _path_lstat(path)
    except FileNotFoundError:
        return False
    return True


def _path_open(path: Path, flags: int, mode: int | None = None) -> int:
    binding = _state_path_binding(path)
    if binding is None:
        if mode is None:
            return os.open(path, flags)
        return os.open(path, flags, mode)
    directory_fd, name = binding
    if name is None:
        return os.dup(directory_fd)
    if mode is None:
        return os.open(name, flags, dir_fd=directory_fd)
    return os.open(name, flags, mode, dir_fd=directory_fd)


def _path_chmod(path: Path, mode: int) -> None:
    binding = _state_path_binding(path)
    if binding is None:
        os.chmod(path, mode, follow_symlinks=False)
        return
    directory_fd, name = binding
    if name is None:
        os.fchmod(directory_fd, mode)
        return
    os.chmod(
        name,
        mode,
        dir_fd=directory_fd,
        follow_symlinks=False,
    )


def _path_unlink(path: Path) -> None:
    binding = _state_path_binding(path)
    if binding is None:
        path.unlink()
        return
    directory_fd, name = binding
    if name is None:
        raise StoreError(
            "invalid_state_path",
            "State directories cannot be unlinked as files.",
            details={"path": str(path)},
        )
    os.unlink(name, dir_fd=directory_fd)


def _path_replace(source: Path, target: Path) -> None:
    source_binding = _state_path_binding(source)
    target_binding = _state_path_binding(target)
    if source_binding is None and target_binding is None:
        os.replace(source, target)
        return
    if source_binding is None or target_binding is None:
        raise StoreError(
            "invalid_state_path",
            "Atomic replacement cannot cross the fixed state root.",
        )
    source_fd, source_name = source_binding
    target_fd, target_name = target_binding
    if source_name is None or target_name is None:
        raise StoreError(
            "invalid_state_path",
            "Atomic replacement requires file paths.",
        )
    os.replace(
        source_name,
        target_name,
        src_dir_fd=source_fd,
        dst_dir_fd=target_fd,
    )


def _path_listdir(path: Path) -> list[str]:
    binding = _state_path_binding(path)
    if binding is None:
        return os.listdir(path)
    directory_fd, name = binding
    if name is not None:
        raise StoreError(
            "invalid_state_path",
            "Expected a fixed state directory.",
            details={"path": str(path)},
        )
    return os.listdir(directory_fd)


def _path_mkstemp(target: Path) -> tuple[int, Path]:
    binding = _state_path_binding(target.parent)
    if binding is None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        return descriptor, Path(temporary_name)
    directory_fd, name = binding
    if name is not None:
        raise StoreError(
            "invalid_state_path",
            "Temporary state file parent is not a fixed directory.",
            details={"path": str(target.parent)},
        )
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    for _ in range(32):
        temporary_name = f".{target.name}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return descriptor, target.parent / temporary_name
    raise StoreError(
        "state_write_failed",
        "Could not allocate a unique temporary state file.",
        details={"path": str(target)},
    )


def _secure_open_directory_component(
    parent_fd: int,
    component: str,
    display_path: Path,
    *,
    private: bool,
) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    created = False
    require_private = private
    for _ in range(8):
        try:
            descriptor = os.open(component, flags, dir_fd=parent_fd)
            break
        except FileNotFoundError:
            require_private = True
            parent_info = os.fstat(parent_fd)
            parent_mode = stat.S_IMODE(parent_info.st_mode)
            if parent_mode & 0o022 and not parent_mode & stat.S_ISVTX:
                raise StoreError(
                    "unsafe_state_ancestor",
                    "Cannot create state below a shared non-sticky directory.",
                    details={
                        "path": str(display_path.parent),
                        "mode": oct(parent_mode),
                    },
                )
            try:
                os.mkdir(component, 0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                continue
            except OSError as error:
                raise _filesystem_error(
                    "state_directory_create_failed",
                    "Could not create state directory component.",
                    display_path,
                    error,
                ) from error
        except OSError as error:
            try:
                info = os.stat(
                    component,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError:
                info = None
            if info is not None and stat.S_ISLNK(info.st_mode):
                raise StoreError(
                    "symlink_rejected",
                    "Symbolic links are not allowed in state path components.",
                    details={"path": str(display_path)},
                ) from error
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise StoreError(
                    "invalid_state_path",
                    "State path component is not a directory.",
                    details={"path": str(display_path)},
                ) from error
            raise _filesystem_error(
                "state_access_failed",
                "Could not securely open state directory component.",
                display_path,
                error,
            ) from error
    else:
        raise StoreError(
            "state_directory_create_failed",
            "State directory component changed repeatedly during creation.",
            details={"path": str(display_path)},
        )
    if created:
        os.fchmod(descriptor, 0o700)
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise StoreError(
            "invalid_state_path",
            "Opened state path component is not a directory.",
            details={"path": str(display_path)},
        )
    if require_private:
        try:
            _validate_owner_and_mode(info, display_path, expected_mode=0o700)
        except BaseException:
            os.close(descriptor)
            raise
    return descriptor


def _open_active_state_root(value: str | os.PathLike[str] | None) -> _ActiveStateRoot:
    display_root = _normalize_state_dir(value)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    current_fd = os.open(display_root.anchor, flags)
    current_path = Path(display_root.anchor)
    try:
        for position, component in enumerate(display_root.parts[1:]):
            current_path /= component
            next_fd = _secure_open_directory_component(
                current_fd,
                component,
                current_path,
                private=position == len(display_root.parts[1:]) - 1,
            )
            os.close(current_fd)
            current_fd = next_fd
        root_fd = current_fd
        tracks_fd = _secure_open_directory_component(
            root_fd,
            "tracks",
            display_root / "tracks",
            private=True,
        )
        try:
            archive_fd = _secure_open_directory_component(
                root_fd,
                "archive",
                display_root / "archive",
                private=True,
            )
        except BaseException:
            os.close(tracks_fd)
            raise
    except BaseException:
        os.close(current_fd)
        raise
    return _ActiveStateRoot(
        display_root=display_root,
        root_fd=root_fd,
        tracks_fd=tracks_fd,
        archive_fd=archive_fd,
    )


def _reject_symlink_components(path: Path) -> None:
    """Reject every existing symlink component before traversing a state path."""

    if _state_path_binding(path) is not None:
        return
    if not path.is_absolute():
        raise ValueError("State paths must be normalized to absolute paths.")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise _filesystem_error(
                "state_access_failed",
                "Could not inspect a state path component.",
                current,
                error,
            ) from error
        if stat.S_ISLNK(info.st_mode):
            raise StoreError(
                "symlink_rejected",
                "Symbolic links are not allowed in state path components.",
                details={"path": str(current)},
            )


def _raise_wrong_type(path: str, expected: str) -> None:
    raise StoreError(
        "invalid_schema",
        f"{path} must be {expected}.",
        details={"path": path, "expected": expected},
    )


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _raise_wrong_type(path, "an object")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _raise_wrong_type(path, "an array")
    return value


def _require_nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _raise_wrong_type(path, "a non-empty string")
    return value


def _require_nullable_string(value: Any, path: str) -> str | None:
    if value is not None and not isinstance(value, str):
        _raise_wrong_type(path, "a string or null")
    return value


def _require_revision(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _raise_wrong_type(path, "a non-negative integer")
    return value


def _reject_unknown_fields(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise StoreError(
            "invalid_schema",
            f"{path} contains unknown fields.",
            details={"path": path, "unknown_fields": unknown},
        )


def _require_fields(value: dict[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise StoreError(
            "invalid_schema",
            f"{path} is missing required fields.",
            details={"path": path, "missing_fields": missing},
        )


def _optional(value: dict[str, Any], key: str, default: Any) -> Any:
    if key in value:
        return value[key]
    return copy.deepcopy(default)


def _validate_json_unicode(value: Any, path: str) -> None:
    if isinstance(value, str):
        for position, character in enumerate(value):
            code_point = ord(character)
            if 0xD800 <= code_point <= 0xDFFF:
                raise StoreError(
                    "invalid_unicode",
                    "JSON strings must not contain lone surrogate code points.",
                    details={
                        "path": path,
                        "position": position,
                        "code_point": f"U+{code_point:04X}",
                    },
                )
        return
    if isinstance(value, list):
        for position, item in enumerate(value):
            _validate_json_unicode(item, f"{path}[{position}]")
        return
    if isinstance(value, dict):
        for position, (key, item) in enumerate(value.items()):
            _validate_json_unicode(key, f"{path}.key[{position}]")
            _validate_json_unicode(item, f"{path}.value[{position}]")


def _validate_track_id(track_id: str) -> str:
    if not isinstance(track_id, str) or not TRACK_ID_PATTERN.fullmatch(track_id):
        raise StoreError(
            "invalid_track_id",
            "Track ID is not a safe generated identifier.",
            details={"track_id": track_id},
        )
    return track_id


def _slugify(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    if not slug:
        slug = "track"
    return slug[:40].rstrip("-")


def _new_track_id(topic: str) -> str:
    return f"{_slugify(topic)}-{secrets.token_hex(4)}"


def _filesystem_error(
    code: str, message: str, path: Path, error: OSError
) -> StoreError:
    details: dict[str, Any] = {
        "path": str(path),
        "reason": str(error),
    }
    if error.errno is not None:
        details["errno"] = error.errno
    return StoreError(code, message, details=details)


def _validate_owner_and_mode(
    info: os.stat_result,
    path: Path,
    *,
    expected_mode: int,
) -> None:
    actual_mode = stat.S_IMODE(info.st_mode)
    current_uid = os.geteuid()
    if info.st_uid != current_uid or actual_mode != expected_mode:
        raise StoreError(
            "invalid_permissions",
            "State path must be owned by the current user with private permissions.",
            details={
                "path": str(path),
                "expected_owner": current_uid,
                "actual_owner": info.st_uid,
                "expected_mode": oct(expected_mode),
                "actual_mode": oct(actual_mode),
            },
        )


def _assert_directory(path: Path) -> None:
    _reject_symlink_components(path)
    try:
        info = _path_lstat(path)
    except FileNotFoundError:
        raise StoreError(
            "state_path_missing",
            "Required state directory does not exist.",
            details={"path": str(path)},
        ) from None
    except OSError as error:
        raise _filesystem_error(
            "state_access_failed",
            "Could not inspect state directory.",
            path,
            error,
        ) from error
    if stat.S_ISLNK(info.st_mode):
        raise StoreError(
            "symlink_rejected",
            "Symbolic links are not allowed in the state store.",
            details={"path": str(path)},
        )
    if not stat.S_ISDIR(info.st_mode):
        raise StoreError(
            "invalid_state_path",
            "Expected a directory in the state store.",
            details={"path": str(path)},
        )
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = _path_open(path, flags)
    except PermissionError as error:
        raise _filesystem_error(
            "invalid_permissions",
            "Could not open private state directory.",
            path,
            error,
        ) from error
    except OSError as error:
        raise _filesystem_error(
            "state_access_failed",
            "Could not open state directory.",
            path,
            error,
        ) from error
    try:
        try:
            opened_info = os.fstat(descriptor)
            if not stat.S_ISDIR(opened_info.st_mode):
                raise StoreError(
                    "invalid_state_path",
                    "Opened state path is not a directory.",
                    details={"path": str(path)},
                )
            _validate_owner_and_mode(
                opened_info,
                path,
                expected_mode=0o700,
            )
        except OSError as error:
            raise _filesystem_error(
                "state_access_failed",
                "Could not inspect opened state directory.",
                path,
                error,
            ) from error
    finally:
        os.close(descriptor)


@contextmanager
def _prepare_state_dir(
    value: str | os.PathLike[str] | None,
) -> Iterator[Path]:
    active = _open_active_state_root(value)
    token = _ACTIVE_STATE_ROOT.set(active)
    try:
        yield active.display_root
    finally:
        _ACTIVE_STATE_ROOT.reset(token)
        os.close(active.archive_fd)
        os.close(active.tracks_fd)
        os.close(active.root_fd)


def _assert_regular_file(path: Path) -> None:
    _reject_symlink_components(path)
    try:
        info = _path_lstat(path)
    except FileNotFoundError:
        raise StoreError(
            "state_file_missing",
            "Required state file does not exist.",
            details={"path": str(path)},
        ) from None
    except OSError as error:
        raise _filesystem_error(
            "state_access_failed",
            "Could not inspect state file.",
            path,
            error,
        ) from error
    if stat.S_ISLNK(info.st_mode):
        raise StoreError(
            "symlink_rejected",
            "Symbolic links are not allowed in the state store.",
            details={"path": str(path)},
        )
    if not stat.S_ISREG(info.st_mode):
        raise StoreError(
            "invalid_state_path",
            "Expected a regular file in the state store.",
            details={"path": str(path)},
        )
    _validate_owner_and_mode(info, path, expected_mode=0o600)


def _read_json(path: Path) -> dict[str, Any]:
    _assert_regular_file(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = _path_open(path, flags)
    except PermissionError as error:
        raise _filesystem_error(
            "invalid_permissions",
            "Could not open private state file.",
            path,
            error,
        ) from error
    except OSError as error:
        raise _filesystem_error(
            "state_read_failed",
            "Could not open state file.",
            path,
            error,
        ) from error
    try:
        opened_info = os.fstat(descriptor)
        if not stat.S_ISREG(opened_info.st_mode):
            raise StoreError(
                "invalid_state_path",
                "Opened state path is not a regular file.",
                details={"path": str(path)},
            )
        _validate_owner_and_mode(opened_info, path, expected_mode=0o600)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            try:
                value = json.load(handle)
            except json.JSONDecodeError as error:
                raise StoreError(
                    "corrupted_json",
                    "State file contains invalid JSON.",
                    details={
                        "path": str(path),
                        "line": error.lineno,
                        "column": error.colno,
                    },
                ) from None
            except UnicodeDecodeError as error:
                raise StoreError(
                    "invalid_encoding",
                    "State file is not valid UTF-8.",
                    details={
                        "path": str(path),
                        "start": error.start,
                        "end": error.end,
                    },
                ) from None
            except OSError as error:
                raise _filesystem_error(
                    "state_read_failed",
                    "Could not read state file.",
                    path,
                    error,
                ) from error
    except OSError as error:
        raise _filesystem_error(
            "state_read_failed",
            "Could not inspect or read opened state file.",
            path,
            error,
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _validate_json_unicode(value, str(path))
    return _require_object(value, str(path))


def _fsync_directory(path: Path) -> None:
    _assert_directory(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = _path_open(path, flags)
    try:
        opened_info = os.fstat(descriptor)
        _validate_owner_and_mode(opened_info, path, expected_mode=0o700)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace one file; transactions cover post-replace failures."""

    _validate_json_unicode(value, "state write")
    _assert_directory(path.parent)
    if _path_exists(path):
        _assert_regular_file(path)
    try:
        descriptor, temporary = _path_mkstemp(path)
    except OSError as error:
        raise _filesystem_error(
            "state_write_failed",
            "Could not create temporary state file.",
            path,
            error,
        ) from error
    replaced = False
    primary_error: BaseException | None = None
    primary_cause: OSError | None = None
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _path_replace(temporary, path)
        replaced = True
        _path_chmod(path, 0o600)
        _fsync_directory(path.parent)
    except OSError as error:
        primary_error = _filesystem_error(
            "state_write_failed",
            "Could not atomically write state file.",
            path,
            error,
        )
        primary_cause = error
    except BaseException as error:
        primary_error = error

    cleanup_errors: list[tuple[Path, OSError]] = []
    if descriptor >= 0:
        try:
            os.close(descriptor)
        except OSError as error:
            cleanup_errors.append((path, error))
    if not replaced:
        try:
            _path_unlink(temporary)
        except FileNotFoundError:
            pass
        except OSError as error:
            cleanup_errors.append((temporary, error))

    if cleanup_errors:
        cleanup_path, cleanup_error = cleanup_errors[0]
        cleanup_details = {
            "path": str(cleanup_path),
            "reason": str(cleanup_error),
        }
        if cleanup_error.errno is not None:
            cleanup_details["errno"] = cleanup_error.errno
        if isinstance(primary_error, StoreError):
            details = (
                copy.deepcopy(primary_error.details)
                if primary_error.details is not None
                else {}
            )
            details["cleanup_error"] = cleanup_details
            primary_error.details = details
            primary_error.add_note(
                f"Temporary state cleanup also failed: {cleanup_error}"
            )
        elif primary_error is not None:
            primary_error.add_note(
                f"Temporary state cleanup failed at {cleanup_path}: {cleanup_error}"
            )
        else:
            raise StoreError(
                "state_cleanup_failed",
                "State write completed but temporary cleanup failed.",
                details={"cleanup_error": cleanup_details},
            ) from cleanup_error

    if primary_error is not None:
        if primary_cause is not None:
            raise primary_error from primary_cause
        raise primary_error


@contextmanager
def _store_lock(root: Path) -> Iterator[None]:
    lock_path = root / ".lock"
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created = False
    try:
        descriptor = _path_open(
            lock_path,
            flags | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        created = True
    except FileExistsError:
        _assert_regular_file(lock_path)
        try:
            descriptor = _path_open(lock_path, flags)
        except PermissionError as error:
            raise _filesystem_error(
                "invalid_permissions",
                "Could not open private state lock.",
                lock_path,
                error,
            ) from error
        except OSError as error:
            raise _filesystem_error(
                "lock_failed",
                "Could not open state lock.",
                lock_path,
                error,
            ) from error
    except OSError as error:
        raise _filesystem_error(
            "lock_failed",
            "Could not create state lock.",
            lock_path,
            error,
        ) from error
    try:
        try:
            if created:
                os.fchmod(descriptor, 0o600)
            opened_info = os.fstat(descriptor)
            if not stat.S_ISREG(opened_info.st_mode):
                raise StoreError(
                    "invalid_state_path",
                    "Opened state lock is not a regular file.",
                    details={"path": str(lock_path)},
                )
            _validate_owner_and_mode(opened_info, lock_path, expected_mode=0o600)
        except OSError as error:
            raise _filesystem_error(
                "lock_failed",
                "Could not secure or inspect state lock.",
                lock_path,
                error,
            ) from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as error:
            raise _filesystem_error(
                "lock_failed",
                "Could not acquire state lock.",
                lock_path,
                error,
            ) from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as error:
            raise _filesystem_error(
                "lock_failed",
                "Could not release state lock.",
                lock_path,
                error,
            ) from error
        os.close(descriptor)


def _empty_index(timestamp: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "active_track_id": None,
        "tracks": {},
        "archived": {},
        "updated_at": timestamp,
    }


def _validate_metadata(value: Any, path: str) -> dict[str, Any]:
    metadata = _require_object(value, path)
    _require_fields(metadata, METADATA_FIELDS, path)
    _reject_unknown_fields(metadata, METADATA_FIELDS, path)
    _require_nonempty_string(metadata["topic"], f"{path}.topic")
    _require_nonempty_string(metadata["goal"], f"{path}.goal")
    if not isinstance(metadata["status"], str) or metadata["status"] not in {
        "active",
        "archived",
    }:
        _raise_wrong_type(f"{path}.status", "active or archived")
    _require_revision(metadata["revision"], f"{path}.revision")
    _require_nonempty_string(metadata["updated_at"], f"{path}.updated_at")
    return metadata


def _validate_index(value: dict[str, Any]) -> dict[str, Any]:
    _require_fields(value, {"schema_version"}, "index")
    if isinstance(value["schema_version"], bool) or not isinstance(
        value["schema_version"], int
    ):
        _raise_wrong_type("index.schema_version", "an integer")
    if value["schema_version"] != SCHEMA_VERSION:
        raise StoreError(
            "unknown_schema",
            "Unsupported index schema version.",
            details={
                "path": "index.schema_version",
                "actual": value["schema_version"],
                "supported": SCHEMA_VERSION,
            },
        )
    _require_fields(value, INDEX_FIELDS, "index")
    _reject_unknown_fields(value, INDEX_FIELDS, "index")
    tracks = _require_object(value["tracks"], "index.tracks")
    archived = _require_object(value["archived"], "index.archived")
    overlap = sorted(set(tracks) & set(archived))
    if overlap:
        raise StoreError(
            "inconsistent_state",
            "Track IDs occur in both active and archived indexes.",
            details={"track_ids": overlap},
        )
    for track_id, metadata in tracks.items():
        _validate_track_id(track_id)
        valid = _validate_metadata(metadata, f"index.tracks.{track_id}")
        if valid["status"] != "active":
            raise StoreError(
                "inconsistent_state",
                "Active index contains non-active metadata.",
                details={"track_id": track_id},
            )
    for track_id, metadata in archived.items():
        _validate_track_id(track_id)
        valid = _validate_metadata(metadata, f"index.archived.{track_id}")
        if valid["status"] != "archived":
            raise StoreError(
                "inconsistent_state",
                "Archive index contains non-archived metadata.",
                details={"track_id": track_id},
            )
    active = value["active_track_id"]
    if active is not None:
        _validate_track_id(active)
        if active not in tracks:
            raise StoreError(
                "inconsistent_state",
                "active_track_id does not identify an active track.",
                details={"active_track_id": active},
            )
    elif tracks:
        raise StoreError(
            "inconsistent_state",
            "active_track_id is null while active tracks exist.",
        )
    _require_nonempty_string(value["updated_at"], "index.updated_at")
    return value


def _directory_entry_names(path: Path) -> list[str]:
    _assert_directory(path)
    try:
        return sorted(_path_listdir(path))
    except OSError as error:
        raise _filesystem_error(
            "state_read_failed",
            "Could not inspect state directory entries.",
            path,
            error,
        ) from error


def _load_index(root: Path) -> dict[str, Any]:
    path = root / "index.json"
    if not _path_exists(path):
        existing = {
            "tracks": _directory_entry_names(root / "tracks"),
            "archive": _directory_entry_names(root / "archive"),
        }
        if existing["tracks"] or existing["archive"]:
            raise StoreError(
                "inconsistent_state",
                "index.json is missing while track files still exist.",
                details={"path": str(path), **existing},
            )
        index = _empty_index(_now())
        _commit_transaction(
            root,
            "initialize",
            {"index.json": index},
            [],
        )
        return index
    return _validate_index(_read_json(path))


def _normalize_history_entry(value: Any, timestamp: str) -> dict[str, Any]:
    entry = _require_object(value, "history entry")
    _require_fields(entry, HISTORY_INPUT_FIELDS, "history entry")
    _reject_unknown_fields(entry, HISTORY_INPUT_FIELDS, "history entry")
    for field in sorted(HISTORY_INPUT_FIELDS):
        _require_nonempty_string(entry[field], f"history entry.{field}")
    return {
        "task": entry["task"],
        "learner_response": entry["learner_response"],
        "feedback": entry["feedback"],
        "attainment": entry["attainment"],
        "recorded_at": timestamp,
    }


def _validate_history(value: Any, path: str) -> list[Any]:
    entries = _require_list(value, path)
    for position, raw_entry in enumerate(entries):
        entry_path = f"{path}[{position}]"
        entry = _require_object(raw_entry, entry_path)
        _require_fields(entry, HISTORY_FIELDS, entry_path)
        _reject_unknown_fields(entry, HISTORY_FIELDS, entry_path)
        for field in sorted(HISTORY_INPUT_FIELDS):
            _require_nonempty_string(entry[field], f"{entry_path}.{field}")
        _require_nonempty_string(entry["recorded_at"], f"{entry_path}.recorded_at")
    return entries


def _validate_track(value: dict[str, Any], expected_id: str) -> dict[str, Any]:
    _require_fields(value, {"schema_version"}, "track")
    if isinstance(value["schema_version"], bool) or not isinstance(
        value["schema_version"], int
    ):
        _raise_wrong_type("track.schema_version", "an integer")
    if value["schema_version"] != SCHEMA_VERSION:
        raise StoreError(
            "unknown_schema",
            "Unsupported track schema version.",
            details={
                "track_id": expected_id,
                "actual": value["schema_version"],
                "supported": SCHEMA_VERSION,
            },
        )
    _require_fields(value, TRACK_FIELDS, "track")
    _reject_unknown_fields(value, TRACK_FIELDS, "track")
    if value["id"] != expected_id:
        raise StoreError(
            "inconsistent_state",
            "Track file ID does not match its filename.",
            details={"expected": expected_id, "actual": value["id"]},
        )
    _validate_track_id(value["id"])
    _require_revision(value["revision"], "track.revision")
    if not isinstance(value["status"], str) or value["status"] not in {
        "active",
        "archived",
    }:
        _raise_wrong_type("track.status", "active or archived")
    _require_nonempty_string(value["topic"], "track.topic")
    _require_nonempty_string(value["goal"], "track.goal")
    _require_object(value["learner_profile"], "track.learner_profile")
    _require_list(value["sources"], "track.sources")
    _require_nonempty_string(value["destination"], "track.destination")
    _require_list(value["nearby_steps"], "track.nearby_steps")
    _require_nullable_string(value["current_step_id"], "track.current_step_id")
    _require_list(value["recommended_route"], "track.recommended_route")
    _require_object(value["mastery"], "track.mastery")
    if value["current_task"] is not None:
        _require_object(value["current_task"], "track.current_task")
    _validate_history(value["history"], "track.history")
    _require_nullable_string(value["next_action"], "track.next_action")
    _require_nonempty_string(value["created_at"], "track.created_at")
    _require_nonempty_string(value["updated_at"], "track.updated_at")
    return value


def _metadata(track: dict[str, Any]) -> dict[str, Any]:
    return {
        "topic": track["topic"],
        "goal": track["goal"],
        "status": track["status"],
        "revision": track["revision"],
        "updated_at": track["updated_at"],
    }


def _track_path(root: Path, track_id: str, status: str) -> Path:
    _validate_track_id(track_id)
    if status == "active":
        return root / "tracks" / f"{track_id}.json"
    if status == "archived":
        return root / "archive" / f"{track_id}.json"
    raise ValueError(f"Unexpected track status: {status}")


def _load_track(root: Path, track_id: str, status: str) -> dict[str, Any]:
    value = _read_json(_track_path(root, track_id, status))
    track = _validate_track(value, track_id)
    if track["status"] != status:
        raise StoreError(
            "inconsistent_state",
            "Track status does not match its directory.",
            details={
                "track_id": track_id,
                "directory_status": status,
                "track_status": track["status"],
            },
        )
    return track


def _load_indexed_track(
    root: Path,
    index: dict[str, Any],
    track_id: str,
    status: str,
) -> dict[str, Any]:
    collection = "tracks" if status == "active" else "archived"
    if track_id not in index[collection]:
        raise StoreError(
            "inconsistent_state",
            "Track is absent from the expected index collection.",
            details={"track_id": track_id, "status": status},
        )
    track = _load_track(root, track_id, status)
    actual_metadata = _metadata(track)
    if index[collection][track_id] != actual_metadata:
        raise StoreError(
            "inconsistent_state",
            "Track metadata differs from its index entry.",
            details={
                "track_id": track_id,
                "index_metadata": index[collection][track_id],
                "track_metadata": actual_metadata,
            },
        )
    return track


def _transaction_relative_path(
    raw_path: Any,
    *,
    allow_index: bool,
) -> tuple[str, str | None, str | None]:
    if not isinstance(raw_path, str) or not raw_path:
        _raise_wrong_type("transaction path", "a non-empty relative path")
    pure = PurePosixPath(raw_path)
    if (
        pure.is_absolute()
        or pure.as_posix() != raw_path
        or ".." in pure.parts
        or "." in pure.parts
        or "\\" in raw_path
    ):
        raise StoreError(
            "invalid_transaction",
            "Transaction contains an unsafe path.",
            details={"path": raw_path},
        )
    if raw_path == "index.json" and allow_index:
        return raw_path, None, None
    if len(pure.parts) != 2 or pure.parts[0] not in {"tracks", "archive"}:
        raise StoreError(
            "invalid_transaction",
            "Transaction path is outside the allowed state files.",
            details={"path": raw_path},
        )
    filename = pure.parts[1]
    if not filename.endswith(".json"):
        raise StoreError(
            "invalid_transaction",
            "Transaction track path must end in .json.",
            details={"path": raw_path},
        )
    track_id = filename[:-5]
    _validate_track_id(track_id)
    status = "active" if pure.parts[0] == "tracks" else "archived"
    return raw_path, track_id, status


def _index_file_entries(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for track_id, metadata in index["tracks"].items():
        entries[f"tracks/{track_id}.json"] = metadata
    for track_id, metadata in index["archived"].items():
        entries[f"archive/{track_id}.json"] = metadata
    return entries


def _validate_transaction_transition(
    value: dict[str, Any],
    operation: str,
    final_index: dict[str, Any],
    writes: dict[str, Any],
    write_tracks: list[tuple[str, str, str]],
    delete_tracks: list[tuple[str, str, str]],
) -> None:
    affected_paths = {item[0] for item in write_tracks} | {
        item[0] for item in delete_tracks
    }
    before = _require_object(value["before"], "transaction.before")
    _require_fields(before, TRANSACTION_BEFORE_FIELDS, "transaction.before")
    _reject_unknown_fields(before, TRANSACTION_BEFORE_FIELDS, "transaction.before")
    raw_before_index = before["index"]
    if raw_before_index is None:
        before_index = None
    else:
        before_index = _validate_index(
            _require_object(raw_before_index, "transaction.before.index")
        )
    before_files = _require_object(before["files"], "transaction.before.files")
    if set(before_files) != affected_paths:
        raise StoreError(
            "invalid_transaction",
            "Transaction before-state must cover exactly the affected track paths.",
            details={
                "expected_paths": sorted(affected_paths),
                "actual_paths": sorted(before_files),
            },
        )

    validated_before_files: dict[str, dict[str, Any] | None] = {}
    for raw_path, raw_content in before_files.items():
        relative, track_id, status = _transaction_relative_path(
            raw_path,
            allow_index=False,
        )
        if track_id is None or status is None:
            raise StoreError(
                "invalid_transaction",
                "Before-state track path is missing its identity.",
            )
        if raw_content is None:
            validated_before_files[relative] = None
            continue
        track = _validate_track(
            _require_object(
                raw_content,
                f"transaction.before.files.{relative}",
            ),
            track_id,
        )
        if track["status"] != status:
            raise StoreError(
                "invalid_transaction",
                "Before-state track is stored under the wrong directory.",
                details={"path": relative, "track_status": track["status"]},
            )
        validated_before_files[relative] = track

    before_entries = {} if before_index is None else _index_file_entries(before_index)
    final_entries = _index_file_entries(final_index)
    for relative in affected_paths:
        snapshot = validated_before_files[relative]
        if relative not in before_entries:
            if snapshot is not None:
                raise StoreError(
                    "invalid_transaction",
                    "Before-state file exists but is absent from the before index.",
                    details={"path": relative},
                )
        elif snapshot is None or before_entries[relative] != _metadata(snapshot):
            raise StoreError(
                "invalid_transaction",
                "Before-state track does not match the before index.",
                details={"path": relative},
            )

    unchanged_before = {
        path: metadata
        for path, metadata in before_entries.items()
        if path not in affected_paths
    }
    unchanged_final = {
        path: metadata
        for path, metadata in final_entries.items()
        if path not in affected_paths
    }
    if unchanged_before != unchanged_final:
        raise StoreError(
            "invalid_transaction",
            "Transaction changes tracks outside its declared file set.",
            details={
                "before_paths": sorted(unchanged_before),
                "final_paths": sorted(unchanged_final),
            },
        )

    if operation == "initialize":
        if before_index is not None or validated_before_files:
            raise StoreError(
                "invalid_transaction",
                "Initialize transaction requires an empty before-state.",
            )
    else:
        if before_index is None:
            raise StoreError(
                "invalid_transaction",
                "Mutation transaction requires a complete before index.",
            )

    if operation == "create":
        path = write_tracks[0][0]
        if validated_before_files[path] is not None or path in before_entries:
            raise StoreError(
                "invalid_transaction",
                "Create transaction target already exists in the before-state.",
                details={"path": path},
            )
        created_track = writes[path]
        if (
            created_track["revision"] != 0
            or created_track["status"] != "active"
            or created_track["created_at"] != created_track["updated_at"]
            or final_index["updated_at"] != created_track["updated_at"]
        ):
            raise StoreError(
                "invalid_transaction",
                "Created track violates initial revision or timestamp invariants.",
                details={"path": path},
            )
        if final_index["active_track_id"] != write_tracks[0][1]:
            raise StoreError(
                "invalid_transaction",
                "Create transaction must select the created track as active.",
            )
    elif operation == "update":
        path = write_tracks[0][0]
        if validated_before_files[path] is None or path not in before_entries:
            raise StoreError(
                "invalid_transaction",
                "Update transaction target is absent from the before-state.",
                details={"path": path},
            )
        before_track = validated_before_files[path]
        updated_track = writes[path]
        immutable_fields = (
            TRACK_FIELDS
            - UPDATE_FIELDS
            - {
                "history",
                "revision",
                "updated_at",
            }
        )
        if (
            updated_track["revision"] != before_track["revision"] + 1
            or updated_track["status"] != "active"
            or before_track["status"] != "active"
            or any(
                updated_track[field] != before_track[field]
                for field in immutable_fields
            )
        ):
            raise StoreError(
                "invalid_transaction",
                "Update transaction violates revision or immutable-field invariants.",
                details={"path": path},
            )
        before_history = before_track["history"]
        updated_history = updated_track["history"]
        if updated_history[: len(before_history)] != before_history:
            raise StoreError(
                "invalid_transaction",
                "Update transaction must preserve existing history as a prefix.",
                details={"path": path},
            )
        appended_history = updated_history[len(before_history) :]
        if any(
            entry["recorded_at"] != updated_track["updated_at"]
            for entry in appended_history
        ):
            raise StoreError(
                "invalid_transaction",
                "Appended history timestamps must match the track update timestamp.",
                details={"path": path},
            )
        if final_index["updated_at"] != updated_track["updated_at"]:
            raise StoreError(
                "invalid_transaction",
                "Update transaction index and track timestamps must match.",
                details={"path": path},
            )
        if final_index["active_track_id"] != write_tracks[0][1]:
            raise StoreError(
                "invalid_transaction",
                "Update transaction must select the updated track as active.",
            )
    elif operation in {"archive", "restore"}:
        write_path = write_tracks[0][0]
        delete_path = delete_tracks[0][0]
        if (
            validated_before_files[write_path] is not None
            or write_path in before_entries
            or validated_before_files[delete_path] is None
            or delete_path not in before_entries
        ):
            raise StoreError(
                "invalid_transaction",
                f"{operation.capitalize()} before-state is not a valid move.",
            )
        before_track = validated_before_files[delete_path]
        moved_track = writes[write_path]
        expected_before_status = "active" if operation == "archive" else "archived"
        expected_final_status = "archived" if operation == "archive" else "active"
        immutable_fields = TRACK_FIELDS - {"status", "revision", "updated_at"}
        if (
            before_track["status"] != expected_before_status
            or moved_track["status"] != expected_final_status
            or moved_track["revision"] != before_track["revision"] + 1
            or final_index["updated_at"] != moved_track["updated_at"]
            or any(
                moved_track[field] != before_track[field] for field in immutable_fields
            )
        ):
            raise StoreError(
                "invalid_transaction",
                f"{operation.capitalize()} transaction violates move invariants.",
            )
        expected_active = (
            _choose_active(final_index["tracks"])
            if operation == "archive"
            else write_tracks[0][1]
        )
        if final_index["active_track_id"] != expected_active:
            raise StoreError(
                "invalid_transaction",
                f"{operation.capitalize()} transaction selected an invalid active track.",
            )
    elif operation == "delete":
        path = delete_tracks[0][0]
        if validated_before_files[path] is None or path not in before_entries:
            raise StoreError(
                "invalid_transaction",
                "Delete transaction target is absent from the before-state.",
                details={"path": path},
            )
        expected_active = before_index["active_track_id"]
        if expected_active == delete_tracks[0][1]:
            expected_active = _choose_active(final_index["tracks"])
        if final_index["active_track_id"] != expected_active:
            raise StoreError(
                "invalid_transaction",
                "Delete transaction selected an invalid active track.",
            )

    for relative in affected_paths:
        if relative in writes:
            if relative not in final_entries:
                raise StoreError(
                    "invalid_transaction",
                    "Written track is absent from the final index.",
                    details={"path": relative},
                )
        elif relative in final_entries:
            raise StoreError(
                "invalid_transaction",
                "Deleted track remains in the final index.",
                details={"path": relative},
            )


def _validate_transaction(value: dict[str, Any]) -> dict[str, Any]:
    _require_fields(value, {"schema_version"}, "transaction")
    schema_version = value["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _raise_wrong_type("transaction.schema_version", "an integer")
    if schema_version != SCHEMA_VERSION:
        raise StoreError(
            "unknown_schema",
            "Unsupported transaction schema version.",
            details={
                "path": "transaction.schema_version",
                "actual": schema_version,
                "supported": SCHEMA_VERSION,
            },
        )
    _require_fields(value, TRANSACTION_FIELDS, "transaction")
    _reject_unknown_fields(value, TRANSACTION_FIELDS, "transaction")
    operation = _require_nonempty_string(value["operation"], "transaction.operation")
    if operation not in TRANSACTION_OPERATIONS:
        raise StoreError(
            "invalid_transaction",
            "Transaction operation is not supported.",
            details={"operation": operation},
        )
    writes = _require_object(value["writes"], "transaction.writes")
    deletes = _require_list(value["deletes"], "transaction.deletes")
    _require_nonempty_string(value["created_at"], "transaction.created_at")
    if "index.json" not in writes:
        raise StoreError(
            "invalid_transaction",
            "Transaction must contain the complete new index.",
        )

    index_value = _require_object(writes["index.json"], "transaction.writes.index.json")
    final_index = _validate_index(index_value)
    write_tracks: list[tuple[str, str, str]] = []
    for raw_path, raw_content in writes.items():
        relative, track_id, status = _transaction_relative_path(
            raw_path,
            allow_index=True,
        )
        content = _require_object(
            raw_content,
            f"transaction.writes.{relative}",
        )
        if relative == "index.json":
            continue
        if track_id is None or status is None:
            raise StoreError(
                "invalid_transaction",
                "Track write is missing its identity.",
            )
        track = _validate_track(content, track_id)
        if track["status"] != status:
            raise StoreError(
                "invalid_transaction",
                "Transaction writes a track into the wrong directory.",
                details={"path": relative, "track_status": track["status"]},
            )
        collection = "tracks" if status == "active" else "archived"
        if track_id not in final_index[collection] or final_index[collection][
            track_id
        ] != _metadata(track):
            raise StoreError(
                "invalid_transaction",
                "Transaction track does not match its complete new index.",
                details={"path": relative, "track_id": track_id},
            )
        write_tracks.append((relative, track_id, status))

    delete_tracks: list[tuple[str, str, str]] = []
    seen_deletes: set[str] = set()
    for raw_path in deletes:
        relative, track_id, status = _transaction_relative_path(
            raw_path,
            allow_index=False,
        )
        if relative in seen_deletes:
            raise StoreError(
                "invalid_transaction",
                "Transaction contains a duplicate delete target.",
                details={"path": relative},
            )
        seen_deletes.add(relative)
        if track_id is None or status is None:
            raise StoreError(
                "invalid_transaction",
                "Delete target is missing its track identity.",
            )
        collection = "tracks" if status == "active" else "archived"
        if track_id in final_index[collection]:
            raise StoreError(
                "invalid_transaction",
                "Deleted track remains in the complete new index.",
                details={"path": relative, "track_id": track_id},
            )
        delete_tracks.append((relative, track_id, status))

    if set(writes) & seen_deletes:
        raise StoreError(
            "invalid_transaction",
            "Transaction cannot write and delete the same path.",
        )

    write_paths = {item[0] for item in write_tracks}
    delete_paths = {item[0] for item in delete_tracks}
    if operation == "initialize":
        if set(writes) != {"index.json"} or deletes:
            raise StoreError(
                "invalid_transaction",
                "Initialize transaction must only write an empty index.",
            )
        if final_index["tracks"] or final_index["archived"]:
            raise StoreError(
                "invalid_transaction",
                "Initialize transaction index must be empty.",
            )
    elif operation in {"create", "update"}:
        if len(write_tracks) != 1 or write_tracks[0][2] != "active" or deletes:
            raise StoreError(
                "invalid_transaction",
                f"{operation} transaction has an invalid file set.",
                details={
                    "writes": sorted(write_paths),
                    "deletes": sorted(delete_paths),
                },
            )
    elif operation == "archive":
        if (
            len(write_tracks) != 1
            or write_tracks[0][2] != "archived"
            or len(delete_tracks) != 1
            or delete_tracks[0][2] != "active"
            or write_tracks[0][1] != delete_tracks[0][1]
        ):
            raise StoreError(
                "invalid_transaction",
                "Archive transaction has an invalid file set.",
            )
    elif operation == "restore":
        if (
            len(write_tracks) != 1
            or write_tracks[0][2] != "active"
            or len(delete_tracks) != 1
            or delete_tracks[0][2] != "archived"
            or write_tracks[0][1] != delete_tracks[0][1]
        ):
            raise StoreError(
                "invalid_transaction",
                "Restore transaction has an invalid file set.",
            )
    elif operation == "delete":
        if write_tracks or len(delete_tracks) != 1:
            raise StoreError(
                "invalid_transaction",
                "Delete transaction has an invalid file set.",
            )
        deleted_id = delete_tracks[0][1]
        if deleted_id in final_index["tracks"] or deleted_id in final_index["archived"]:
            raise StoreError(
                "invalid_transaction",
                "Deleted track remains in the complete new index.",
                details={"track_id": deleted_id},
            )
    _validate_transaction_transition(
        value,
        operation,
        final_index,
        writes,
        write_tracks,
        delete_tracks,
    )
    return value


def _transaction_target(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    return root.joinpath(*pure.parts)


def _unlink_and_fsync(path: Path, *, missing_ok: bool) -> None:
    try:
        _path_lstat(path)
    except FileNotFoundError:
        if not missing_ok:
            raise StoreError(
                "state_file_missing",
                "Required state file does not exist.",
                details={"path": str(path)},
            ) from None
    except OSError as error:
        raise _filesystem_error(
            "state_delete_failed",
            "Could not inspect state file before deletion.",
            path,
            error,
        ) from error
    else:
        _assert_regular_file(path)
        try:
            _path_unlink(path)
        except OSError as error:
            raise _filesystem_error(
                "state_delete_failed",
                "Could not delete state file.",
                path,
                error,
            ) from error
    try:
        _fsync_directory(path.parent)
    except OSError as error:
        raise _filesystem_error(
            "state_delete_failed",
            "Could not persist state file deletion.",
            path,
            error,
        ) from error


def _is_transaction_temporary(name: str, target_name: str) -> bool:
    prefix = f".{target_name}."
    suffix = ".tmp"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return False
    token = name[len(prefix) : -len(suffix)]
    return len(token) == 16 and all(
        character in "0123456789abcdef" for character in token
    )


def _cleanup_transaction_temporaries(
    root: Path,
    transaction: dict[str, Any],
) -> None:
    for relative in sorted(transaction["writes"]):
        target = _transaction_target(root, relative)
        for name in _directory_entry_names(target.parent):
            if _is_transaction_temporary(name, target.name):
                _unlink_and_fsync(
                    target.parent / name,
                    missing_ok=True,
                )


def _read_existing_track_files(root: Path) -> dict[str, dict[str, Any]]:
    tracks: dict[str, dict[str, Any]] = {}
    for track_id in _scan_track_ids(root / "tracks"):
        tracks[f"tracks/{track_id}.json"] = _load_track(
            root,
            track_id,
            "active",
        )
    for track_id in _scan_track_ids(root / "archive"):
        tracks[f"archive/{track_id}.json"] = _load_track(
            root,
            track_id,
            "archived",
        )
    return tracks


def _capture_before_state(
    root: Path,
    affected_paths: set[str],
) -> dict[str, Any]:
    index_path = root / "index.json"
    if _path_exists(index_path):
        index: dict[str, Any] | None = _validate_index(_read_json(index_path))
    else:
        index = None
    tracks = _read_existing_track_files(root)
    if index is None:
        if tracks:
            raise StoreError(
                "inconsistent_state",
                "Track files exist without index.json.",
                details={"paths": sorted(tracks)},
            )
    else:
        index_entries = _index_file_entries(index)
        if set(index_entries) != set(tracks):
            raise StoreError(
                "inconsistent_state",
                "Track files and index entries differ before mutation.",
                details={
                    "files_only": sorted(set(tracks) - set(index_entries)),
                    "index_only": sorted(set(index_entries) - set(tracks)),
                },
            )
        for relative, track in tracks.items():
            if index_entries[relative] != _metadata(track):
                raise StoreError(
                    "inconsistent_state",
                    "Track metadata differs from index before mutation.",
                    details={"path": relative},
                )
    return {
        "index": copy.deepcopy(index),
        "files": {
            relative: copy.deepcopy(tracks[relative]) if relative in tracks else None
            for relative in sorted(affected_paths)
        },
    }


def _validate_existing_state_before_transaction(
    root: Path,
    transaction: dict[str, Any],
) -> None:
    """Accept only an exact before/final crash-partial state."""

    before = transaction["before"]
    before_index = before["index"]
    final_index = transaction["writes"]["index.json"]
    affected_paths = set(before["files"])
    index_path = root / "index.json"
    if _path_exists(index_path):
        current_index: dict[str, Any] | None = _validate_index(_read_json(index_path))
    else:
        current_index = None
    if current_index != before_index and current_index != final_index:
        raise StoreError(
            "inconsistent_state",
            "Current index is neither the transaction before-state nor final state.",
            details={"path": str(index_path)},
        )

    current_tracks = _read_existing_track_files(root)
    before_entries = {} if before_index is None else _index_file_entries(before_index)
    final_entries = _index_file_entries(final_index)
    known_paths = set(before_entries) | set(final_entries)
    unexpected_paths = set(current_tracks) - known_paths
    if unexpected_paths:
        raise StoreError(
            "inconsistent_state",
            "Current state contains tracks outside the transaction snapshots.",
            details={"paths": sorted(unexpected_paths)},
        )

    for relative in known_paths - affected_paths:
        if relative not in current_tracks:
            raise StoreError(
                "inconsistent_state",
                "Unchanged track is missing while a transaction is pending.",
                details={"path": relative},
            )
        if before_entries[relative] != _metadata(current_tracks[relative]):
            raise StoreError(
                "inconsistent_state",
                "Unchanged track differs from the transaction before-state.",
                details={"path": relative},
            )

    for relative in affected_paths:
        before_content = before["files"][relative]
        if relative in transaction["writes"]:
            final_content = transaction["writes"][relative]
        else:
            final_content = None
        current_content = (
            current_tracks[relative] if relative in current_tracks else None
        )
        if current_content != before_content and current_content != final_content:
            raise StoreError(
                "inconsistent_state",
                "Affected track is neither its before-state nor final state.",
                details={"path": relative},
            )


def _apply_transaction(root: Path, transaction: dict[str, Any]) -> None:
    validated = _validate_transaction(transaction)
    _validate_existing_state_before_transaction(root, validated)
    writes = validated["writes"]
    non_index_paths = sorted(path for path in writes if path != "index.json")
    for relative in non_index_paths:
        _atomic_write_json(
            _transaction_target(root, relative),
            writes[relative],
        )
    _atomic_write_json(root / "index.json", writes["index.json"])
    for relative in validated["deletes"]:
        _unlink_and_fsync(
            _transaction_target(root, relative),
            missing_ok=True,
        )
    _unlink_and_fsync(root / ".transaction.json", missing_ok=False)


def _commit_transaction(
    root: Path,
    operation: str,
    writes: dict[str, dict[str, Any]],
    deletes: list[str],
) -> None:
    journal_path = root / ".transaction.json"
    if _path_exists(journal_path):
        raise StoreError(
            "inconsistent_state",
            "A pending transaction must be recovered before a new commit.",
            details={"path": str(journal_path)},
        )
    affected_paths = {path for path in writes if path != "index.json"} | set(deletes)
    before = _capture_before_state(root, affected_paths)
    transaction: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "before": before,
        "writes": copy.deepcopy(writes),
        "deletes": list(deletes),
        "created_at": _now(),
    }
    _validate_transaction(transaction)
    _atomic_write_json(journal_path, transaction)
    _apply_transaction(root, transaction)


def _recover_transaction(root: Path) -> None:
    journal_path = root / ".transaction.json"
    if not _path_exists(journal_path):
        return
    transaction = _validate_transaction(_read_json(journal_path))
    _cleanup_transaction_temporaries(root, transaction)
    _apply_transaction(root, transaction)


@contextmanager
def _locked_store(root: Path) -> Iterator[None]:
    with _store_lock(root):
        _recover_transaction(root)
        yield


@contextmanager
def _prepared_locked_store(
    state_dir: str | os.PathLike[str] | None,
) -> Iterator[Path]:
    with _prepare_state_dir(state_dir) as root:
        with _locked_store(root):
            yield root


def _locate(index: dict[str, Any], track_id: str) -> str:
    _validate_track_id(track_id)
    if track_id in index["tracks"]:
        return "active"
    if track_id in index["archived"]:
        return "archived"
    raise StoreError(
        "track_not_found",
        "Track is not present in the index.",
        details={"track_id": track_id},
    )


def _choose_active(tracks: dict[str, Any]) -> str | None:
    if not tracks:
        return None
    return max(
        tracks,
        key=lambda track_id: (tracks[track_id]["updated_at"], track_id),
    )


def _read_input(source: str) -> dict[str, Any]:
    try:
        if source == "-":
            value = json.load(sys.stdin)
        else:
            with open(source, "r", encoding="utf-8") as handle:
                value = json.load(handle)
    except json.JSONDecodeError as error:
        raise StoreError(
            "invalid_input_json",
            "Input contains invalid JSON.",
            details={"source": source, "line": error.lineno, "column": error.colno},
        ) from None
    except UnicodeDecodeError as error:
        raise StoreError(
            "invalid_input_encoding",
            "Input JSON is not valid UTF-8.",
            details={"source": source, "start": error.start, "end": error.end},
        ) from None
    except OSError as error:
        raise StoreError(
            "input_read_failed",
            "Could not read input JSON.",
            details={"source": source, "reason": str(error)},
        ) from None
    _validate_json_unicode(value, "input")
    return _require_object(value, "input")


def _build_track(
    payload: dict[str, Any], track_id: str, timestamp: str
) -> dict[str, Any]:
    _validate_json_unicode(payload, "create input")
    _reject_unknown_fields(payload, CREATE_FIELDS, "create input")
    _require_fields(payload, {"topic", "goal"}, "create input")
    topic = _require_nonempty_string(payload["topic"], "create input.topic")
    goal = _require_nonempty_string(payload["goal"], "create input.goal")
    raw_history = _require_list(
        _optional(payload, "history", []), "create input.history"
    )
    history = [_normalize_history_entry(entry, timestamp) for entry in raw_history]
    track = {
        "schema_version": SCHEMA_VERSION,
        "id": track_id,
        "revision": 0,
        "status": "active",
        "topic": topic,
        "goal": goal,
        "learner_profile": _optional(payload, "learner_profile", {}),
        "sources": _optional(payload, "sources", []),
        "destination": _optional(payload, "destination", goal),
        "nearby_steps": _optional(payload, "nearby_steps", []),
        "current_step_id": _optional(payload, "current_step_id", None),
        "recommended_route": _optional(payload, "recommended_route", []),
        "mastery": _optional(payload, "mastery", {}),
        "current_task": _optional(payload, "current_task", None),
        "history": history,
        "next_action": _optional(payload, "next_action", None),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    return _validate_track(track, track_id)


def create_track(
    state_dir: str | os.PathLike[str] | None, payload: dict[str, Any]
) -> dict[str, Any]:
    # Validate all caller-controlled fields before touching persistent state.
    _build_track(payload, "track-00000000", _now())
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        timestamp = _now()
        for _ in range(32):
            track_id = _new_track_id(
                _require_nonempty_string(payload["topic"], "create input.topic")
                if "topic" in payload
                else "track"
            )
            if (
                track_id not in index["tracks"]
                and track_id not in index["archived"]
                and not _path_exists(_track_path(root, track_id, "active"))
                and not _path_exists(_track_path(root, track_id, "archived"))
            ):
                break
        else:
            raise StoreError(
                "id_generation_failed",
                "Could not generate a unique track ID.",
            )
        track = _build_track(payload, track_id, timestamp)
        new_index = copy.deepcopy(index)
        new_index["tracks"][track_id] = _metadata(track)
        new_index["active_track_id"] = track_id
        new_index["updated_at"] = timestamp
        _commit_transaction(
            root,
            "create",
            {
                f"tracks/{track_id}.json": track,
                "index.json": new_index,
            },
            [],
        )
        return track


def list_tracks(
    state_dir: str | os.PathLike[str] | None,
    *,
    include_archived: bool = False,
) -> dict[str, Any]:
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "active_track_id": index["active_track_id"],
            "tracks": [
                {"id": track_id, **copy.deepcopy(index["tracks"][track_id])}
                for track_id in sorted(index["tracks"])
            ],
        }
        if include_archived:
            result["archived"] = [
                {"id": track_id, **copy.deepcopy(index["archived"][track_id])}
                for track_id in sorted(index["archived"])
            ]
        return result


def load_track(
    state_dir: str | os.PathLike[str] | None, track_id: str
) -> dict[str, Any]:
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        status = _locate(index, track_id)
        return _load_indexed_track(root, index, track_id, status)


def update_track(
    state_dir: str | os.PathLike[str] | None,
    track_id: str,
    expected_revision: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _validate_json_unicode(payload, "update input")
    _require_revision(expected_revision, "expected_revision")
    _reject_unknown_fields(payload, {"set", "append_history"}, "update input")
    if "set" not in payload and "append_history" not in payload:
        raise StoreError(
            "invalid_schema",
            "Update input must contain set or append_history.",
        )
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        status = _locate(index, track_id)
        if status != "active":
            raise StoreError(
                "track_archived",
                "Archived tracks must be restored before they can be updated.",
                details={"track_id": track_id},
            )
        current = _load_indexed_track(root, index, track_id, "active")
        if current["revision"] != expected_revision:
            raise StoreError(
                "revision_conflict",
                "Track revision does not match expected_revision.",
                details={
                    "track_id": track_id,
                    "expected_revision": expected_revision,
                    "actual_revision": current["revision"],
                },
            )
        updated = copy.deepcopy(current)
        if "set" in payload:
            changes = _require_object(payload["set"], "update input.set")
            _reject_unknown_fields(changes, UPDATE_FIELDS, "update input.set")
            if not changes:
                raise StoreError(
                    "invalid_schema",
                    "update input.set must not be empty.",
                )
            for field, value in changes.items():
                updated[field] = copy.deepcopy(value)
        timestamp = _now()
        if "append_history" in payload:
            entries = _require_list(
                payload["append_history"], "update input.append_history"
            )
            if not entries:
                raise StoreError(
                    "invalid_schema",
                    "update input.append_history must not be empty.",
                )
            updated["history"].extend(
                _normalize_history_entry(entry, timestamp) for entry in entries
            )
        updated["revision"] += 1
        updated["updated_at"] = timestamp
        _validate_track(updated, track_id)
        new_index = copy.deepcopy(index)
        new_index["tracks"][track_id] = _metadata(updated)
        new_index["active_track_id"] = track_id
        new_index["updated_at"] = timestamp
        _commit_transaction(
            root,
            "update",
            {
                f"tracks/{track_id}.json": updated,
                "index.json": new_index,
            },
            [],
        )
        return updated


def archive_track(
    state_dir: str | os.PathLike[str] | None,
    track_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    _require_revision(expected_revision, "expected_revision")
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        status = _locate(index, track_id)
        if status != "active":
            raise StoreError(
                "already_archived",
                "Track is already archived.",
                details={"track_id": track_id},
            )
        current = _load_indexed_track(root, index, track_id, "active")
        if current["revision"] != expected_revision:
            raise StoreError(
                "revision_conflict",
                "Track revision does not match expected_revision.",
                details={
                    "track_id": track_id,
                    "expected_revision": expected_revision,
                    "actual_revision": current["revision"],
                },
            )
        timestamp = _now()
        archived = copy.deepcopy(current)
        archived["status"] = "archived"
        archived["revision"] += 1
        archived["updated_at"] = timestamp
        _validate_track(archived, track_id)
        archive_path = _track_path(root, track_id, "archived")
        if _path_exists(archive_path):
            raise StoreError(
                "inconsistent_state",
                "Archive target already exists.",
                details={"path": str(archive_path)},
            )
        new_index = copy.deepcopy(index)
        del new_index["tracks"][track_id]
        new_index["archived"][track_id] = _metadata(archived)
        new_index["active_track_id"] = _choose_active(new_index["tracks"])
        new_index["updated_at"] = timestamp
        _commit_transaction(
            root,
            "archive",
            {
                f"archive/{track_id}.json": archived,
                "index.json": new_index,
            },
            [f"tracks/{track_id}.json"],
        )
        return archived


def restore_track(
    state_dir: str | os.PathLike[str] | None, track_id: str
) -> dict[str, Any]:
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        status = _locate(index, track_id)
        if status != "archived":
            raise StoreError(
                "already_active",
                "Track is already active.",
                details={"track_id": track_id},
            )
        current = _load_indexed_track(root, index, track_id, "archived")
        timestamp = _now()
        restored = copy.deepcopy(current)
        restored["status"] = "active"
        restored["revision"] += 1
        restored["updated_at"] = timestamp
        _validate_track(restored, track_id)
        active_path = _track_path(root, track_id, "active")
        if _path_exists(active_path):
            raise StoreError(
                "inconsistent_state",
                "Restore target already exists.",
                details={"path": str(active_path)},
            )
        new_index = copy.deepcopy(index)
        del new_index["archived"][track_id]
        new_index["tracks"][track_id] = _metadata(restored)
        new_index["active_track_id"] = track_id
        new_index["updated_at"] = timestamp
        _commit_transaction(
            root,
            "restore",
            {
                f"tracks/{track_id}.json": restored,
                "index.json": new_index,
            },
            [f"archive/{track_id}.json"],
        )
        return restored


def delete_track(
    state_dir: str | os.PathLike[str] | None,
    track_id: str,
    confirmation: str,
) -> dict[str, Any]:
    _validate_track_id(track_id)
    if confirmation != track_id:
        raise StoreError(
            "confirmation_mismatch",
            "--confirm must exactly match the track ID.",
            details={"track_id": track_id},
        )
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        status = _locate(index, track_id)
        _load_indexed_track(root, index, track_id, status)
        timestamp = _now()
        new_index = copy.deepcopy(index)
        collection = "tracks" if status == "active" else "archived"
        del new_index[collection][track_id]
        if new_index["active_track_id"] == track_id:
            new_index["active_track_id"] = _choose_active(new_index["tracks"])
        new_index["updated_at"] = timestamp
        directory = "tracks" if status == "active" else "archive"
        _commit_transaction(
            root,
            "delete",
            {"index.json": new_index},
            [f"{directory}/{track_id}.json"],
        )
        return {"track_id": track_id, "deleted": True, "status": status}


def _scan_track_ids(directory: Path) -> set[str]:
    identifiers: set[str] = set()
    _assert_directory(directory)
    try:
        for name in _path_listdir(directory):
            path = directory / name
            info = _path_lstat(path)
            if stat.S_ISLNK(info.st_mode):
                raise StoreError(
                    "symlink_rejected",
                    "Symbolic links are not allowed in the state store.",
                    details={"path": str(path)},
                )
            if not stat.S_ISREG(info.st_mode) or not name.endswith(".json"):
                raise StoreError(
                    "unexpected_state_entry",
                    "State directory contains an unexpected entry.",
                    details={"path": str(path)},
                )
            track_id = name[:-5]
            _validate_track_id(track_id)
            identifiers.add(track_id)
    except StoreError:
        raise
    except OSError as error:
        raise _filesystem_error(
            "state_read_failed",
            "Could not scan state directory.",
            directory,
            error,
        ) from error
    return identifiers


def _check_mode(path: Path, expected: int) -> None:
    try:
        info = _path_lstat(path)
    except OSError as error:
        raise _filesystem_error(
            "state_access_failed",
            "Could not inspect state path permissions.",
            path,
            error,
        ) from error
    _validate_owner_and_mode(info, path, expected_mode=expected)


def validate_store(state_dir: str | os.PathLike[str] | None) -> dict[str, Any]:
    with _prepared_locked_store(state_dir) as root:
        index = _load_index(root)
        active_files = _scan_track_ids(root / "tracks")
        archived_files = _scan_track_ids(root / "archive")
        if active_files != set(index["tracks"]):
            raise StoreError(
                "inconsistent_state",
                "Active track files and index entries differ.",
                details={
                    "files_only": sorted(active_files - set(index["tracks"])),
                    "index_only": sorted(set(index["tracks"]) - active_files),
                },
            )
        if archived_files != set(index["archived"]):
            raise StoreError(
                "inconsistent_state",
                "Archived track files and index entries differ.",
                details={
                    "files_only": sorted(archived_files - set(index["archived"])),
                    "index_only": sorted(set(index["archived"]) - archived_files),
                },
            )
        for track_id in active_files:
            _load_indexed_track(root, index, track_id, "active")
        for track_id in archived_files:
            _load_indexed_track(root, index, track_id, "archived")
        for directory in (root, root / "tracks", root / "archive"):
            _check_mode(directory, 0o700)
        _check_mode(root / ".lock", 0o600)
        _check_mode(root / "index.json", 0o600)
        for track_id in active_files:
            _check_mode(_track_path(root, track_id, "active"), 0o600)
        for track_id in archived_files:
            _check_mode(_track_path(root, track_id, "archived"), 0o600)
        return {
            "valid": True,
            "active_tracks": len(active_files),
            "archived_tracks": len(archived_files),
        }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Store guided-learning progress with revision checks."
    )
    parser.add_argument(
        "--state-dir",
        help="Override the state directory (default: CODEX_HOME/state/guided-learning).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create")
    create.add_argument("--input", required=True, help="JSON file, or - for stdin.")

    listing = commands.add_parser("list")
    listing.add_argument("--include-archived", action="store_true")

    load = commands.add_parser("load")
    load.add_argument("track_id")

    update = commands.add_parser("update")
    update.add_argument("track_id")
    update.add_argument("--expected-revision", required=True, type=int)
    update.add_argument("--input", required=True, help="JSON file, or - for stdin.")

    archive = commands.add_parser("archive")
    archive.add_argument("track_id")
    archive.add_argument("--expected-revision", required=True, type=int)

    restore = commands.add_parser("restore")
    restore.add_argument("track_id")

    export = commands.add_parser("export")
    export.add_argument("track_id")

    delete = commands.add_parser("delete")
    delete.add_argument("track_id")
    delete.add_argument("--confirm", required=True)

    commands.add_parser("validate")
    return parser


def _write_output(value: dict[str, Any]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = {
                "ok": True,
                "track": create_track(args.state_dir, _read_input(args.input)),
            }
        elif args.command == "list":
            result = {
                "ok": True,
                **list_tracks(
                    args.state_dir,
                    include_archived=args.include_archived,
                ),
            }
        elif args.command == "load":
            result = {"ok": True, "track": load_track(args.state_dir, args.track_id)}
        elif args.command == "update":
            result = {
                "ok": True,
                "track": update_track(
                    args.state_dir,
                    args.track_id,
                    args.expected_revision,
                    _read_input(args.input),
                ),
            }
        elif args.command == "archive":
            result = {
                "ok": True,
                "track": archive_track(
                    args.state_dir,
                    args.track_id,
                    args.expected_revision,
                ),
            }
        elif args.command == "restore":
            result = {"ok": True, "track": restore_track(args.state_dir, args.track_id)}
        elif args.command == "export":
            result = {"ok": True, "track": load_track(args.state_dir, args.track_id)}
        elif args.command == "delete":
            result = {
                "ok": True,
                **delete_track(
                    args.state_dir,
                    args.track_id,
                    args.confirm,
                ),
            }
        elif args.command == "validate":
            result = {"ok": True, **validate_store(args.state_dir)}
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except StoreError as error:
        json.dump(error.as_dict(), sys.stderr, ensure_ascii=False, sort_keys=True)
        sys.stderr.write("\n")
        return 1
    _write_output(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
