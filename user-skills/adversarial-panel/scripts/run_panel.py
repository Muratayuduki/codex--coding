#!/usr/bin/env python3
"""Run an isolated, three-round adversarial Codex panel."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Sequence


ROLES = ("first-principles", "outside-view", "falsifier")
RUN_ROOT = Path("/tmp")
RUN_PREFIX = "adversarial-panel-"
MARKER_NAME = ".adversarial-panel-run"
MARKER_CONTENT = "adversarial-panel-v1\n"
SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_FILE = SKILL_DIR / "SKILL.md"
ROLE_METHODS = {
    "first-principles": (
        "Decompose the decision into objectives, hard constraints, causal mechanisms, and "
        "irreducible facts. Rebuild the conclusion from verified premises without relying on "
        "analogy or inherited convention. Mark every unsupported premise as an assumption."
    ),
    "outside-view": (
        "Identify the closest defensible reference classes and base rates. Compare this case "
        "with observed outcomes, account for selection effects, and calibrate the forecast "
        "before considering case-specific narratives."
    ),
    "falsifier": (
        "Try to disprove the proposed claims. Construct counterexamples and failure scenarios, "
        "locate observations that would reverse the decision, and prioritize tests that can "
        "kill a weak hypothesis quickly."
    ),
}
NONEMPTY_STRING = {"type": "string", "minLength": 1}
STRING_ARRAY = {"type": "array", "items": NONEMPTY_STRING}
CONFIDENCE_LEVEL = {"type": "string", "enum": ["low", "medium", "high"]}
ROUND_SCHEMAS = {
    1: {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["position", "claims", "risks", "verification_requests"],
        "properties": {
            "position": NONEMPTY_STRING,
            "claims": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "claim",
                        "basis",
                        "evidence",
                        "confidence",
                        "falsification_condition",
                    ],
                    "properties": {
                        "id": NONEMPTY_STRING,
                        "claim": NONEMPTY_STRING,
                        "basis": {
                            "type": "string",
                            "enum": ["fact", "inference", "assumption"],
                        },
                        "evidence": NONEMPTY_STRING,
                        "confidence": CONFIDENCE_LEVEL,
                        "falsification_condition": NONEMPTY_STRING,
                    },
                },
            },
            "risks": STRING_ARRAY,
            "verification_requests": STRING_ARRAY,
        },
    },
    2: {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["critiques"],
        "properties": {
            "critiques": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "target_panelist",
                        "target_claim_id",
                        "outcome",
                        "issue_type",
                        "analysis",
                        "attempted_falsification",
                        "result",
                        "verification",
                        "severity",
                    ],
                    "properties": {
                        "id": NONEMPTY_STRING,
                        "target_panelist": {"type": "string", "enum": list(ROLES)},
                        "target_claim_id": NONEMPTY_STRING,
                        "outcome": {
                            "type": "string",
                            "enum": ["attack-established", "no-valid-attack"],
                        },
                        "issue_type": NONEMPTY_STRING,
                        "analysis": NONEMPTY_STRING,
                        "attempted_falsification": NONEMPTY_STRING,
                        "result": NONEMPTY_STRING,
                        "verification": NONEMPTY_STRING,
                        "severity": CONFIDENCE_LEVEL,
                    },
                },
            },
        },
    },
    3: {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "final_position",
            "position_changed",
            "position_change_source",
            "position_change_reason",
            "position_change_evidence",
            "critique_responses",
            "residual_uncertainties",
            "confidence",
            "falsification_conditions",
        ],
        "properties": {
            "final_position": NONEMPTY_STRING,
            "position_changed": {"type": "boolean"},
            "position_change_source": {
                "type": "string",
                "enum": ["none", "critique", "new-evidence"],
            },
            "position_change_reason": NONEMPTY_STRING,
            "position_change_evidence": {"type": "string"},
            "critique_responses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "critic_panelist",
                        "critique_id",
                        "decision",
                        "reason",
                        "evidence",
                    ],
                    "properties": {
                        "critic_panelist": {"type": "string", "enum": list(ROLES)},
                        "critique_id": NONEMPTY_STRING,
                        "decision": {
                            "type": "string",
                            "enum": ["concede", "defend"],
                        },
                        "reason": NONEMPTY_STRING,
                        "evidence": NONEMPTY_STRING,
                    },
                },
            },
            "residual_uncertainties": STRING_ARRAY,
            "confidence": CONFIDENCE_LEVEL,
            "falsification_conditions": {
                "type": "array",
                "minItems": 1,
                "items": NONEMPTY_STRING,
            },
        },
    },
}


class ConfigurationError(ValueError):
    """Raised before a run starts when local inputs are invalid."""


class PanelRunError(RuntimeError):
    """Raised when fewer than two panelists survive a round."""

    def __init__(self, message: str, record_path: Path) -> None:
        super().__init__(message)
        self.record_path = record_path


@dataclass(frozen=True)
class Panelist:
    role: str
    model: str
    effort: str


@dataclass(frozen=True)
class SystemCodexIsolation:
    mode: str
    package_root: Path | None


CUSTOM_CA_ENV_NAMES = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)
SAFE_CODEX_ENV_NAMES = (
    "HOME",
    "CODEX_HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "LC_COLLATE",
    "LC_MONETARY",
    "LC_NUMERIC",
    "LC_TIME",
    "LC_PAPER",
    "LC_NAME",
    "LC_ADDRESS",
    "LC_TELEPHONE",
    "LC_MEASUREMENT",
    "LC_IDENTIFICATION",
    "TERM",
    "TMPDIR",
    "USER",
    "LOGNAME",
    "SHELL",
    "TZ",
    *CUSTOM_CA_ENV_NAMES,
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
)
PROXY_ENV_NAMES = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "all_proxy",
}
SAFE_TOOL_ENV_NAMES = tuple(
    name for name in SAFE_CODEX_ENV_NAMES if name not in PROXY_ENV_NAMES
)
SANDBOX_PRIVATE_DIR = Path("/panel")
SANDBOX_WORKSPACE = Path("/workspace")
SANDBOX_CODEX_HOME = SANDBOX_PRIVATE_DIR / "codex-home"
SANDBOX_AUTH_PATH = SANDBOX_CODEX_HOME / "auth.json"
SANDBOX_HOME = SANDBOX_PRIVATE_DIR / "home"
SANDBOX_TMPDIR = SANDBOX_PRIVATE_DIR / "tmp"
SANDBOX_CUSTOM_CA_DIR = Path("/etc/adversarial-panel-ca")
SYSTEM_CODEX_PATH = Path("/usr/bin/codex")
SYSTEM_CODEX_MASK_NAME = "system-codex-mask"
SYSTEM_CODEX_PACKAGE_MARKER = ("node_modules", "@openai", "codex")


@dataclass(frozen=True)
class ValidationContext:
    critique_targets: dict[str, set[str]] | None = None
    expected_critiques: set[tuple[str, str]] | None = None


class RoundAborted(RuntimeError):
    """Raised in a worker after another worker aborts the round."""


class ActiveProcessRegistry:
    """Track and terminate every isolated Codex process group in a run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen[str]] = set()
        self._aborted = threading.Event()

    def register(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._aborted.is_set():
                terminate_process_group(process)
                raise RoundAborted("round was aborted before process registration")
            self._processes.add(process)

    def unregister(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.discard(process)

    def ensure_running(self) -> None:
        if self._aborted.is_set():
            raise RoundAborted("round was aborted")

    def abort_all(self) -> None:
        self._aborted.set()
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            kill_process_group(process)
        for process in processes:
            process.wait()


def kill_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def terminate_process_group(
    process: subprocess.Popen[str],
) -> tuple[str, str]:
    kill_process_group(process)
    stdout, stderr = process.communicate()
    return stdout, stderr


def resolve_custom_ca_mounts() -> dict[str, tuple[Path, Path]]:
    mounts: dict[str, tuple[Path, Path]] = {}
    for name in CUSTOM_CA_ENV_NAMES:
        if name not in os.environ:
            continue
        value = os.environ[name]
        if not value:
            raise ConfigurationError(f"{name} must not be empty")
        source = Path(value).expanduser()
        try:
            resolved = source.resolve(strict=True)
        except OSError as error:
            raise ConfigurationError(f"{name} does not exist: {source}") from error
        if name == "SSL_CERT_DIR":
            if not resolved.is_dir():
                raise ConfigurationError(f"{name} is not a directory: {resolved}")
        else:
            if not resolved.is_file():
                raise ConfigurationError(f"{name} is not a regular file: {resolved}")
            try:
                mode = resolved.stat().st_mode
            except OSError as error:
                raise ConfigurationError(f"cannot stat {name}: {resolved}") from error
            if mode & 0o111:
                raise ConfigurationError(f"{name} must not be executable: {resolved}")
        mounts[name] = (resolved, SANDBOX_CUSTOM_CA_DIR / name)
    return mounts


def resolve_system_codex_isolation() -> SystemCodexIsolation:
    if SYSTEM_CODEX_PATH.is_symlink():
        try:
            target = SYSTEM_CODEX_PATH.resolve(strict=True)
        except OSError as error:
            raise ConfigurationError(
                f"system codex symlink target cannot be resolved: {SYSTEM_CODEX_PATH}"
            ) from error
        usr_root = Path("/usr").resolve(strict=True)
        if not target.is_relative_to(usr_root):
            raise ConfigurationError(
                f"system codex symlink target is outside /usr: {target}"
            )
        parts = target.parts
        marker_length = len(SYSTEM_CODEX_PACKAGE_MARKER)
        matches = [
            index
            for index in range(len(parts) - marker_length + 1)
            if parts[index : index + marker_length] == SYSTEM_CODEX_PACKAGE_MARKER
        ]
        if len(matches) != 1:
            raise ConfigurationError(
                "system codex symlink target is not in one unambiguous "
                f"npm @openai/codex package: {target}"
            )
        package_root = Path(*parts[: matches[0] + marker_length])
        if not (package_root / "package.json").is_file():
            raise ConfigurationError(
                f"system codex npm package is missing package.json: {package_root}"
            )
        return SystemCodexIsolation(
            mode="npm-package-mask", package_root=package_root
        )
    if not SYSTEM_CODEX_PATH.exists():
        return SystemCodexIsolation(mode="absent", package_root=None)
    if not SYSTEM_CODEX_PATH.is_file():
        raise ConfigurationError(
            f"system codex path is not a regular file: {SYSTEM_CODEX_PATH}"
        )
    return SystemCodexIsolation(mode="standalone-overlay", package_root=None)


def build_child_environment(
    custom_ca_mounts: dict[str, tuple[Path, Path]],
) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in SAFE_CODEX_ENV_NAMES
        if name in os.environ
    }
    for name, (_source, sandbox_path) in custom_ca_mounts.items():
        environment[name] = str(sandbox_path)
    environment["HOME"] = str(SANDBOX_HOME)
    environment["CODEX_HOME"] = str(SANDBOX_CODEX_HOME)
    environment["TMPDIR"] = str(SANDBOX_TMPDIR)
    environment["PATH"] = "/usr/bin:/bin"
    return environment


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_panelist(value: str) -> Panelist:
    role, separator, model_effort = value.partition("=")
    model, effort_separator, effort = model_effort.rpartition(":")
    if not separator or not effort_separator or not role or not model or not effort:
        raise ConfigurationError(
            f"invalid --panelist {value!r}; expected role=model:effort"
        )
    if role not in ROLES:
        raise ConfigurationError(
            f"invalid panelist role {role!r}; expected one of {', '.join(ROLES)}"
        )
    return Panelist(role=role, model=model, effort=effort)


def validate_panelists(values: Sequence[str], model_efforts: dict[str, set[str]]) -> list[Panelist]:
    if not 2 <= len(values) <= 3:
        raise ConfigurationError("--panelist must be supplied exactly 2 or 3 times")
    panelists = [parse_panelist(value) for value in values]
    roles = [panelist.role for panelist in panelists]
    if len(set(roles)) != len(roles):
        raise ConfigurationError("panelist roles must be unique")
    configurations = [(panelist.model, panelist.effort) for panelist in panelists]
    if len(set(configurations)) != len(configurations):
        raise ConfigurationError(
            "panelists using the same model must use distinct reasoning efforts"
        )
    for panelist in panelists:
        if panelist.model == "codex-auto-review":
            raise ConfigurationError("codex-auto-review cannot be used as a panelist model")
        if panelist.effort == "ultra":
            raise ConfigurationError(
                "ultra effort cannot be used because it enables automatic delegation"
            )
        if panelist.model not in model_efforts:
            raise ConfigurationError(
                f"model {panelist.model!r} is not present in models_cache.json"
            )
        supported = model_efforts[panelist.model]
        if panelist.effort not in supported:
            rendered = ", ".join(sorted(supported))
            raise ConfigurationError(
                f"effort {panelist.effort!r} is not supported by model "
                f"{panelist.model!r}; supported efforts: {rendered}"
            )
    return panelists


def models_cache_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home is None:
        return Path.home() / ".codex" / "models_cache.json"
    if not codex_home:
        raise ConfigurationError("CODEX_HOME must not be empty")
    return Path(codex_home) / "models_cache.json"


def load_model_efforts(path: Path) -> dict[str, set[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"cannot read models cache {path}: {error}") from error
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"invalid JSON in models cache {path}: {error}") from error
    if not isinstance(document, dict) or "models" not in document:
        raise ConfigurationError("models_cache.json must contain a models array")
    models = document["models"]
    if not isinstance(models, list) or not models:
        raise ConfigurationError("models_cache.json models must be a non-empty array")

    result: dict[str, set[str]] = {}
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            raise ConfigurationError(f"models[{index}] must be an object")
        if "slug" not in model or not isinstance(model["slug"], str) or not model["slug"]:
            raise ConfigurationError(f"models[{index}].slug must be a non-empty string")
        slug = model["slug"]
        if slug in result:
            raise ConfigurationError(f"duplicate model slug {slug!r} in models_cache.json")
        if "supported_reasoning_levels" not in model:
            raise ConfigurationError(
                f"model {slug!r} is missing supported_reasoning_levels"
            )
        levels = model["supported_reasoning_levels"]
        if not isinstance(levels, list) or not levels:
            raise ConfigurationError(
                f"model {slug!r} supported_reasoning_levels must be non-empty"
            )
        efforts: set[str] = set()
        for level_index, level in enumerate(levels):
            if (
                not isinstance(level, dict)
                or "effort" not in level
                or not isinstance(level["effort"], str)
                or not level["effort"]
            ):
                raise ConfigurationError(
                    f"model {slug!r} reasoning level {level_index} has invalid effort"
                )
            effort = level["effort"]
            if effort in efforts:
                raise ConfigurationError(
                    f"model {slug!r} has duplicate effort {effort!r}"
                )
            efforts.add(effort)
        result[slug] = efforts
    return result


def resolve_input_file(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(f"{label} does not exist: {path}") from error
    if not resolved.is_file():
        raise ConfigurationError(f"{label} is not a regular file: {resolved}")
    return resolved


def resolve_workspace(path: Path) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(f"workspace does not exist: {path}") from error
    if not resolved.is_dir():
        raise ConfigurationError(f"workspace is not a directory: {resolved}")
    return resolved


def read_brief(path: Path) -> str:
    try:
        brief = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"cannot read UTF-8 brief {path}: {error}") from error
    if not brief.strip():
        raise ConfigurationError(f"brief must not be empty: {path}")
    return brief


def validate_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ConfigurationError("--timeout must be a positive finite number")
    return value


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def create_run_dir() -> Path:
    try:
        run_dir = Path(tempfile.mkdtemp(prefix=RUN_PREFIX, dir=RUN_ROOT))
        (run_dir / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    except BaseException:
        if "run_dir" in locals() and run_dir.exists():
            shutil.rmtree(run_dir)
        raise
    return run_dir


def create_private_directories(
    run_dir: Path,
    panelists: Sequence[Panelist],
    system_codex_isolation: SystemCodexIsolation,
) -> tuple[dict[str, Path], dict[str, dict[int, Path]]]:
    private_root = run_dir / "private"
    private_root.mkdir(mode=0o700)
    private_dirs: dict[str, Path] = {}
    schema_paths: dict[str, dict[int, Path]] = {}
    for panelist in panelists:
        private_dir = private_root / panelist.role
        private_dir.mkdir(mode=0o700)
        (private_dir / "home").mkdir(mode=0o700)
        (private_dir / "codex-home").mkdir(mode=0o700)
        (private_dir / "tmp").mkdir(mode=0o700)
        if system_codex_isolation.mode == "npm-package-mask":
            (private_dir / SYSTEM_CODEX_MASK_NAME).mkdir(mode=0o500)
        private_dirs[panelist.role] = private_dir
        role_schemas = {
            round_number: private_dir / f"round{round_number}-output-schema.json"
            for round_number in ROUND_SCHEMAS
        }
        for round_number, schema_path in role_schemas.items():
            write_json(schema_path, ROUND_SCHEMAS[round_number])
        schema_paths[panelist.role] = role_schemas
    return private_dirs, schema_paths


def require_exact_fields(value: object, fields: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(
            f"{location} must contain exactly {', '.join(sorted(fields))}"
        )
    return value


def require_nonempty_string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a non-empty string")
    return value


def require_string_array(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array")
    for index, item in enumerate(value):
        require_nonempty_string(item, f"{location}[{index}]")
    return value


def require_enum(value: object, allowed: set[str], location: str) -> str:
    selected = require_nonempty_string(value, location)
    if selected not in allowed:
        raise ValueError(
            f"{location} must be one of {', '.join(sorted(allowed))}"
        )
    return selected


def validate_round_one(response: object) -> dict[str, Any]:
    result = require_exact_fields(
        response,
        {"position", "claims", "risks", "verification_requests"},
        "Round 1 output",
    )
    require_nonempty_string(result["position"], "Round 1 position")
    claims = result["claims"]
    if not isinstance(claims, list) or not claims:
        raise ValueError("Round 1 claims must be a non-empty array")
    claim_fields = {
        "id",
        "claim",
        "basis",
        "evidence",
        "confidence",
        "falsification_condition",
    }
    claim_ids: set[str] = set()
    for index, raw_claim in enumerate(claims):
        claim = require_exact_fields(
            raw_claim, claim_fields, f"Round 1 claims[{index}]"
        )
        claim_id = require_nonempty_string(
            claim["id"], f"Round 1 claims[{index}].id"
        )
        if claim_id in claim_ids:
            raise ValueError(f"Round 1 claim id is duplicated: {claim_id!r}")
        claim_ids.add(claim_id)
        for field in ("claim", "evidence", "falsification_condition"):
            require_nonempty_string(
                claim[field], f"Round 1 claims[{index}].{field}"
            )
        require_enum(
            claim["basis"],
            {"fact", "inference", "assumption"},
            f"Round 1 claims[{index}].basis",
        )
        require_enum(
            claim["confidence"],
            {"low", "medium", "high"},
            f"Round 1 claims[{index}].confidence",
        )
    require_string_array(result["risks"], "Round 1 risks")
    require_string_array(
        result["verification_requests"], "Round 1 verification_requests"
    )
    return result


def validate_round_two(
    response: object,
    panelist: Panelist,
    critique_targets: dict[str, set[str]],
) -> dict[str, Any]:
    result = require_exact_fields(response, {"critiques"}, "Round 2 output")
    critiques = result["critiques"]
    if not isinstance(critiques, list) or not critiques:
        raise ValueError("Round 2 critiques must be a non-empty array")
    critique_fields = {
        "id",
        "target_panelist",
        "target_claim_id",
        "outcome",
        "issue_type",
        "analysis",
        "attempted_falsification",
        "result",
        "verification",
        "severity",
    }
    critique_ids: set[str] = set()
    for index, raw_critique in enumerate(critiques):
        critique = require_exact_fields(
            raw_critique, critique_fields, f"Round 2 critiques[{index}]"
        )
        for field in (
            "id",
            "target_panelist",
            "target_claim_id",
            "issue_type",
            "analysis",
            "attempted_falsification",
            "result",
            "verification",
        ):
            require_nonempty_string(
                critique[field], f"Round 2 critiques[{index}].{field}"
            )
        critique_id = critique["id"]
        if critique_id in critique_ids:
            raise ValueError(f"Round 2 critique id is duplicated: {critique_id!r}")
        critique_ids.add(critique_id)
        target = critique["target_panelist"]
        if target not in critique_targets or target == panelist.role:
            raise ValueError(
                f"Round 2 critiques[{index}].target_panelist must name a Round 1 peer"
            )
        if critique["target_claim_id"] not in critique_targets[target]:
            raise ValueError(
                f"Round 2 critiques[{index}].target_claim_id does not exist for {target}"
            )
        require_enum(
            critique["outcome"],
            {"attack-established", "no-valid-attack"},
            f"Round 2 critiques[{index}].outcome",
        )
        require_enum(
            critique["severity"],
            {"low", "medium", "high"},
            f"Round 2 critiques[{index}].severity",
        )
    covered_targets = {
        critique["target_panelist"] for critique in result["critiques"]
    }
    if covered_targets != set(critique_targets):
        missing = sorted(set(critique_targets) - covered_targets)
        raise ValueError(
            "Round 2 critiques must target every Round 1 peer; missing: "
            + ", ".join(missing)
        )
    return result


def validate_round_three(
    response: object,
    expected_critiques: set[tuple[str, str]],
) -> dict[str, Any]:
    fields = {
        "final_position",
        "position_changed",
        "position_change_source",
        "position_change_reason",
        "position_change_evidence",
        "critique_responses",
        "residual_uncertainties",
        "confidence",
        "falsification_conditions",
    }
    result = require_exact_fields(response, fields, "Round 3 output")
    require_nonempty_string(result["final_position"], "Round 3 final_position")
    if not isinstance(result["position_changed"], bool):
        raise ValueError("Round 3 position_changed must be a boolean")
    change_source = require_enum(
        result["position_change_source"],
        {"none", "critique", "new-evidence"},
        "Round 3 position_change_source",
    )
    require_nonempty_string(
        result["position_change_reason"], "Round 3 position_change_reason"
    )
    if not isinstance(result["position_change_evidence"], str):
        raise ValueError("Round 3 position_change_evidence must be a string")
    for field in ("residual_uncertainties", "falsification_conditions"):
        require_string_array(result[field], f"Round 3 {field}")
    if not result["falsification_conditions"]:
        raise ValueError("Round 3 falsification_conditions must not be empty")
    require_enum(
        result["confidence"],
        {"low", "medium", "high"},
        "Round 3 confidence",
    )
    responses = result["critique_responses"]
    if not isinstance(responses, list):
        raise ValueError("Round 3 critique_responses must be an array")
    response_fields = {
        "critic_panelist",
        "critique_id",
        "decision",
        "reason",
        "evidence",
    }
    covered: set[tuple[str, str]] = set()
    concessions = 0
    for index, raw_response in enumerate(responses):
        critique_response = require_exact_fields(
            raw_response,
            response_fields,
            f"Round 3 critique_responses[{index}]",
        )
        critic = require_enum(
            critique_response["critic_panelist"],
            set(ROLES),
            f"Round 3 critique_responses[{index}].critic_panelist",
        )
        critique_id = require_nonempty_string(
            critique_response["critique_id"],
            f"Round 3 critique_responses[{index}].critique_id",
        )
        key = (critic, critique_id)
        if key in covered:
            raise ValueError(f"Round 3 critique response is duplicated: {key!r}")
        if key not in expected_critiques:
            raise ValueError(f"Round 3 critique response is not addressed here: {key!r}")
        covered.add(key)
        decision = require_enum(
            critique_response["decision"],
            {"concede", "defend"},
            f"Round 3 critique_responses[{index}].decision",
        )
        if decision == "concede":
            concessions += 1
        for field in ("reason", "evidence"):
            require_nonempty_string(
                critique_response[field],
                f"Round 3 critique_responses[{index}].{field}",
            )
    if covered != expected_critiques:
        missing = sorted(expected_critiques - covered)
        raise ValueError(f"Round 3 must answer every addressed critique; missing: {missing}")
    if not result["position_changed"] and change_source != "none":
        raise ValueError("Round 3 unchanged positions must use source none")
    if result["position_changed"] and change_source == "none":
        raise ValueError("Round 3 changed positions must name a change source")
    if result["position_changed"] and change_source == "critique" and concessions == 0:
        raise ValueError("Round 3 changed positions must concede at least one critique")
    if (
        result["position_changed"]
        and change_source == "new-evidence"
        and not result["position_change_evidence"].strip()
    ):
        raise ValueError("Round 3 new-evidence changes require concrete evidence")
    return result


def validate_response(
    path: Path,
    round_number: int,
    panelist: Panelist,
    validation_context: ValidationContext,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("output-last-message file was not created")
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"cannot read output-last-message: {error}") from error
    try:
        response = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"output-last-message is not valid JSON: {error}") from error
    if round_number == 1:
        return validate_round_one(response)
    if round_number == 2:
        if validation_context.critique_targets is None:
            raise ValueError("Round 2 validation targets were not configured")
        return validate_round_two(
            response, panelist, validation_context.critique_targets
        )
    if round_number == 3:
        if validation_context.expected_critiques is None:
            raise ValueError("Round 3 expected critiques were not configured")
        return validate_round_three(
            response, validation_context.expected_critiques
        )
    raise ValueError(f"unsupported round: {round_number}")


def codex_command(
    private_dir: Path,
    denied_paths: Sequence[Path],
    panelist: Panelist,
    schema_path: Path,
    output_path: Path,
) -> list[str]:
    disabled_skill = (
        "skills.config=[{path="
        + json.dumps(str(SKILL_DIR))
        + ",enabled=false}]"
    )
    shell_environment = (
        "shell_environment_policy.include_only="
        + json.dumps(list(SAFE_TOOL_ENV_NAMES))
    )
    denied_filesystem = ",".join(
        f'{json.dumps(str(path))}="deny"' for path in denied_paths
    )
    permissions = (
        'permissions.adversarial-panelist={extends=":read-only",filesystem={'
        + denied_filesystem
        + "}}"
    )
    return [
        "/codex",
        "--strict-config",
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "--cd",
        str(SANDBOX_PRIVATE_DIR),
        "--model",
        panelist.model,
        "-c",
        f'model_reasoning_effort={json.dumps(panelist.effort)}',
        "-c",
        "features.hooks=false",
        "-c",
        "features.multi_agent=false",
        "-c",
        "features.apps=false",
        "-c",
        "features.remote_plugin=false",
        "-c",
        "features.plugins=false",
        "-c",
        "features.plugin_sharing=false",
        "-c",
        shell_environment,
        "-c",
        'default_permissions="adversarial-panelist"',
        "-c",
        permissions,
        "-c",
        disabled_skill,
        "--output-schema",
        str(SANDBOX_PRIVATE_DIR / schema_path.name),
        "--output-last-message",
        str(SANDBOX_PRIVATE_DIR / output_path.name),
        "-",
    ]


def build_bwrap_command(
    bwrap_path: Path,
    codex_path: Path,
    workspace: Path,
    private_dir: Path,
    auth_path: Path | None,
    custom_ca_mounts: dict[str, tuple[Path, Path]],
    system_codex_isolation: SystemCodexIsolation,
    child_command: Sequence[str],
) -> list[str]:
    command = [
        str(bwrap_path),
        "--die-with-parent",
        "--unshare-pid",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for runtime_path in (Path("/lib"), Path("/lib64")):
        if runtime_path.exists():
            command.extend(("--ro-bind", str(runtime_path), str(runtime_path)))
    command.extend(("--dir", "/etc"))
    for etc_path in (
        Path("/etc/ssl"),
        Path("/etc/ca-certificates"),
        Path("/etc/pki"),
        Path("/etc/resolv.conf"),
        Path("/etc/hosts"),
        Path("/etc/nsswitch.conf"),
        Path("/etc/gai.conf"),
        Path("/etc/host.conf"),
        Path("/etc/localtime"),
    ):
        if etc_path.exists():
            command.extend(("--ro-bind", str(etc_path), str(etc_path)))
    if custom_ca_mounts:
        command.extend(("--dir", str(SANDBOX_CUSTOM_CA_DIR)))
        for source, sandbox_path in custom_ca_mounts.values():
            command.extend(("--ro-bind", str(source), str(sandbox_path)))
    command.extend(
        (
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--ro-bind",
            str(workspace),
            str(SANDBOX_WORKSPACE),
            "--bind",
            str(private_dir),
            str(SANDBOX_PRIVATE_DIR),
            "--symlink",
            "usr/bin",
            "/bin",
            "--ro-bind",
            str(codex_path),
            "/codex",
        )
    )
    if system_codex_isolation.mode == "npm-package-mask":
        if system_codex_isolation.package_root is None:
            raise AssertionError("npm package isolation requires a package root")
        command.extend(
            (
                "--ro-bind",
                str(private_dir / SYSTEM_CODEX_MASK_NAME),
                str(system_codex_isolation.package_root),
            )
        )
    elif system_codex_isolation.mode == "standalone-overlay":
        command.extend(("--ro-bind", "/dev/null", str(SYSTEM_CODEX_PATH)))
    elif system_codex_isolation.mode != "absent":
        raise AssertionError(
            f"unsupported system codex isolation mode: {system_codex_isolation.mode}"
        )
    if auth_path is not None:
        private_auth = private_dir / "codex-home" / "auth.json"
        if not private_auth.exists():
            private_auth.touch(mode=0o600)
        command.extend(
            (
                "--ro-bind",
                str(auth_path),
                str(SANDBOX_AUTH_PATH),
            )
        )
    command.extend(
        (
            "--chdir",
            str(SANDBOX_PRIVATE_DIR),
            "--setenv",
            "HOME",
            str(SANDBOX_HOME),
            "--setenv",
            "CODEX_HOME",
            str(SANDBOX_CODEX_HOME),
            "--setenv",
            "TMPDIR",
            str(SANDBOX_TMPDIR),
            "--",
        )
    )
    command.extend(child_command)
    return command


def run_attempt(
    *,
    attempt_number: int,
    round_number: int,
    panelist: Panelist,
    prompt: str,
    codex_path: Path,
    bwrap_path: Path,
    workspace: Path,
    auth_path: Path | None,
    custom_ca_mounts: dict[str, tuple[Path, Path]],
    system_codex_isolation: SystemCodexIsolation,
    schema_path: Path,
    run_dir: Path,
    timeout: float,
    validation_context: ValidationContext,
    processes: ActiveProcessRegistry,
    private_dirs: dict[str, Path],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    private_dir = private_dirs[panelist.role]
    output_path = private_dir / (
        f"round{round_number}_{panelist.role}_attempt{attempt_number}.json"
    )
    if output_path.exists():
        output_path.unlink()
    trace_path = private_dir / (
        f"trace_round{round_number}_{panelist.role}_attempt{attempt_number}.json"
    )
    denied_paths = [
        SANDBOX_AUTH_PATH,
        Path("/proc"),
        run_dir / "panel_record.json",
    ] + [
        sandbox_path for _source, sandbox_path in custom_ca_mounts.values()
    ] + [
        path for role, path in private_dirs.items() if role != panelist.role
    ]
    if system_codex_isolation.mode == "npm-package-mask":
        denied_paths.append(SANDBOX_PRIVATE_DIR / SYSTEM_CODEX_MASK_NAME)
    child_command = codex_command(
        private_dir,
        denied_paths,
        panelist,
        schema_path,
        output_path,
    )
    bwrap_command = build_bwrap_command(
        bwrap_path,
        codex_path,
        workspace,
        private_dir,
        auth_path,
        custom_ca_mounts,
        system_codex_isolation,
        child_command,
    )
    attempt: dict[str, Any] = {
        "attempt": attempt_number,
        "started_at": utc_now(),
        "trace_path": str(trace_path),
    }
    trace: dict[str, Any] = {
        "prompt": prompt,
        "command": child_command,
        "bwrap_command": bwrap_command,
        "cwd": str(private_dir),
        "output_path": str(output_path),
        "stdout": "",
        "stderr": "",
    }
    started = time.monotonic()
    response: dict[str, Any] | None = None
    process: subprocess.Popen[str] | None = None
    registered = False
    try:
        processes.ensure_running()
        process = subprocess.Popen(
            bwrap_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=private_dir,
            env=build_child_environment(custom_ca_mounts),
            start_new_session=True,
        )
        processes.register(process)
        registered = True
        try:
            stdout, stderr = process.communicate(input=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = terminate_process_group(process)
            trace["stdout"] = stdout
            trace["stderr"] = stderr
            attempt["ok"] = False
            attempt["error_type"] = "timeout"
            attempt["error"] = f"codex exec exceeded {timeout} seconds"
        except BaseException as error:
            stdout, stderr = terminate_process_group(process)
            trace["stdout"] = stdout
            trace["stderr"] = stderr
            trace["interrupted_by"] = type(error).__name__
            raise
        else:
            kill_process_group(process)
            process.wait()
            trace["stdout"] = stdout
            trace["stderr"] = stderr
            attempt["returncode"] = process.returncode
            if process.returncode != 0:
                attempt["ok"] = False
                attempt["error_type"] = "nonzero-exit"
                attempt["error"] = f"codex exec exited with {process.returncode}"
            else:
                try:
                    response = validate_response(
                        output_path,
                        round_number,
                        panelist,
                        validation_context,
                    )
                except ValueError as error:
                    attempt["ok"] = False
                    attempt["error_type"] = "invalid-output"
                    attempt["error"] = str(error)
                    response = None
                else:
                    attempt["ok"] = True
    except subprocess.TimeoutExpired as error:
        raise AssertionError("timeout must be handled around Popen.communicate") from error
    except OSError as error:
        if process is not None:
            stdout, stderr = terminate_process_group(process)
            trace["stdout"] = stdout
            trace["stderr"] = stderr
        attempt["ok"] = False
        attempt["error_type"] = "spawn-error"
        attempt["error"] = str(error)
        trace["spawn_error"] = str(error)
    except BaseException as error:
        if process is not None:
            stdout, stderr = terminate_process_group(process)
            trace["stdout"] = stdout
            trace["stderr"] = stderr
        trace["interrupted_by"] = type(error).__name__
        raise
    finally:
        if process is not None and registered:
            processes.unregister(process)
        if output_path.exists() and output_path.is_file():
            try:
                trace["output_last_message"] = output_path.read_text(
                    encoding="utf-8"
                )
            except (OSError, UnicodeError) as error:
                trace["output_read_error"] = str(error)
        attempt["duration_seconds"] = round(time.monotonic() - started, 6)
        attempt["completed_at"] = utc_now()
        write_json(trace_path, trace)
    return attempt, response


def run_panelist(
    *,
    round_number: int,
    panelist: Panelist,
    prompt: str,
    codex_path: Path,
    bwrap_path: Path,
    workspace: Path,
    auth_path: Path | None,
    custom_ca_mounts: dict[str, tuple[Path, Path]],
    system_codex_isolation: SystemCodexIsolation,
    schema_path: Path,
    run_dir: Path,
    timeout: float,
    validation_context: ValidationContext,
    processes: ActiveProcessRegistry,
    private_dirs: dict[str, Path],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    for attempt_number in (1, 2):
        processes.ensure_running()
        attempt, response = run_attempt(
            attempt_number=attempt_number,
            round_number=round_number,
            panelist=panelist,
            prompt=prompt,
            codex_path=codex_path,
            bwrap_path=bwrap_path,
            workspace=workspace,
            auth_path=auth_path,
            custom_ca_mounts=custom_ca_mounts,
            system_codex_isolation=system_codex_isolation,
            schema_path=schema_path,
            run_dir=run_dir,
            timeout=timeout,
            validation_context=validation_context,
            processes=processes,
            private_dirs=private_dirs,
        )
        attempts.append(attempt)
        if response is not None:
            return {
                "status": "success",
                "attempts": attempts,
                "response": response,
            }
    return {
        "status": "failed",
        "attempts": attempts,
        "error": attempts[-1]["error"],
    }


def round_one_prompt(role: str, brief: str, workspace: Path) -> str:
    return (
        "You are an independent adversarial decision analyst.\n"
        f"Your assigned role is {role}.\n"
        f"Apply this methodology: {ROLE_METHODS[role]}\n"
        "Work independently and do not assume access to any peer analysis. State a position; "
        "give uniquely identified claims whose basis is fact, inference, or assumption; and "
        "list risks, verification requests, confidence, and falsification conditions.\n\n"
        f"Analysis workspace (read-only): {workspace}\n\n"
        "Decision brief:\n---\n"
        f"{brief.rstrip()}\n"
        "---\n"
    )


def round_two_prompt(
    role: str,
    brief: str,
    workspace: Path,
    round_one: dict[str, dict[str, Any]],
) -> str:
    peers = {
        peer_role: entry["response"]
        for peer_role, entry in round_one.items()
        if peer_role != role and entry["status"] == "success"
    }
    return (
        "You are conducting Round 2 adversarial peer criticism.\n"
        f"Your assigned role is {role}.\n"
        f"Apply this methodology: {ROLE_METHODS[role]}\n"
        "Critique every peer below at least once. For each critique, copy the exact peer role "
        "and claim id into target_panelist and target_claim_id. Give each assessment a unique "
        "id. Use attack-established only when the attack succeeds; otherwise use "
        "no-valid-attack and record the attempted falsification and its result.\n\n"
        f"Analysis workspace (read-only): {workspace}\n\n"
        "Decision brief:\n---\n"
        f"{brief.rstrip()}\n"
        "---\n\n"
        "Peer Round 1 analyses:\n"
        f"{json.dumps(peers, ensure_ascii=False, indent=2)}\n"
    )


def round_three_prompt(
    role: str,
    brief: str,
    workspace: Path,
    round_one: dict[str, dict[str, Any]],
    round_two: dict[str, dict[str, Any]],
) -> str:
    first_round = {
        peer_role: entry["response"]
        for peer_role, entry in round_one.items()
        if entry["status"] == "success"
    }
    addressed_critiques = []
    for critic_role, entry in round_two.items():
        if entry["status"] != "success":
            continue
        for critique in entry["response"]["critiques"]:
            if critique["target_panelist"] == role:
                addressed_critiques.append(
                    {"critic_panelist": critic_role, **critique}
                )
    return (
        "You are producing your Round 3 final view after adversarial deliberation.\n"
        f"Your assigned role is {role}.\n"
        f"Apply this methodology: {ROLE_METHODS[role]}\n"
        "Reassess the decision using the Round 1 record and only the Round 2 critiques "
        "addressed to you. Give a concrete final "
        "position and state whether it changed. Answer every addressed critique exactly once "
        "by critic_panelist and critique_id with concede or defend, reasons, and evidence. If "
        "the position changed because of a critique, use source critique and concede at least "
        "one critique. If new evidence caused the change, use source new-evidence and state the "
        "concrete evidence; critiques may still all be defended. Use source none when unchanged. "
        "Include residual uncertainties, confidence, and at least one falsification condition.\n\n"
        f"Analysis workspace (read-only): {workspace}\n\n"
        "Decision brief:\n---\n"
        f"{brief.rstrip()}\n"
        "---\n\n"
        "Round 1 analyses:\n"
        f"{json.dumps(first_round, ensure_ascii=False, indent=2)}\n\n"
        "Round 2 critiques addressed to you:\n"
        f"{json.dumps(addressed_critiques, ensure_ascii=False, indent=2)}\n"
    )


def execute_round(
    *,
    round_number: int,
    round_name: str,
    panelists: Sequence[Panelist],
    prompts: dict[str, str],
    codex_path: Path,
    bwrap_path: Path,
    workspace: Path,
    auth_path: Path | None,
    custom_ca_mounts: dict[str, tuple[Path, Path]],
    system_codex_isolation: SystemCodexIsolation,
    schema_paths: dict[str, Path],
    run_dir: Path,
    timeout: float,
    validation_contexts: dict[str, ValidationContext],
    processes: ActiveProcessRegistry,
    private_dirs: dict[str, Path],
    record: dict[str, Any],
    record_path: Path,
) -> dict[str, Any]:
    started_at = utc_now()
    unordered: dict[str, dict[str, Any]] = {}
    executor = ThreadPoolExecutor(max_workers=len(panelists))
    futures: dict[Any, str] = {}
    try:
        futures = {
            executor.submit(
                run_panelist,
                round_number=round_number,
                panelist=panelist,
                prompt=prompts[panelist.role],
                codex_path=codex_path,
                bwrap_path=bwrap_path,
                workspace=workspace,
                auth_path=auth_path,
                custom_ca_mounts=custom_ca_mounts,
                system_codex_isolation=system_codex_isolation,
                schema_path=schema_paths[panelist.role],
                run_dir=run_dir,
                timeout=timeout,
                validation_context=validation_contexts[panelist.role],
                processes=processes,
                private_dirs=private_dirs,
            ): panelist.role
            for panelist in panelists
        }
        for future in as_completed(futures):
            unordered[futures[future]] = future.result()
    except BaseException as error:
        processes.abort_all()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        record["status"] = "failed"
        record["completed_at"] = utc_now()
        record["successful_panelists"] = []
        record["failure"] = {
            "type": type(error).__name__,
            "message": str(error) or type(error).__name__,
            "round": round_number,
        }
        write_json(record_path, record)
        raise PanelRunError(
            f"round {round_number} aborted by {type(error).__name__}",
            record_path,
        ) from error
    else:
        executor.shutdown(wait=True)
    entries = {panelist.role: unordered[panelist.role] for panelist in panelists}
    successful = [
        panelist.role
        for panelist in panelists
        if entries[panelist.role]["status"] == "success"
    ]
    failed = [
        panelist.role
        for panelist in panelists
        if entries[panelist.role]["status"] == "failed"
    ]
    return {
        "round": round_number,
        "name": round_name,
        "started_at": started_at,
        "completed_at": utc_now(),
        "panelists": entries,
        "successful_panelists": successful,
        "failed_panelists": failed,
    }


def record_attempt_events(record: dict[str, Any], round_record: dict[str, Any]) -> None:
    round_number = round_record["round"]
    for role, entry in round_record["panelists"].items():
        attempts = entry["attempts"]
        if len(attempts) == 2:
            record["retries"].append(
                {
                    "round": round_number,
                    "role": role,
                    "after_error_type": attempts[0]["error_type"],
                }
            )
        for attempt in attempts:
            if not attempt["ok"]:
                record["attempt_failures"].append(
                    {
                        "round": round_number,
                        "role": role,
                        "attempt": attempt["attempt"],
                        "error_type": attempt["error_type"],
                        "error": attempt["error"],
                    }
                )
        if entry["status"] == "failed":
            record["panelist_failures"].append(
                {
                    "round": round_number,
                    "role": role,
                    "error": entry["error"],
                }
            )


def fail_if_insufficient(
    record: dict[str, Any],
    record_path: Path,
    round_record: dict[str, Any],
) -> None:
    successful = round_record["successful_panelists"]
    if len(successful) >= 2:
        return
    record["status"] = "failed"
    record["completed_at"] = utc_now()
    record["successful_panelists"] = successful
    record["error"] = (
        f"round {round_record['round']} retained {len(successful)} panelist(s); "
        "at least 2 are required"
    )
    write_json(record_path, record)
    raise PanelRunError(record["error"], record_path)


def _run_panel_impl(
    args: argparse.Namespace, run_state: dict[str, Any]
) -> dict[str, Any]:
    brief_path = resolve_input_file(args.brief, "brief")
    brief = read_brief(brief_path)
    workspace = resolve_workspace(args.workspace)
    timeout = validate_timeout(args.timeout)
    cache_path = models_cache_path().expanduser().resolve()
    model_efforts = load_model_efforts(cache_path)
    panelists = validate_panelists(args.panelist, model_efforts)
    custom_ca_mounts = resolve_custom_ca_mounts()
    system_codex_isolation = resolve_system_codex_isolation()
    codex_executable = shutil.which("codex")
    if codex_executable is None:
        raise ConfigurationError("codex executable was not found on PATH")
    codex_path = Path(codex_executable).resolve()
    if (
        system_codex_isolation.package_root is not None
        and codex_path.is_relative_to(system_codex_isolation.package_root)
    ):
        raise ConfigurationError(
            "selected codex executable is inside the masked system npm package; "
            "configure a standalone codex executable earlier on PATH"
        )
    bwrap_executable = shutil.which("bwrap")
    if bwrap_executable is None:
        raise ConfigurationError("bwrap executable was not found on PATH")
    bwrap_path = Path(bwrap_executable).resolve()
    candidate_auth_path = cache_path.parent / "auth.json"
    auth_path = candidate_auth_path if candidate_auth_path.is_file() else None
    if not SKILL_FILE.is_file():
        raise ConfigurationError(f"skill file does not exist: {SKILL_FILE}")

    processes = ActiveProcessRegistry()
    run_dir = create_run_dir()
    record_path = run_dir / "panel_record.json"
    run_state["run_dir"] = run_dir
    run_state["record_path"] = record_path
    run_state["processes"] = processes
    private_dirs, schema_paths = create_private_directories(
        run_dir, panelists, system_codex_isolation
    )
    record: dict[str, Any] = {
        "record_version": 1,
        "status": "running",
        "created_at": utc_now(),
        "run_dir": str(run_dir),
        "actual_configuration": {
            "brief_path": str(brief_path),
            "workspace": str(workspace),
            "timeout_seconds": timeout,
            "models_cache_path": str(cache_path),
            "codex_path": str(codex_path),
            "bwrap_path": str(bwrap_path),
            "panelists": [
                {
                    "role": panelist.role,
                    "model": panelist.model,
                    "effort": panelist.effort,
                }
                for panelist in panelists
            ],
            "child_isolation": {
                "ephemeral": True,
                "permission_profile": "adversarial-panelist",
                "isolation_runtime": str(bwrap_path),
                "system_codex_isolation": {
                    "mode": system_codex_isolation.mode,
                    "package_root": (
                        str(system_codex_isolation.package_root)
                        if system_codex_isolation.package_root is not None
                        else None
                    ),
                },
                "sandbox_mounts": {
                    "/workspace": {"source": str(workspace), "mode": "read-only"},
                    "/panel": {"source": "role-private", "mode": "read-write"},
                    "/proc": {"source": "new-pid-namespace", "mode": "read-only"},
                },
                "private_directories": {
                    role: str(path) for role, path in private_dirs.items()
                },
                "skip_git_repo_check": True,
                "ignore_user_config": True,
                "hooks": False,
                "multi_agent": False,
                "apps": False,
                "remote_plugin": False,
                "plugins": False,
                "plugin_sharing": False,
                "disabled_skill": str(SKILL_DIR),
                "prompt_transport": "stdin",
                "environment_allowlist": list(SAFE_CODEX_ENV_NAMES),
                "tool_environment_allowlist": list(SAFE_TOOL_ENV_NAMES),
            },
        },
        "rounds": [],
        "retries": [],
        "attempt_failures": [],
        "panelist_failures": [],
    }
    run_state["record"] = record
    write_json(record_path, record)

    first_prompts = {
        panelist.role: round_one_prompt(
            panelist.role, brief, SANDBOX_WORKSPACE
        )
        for panelist in panelists
    }
    first_contexts = {
        panelist.role: ValidationContext() for panelist in panelists
    }
    first = execute_round(
        round_number=1,
        round_name="blind-analysis",
        panelists=panelists,
        prompts=first_prompts,
        codex_path=codex_path,
        bwrap_path=bwrap_path,
        workspace=workspace,
        auth_path=auth_path,
        custom_ca_mounts=custom_ca_mounts,
        system_codex_isolation=system_codex_isolation,
        schema_paths={
            panelist.role: schema_paths[panelist.role][1]
            for panelist in panelists
        },
        run_dir=run_dir,
        timeout=timeout,
        validation_contexts=first_contexts,
        processes=processes,
        private_dirs=private_dirs,
        record=record,
        record_path=record_path,
    )
    record["rounds"].append(first)
    record_attempt_events(record, first)
    write_json(record_path, record)
    fail_if_insufficient(record, record_path, first)

    first_entries = first["panelists"]
    active_panelists = [
        panelist
        for panelist in panelists
        if panelist.role in first["successful_panelists"]
    ]
    second_prompts = {
        panelist.role: round_two_prompt(
            panelist.role, brief, SANDBOX_WORKSPACE, first_entries
        )
        for panelist in active_panelists
    }
    round_one_claim_ids = {
        role: {claim["id"] for claim in entry["response"]["claims"]}
        for role, entry in first_entries.items()
        if entry["status"] == "success"
    }
    critique_targets_by_role = {
        panelist.role: {
            role: claim_ids
            for role, claim_ids in round_one_claim_ids.items()
            if role != panelist.role
        }
        for panelist in active_panelists
    }
    second_contexts = {
        role: ValidationContext(critique_targets=targets)
        for role, targets in critique_targets_by_role.items()
    }
    second = execute_round(
        round_number=2,
        round_name="mutual-criticism",
        panelists=active_panelists,
        prompts=second_prompts,
        codex_path=codex_path,
        bwrap_path=bwrap_path,
        workspace=workspace,
        auth_path=auth_path,
        custom_ca_mounts=custom_ca_mounts,
        system_codex_isolation=system_codex_isolation,
        schema_paths={
            panelist.role: schema_paths[panelist.role][2]
            for panelist in active_panelists
        },
        run_dir=run_dir,
        timeout=timeout,
        validation_contexts=second_contexts,
        processes=processes,
        private_dirs=private_dirs,
        record=record,
        record_path=record_path,
    )
    record["rounds"].append(second)
    record_attempt_events(record, second)
    write_json(record_path, record)
    fail_if_insufficient(record, record_path, second)

    second_entries = second["panelists"]
    active_panelists = [
        panelist
        for panelist in active_panelists
        if panelist.role in second["successful_panelists"]
    ]
    expected_critiques_by_role: dict[str, set[tuple[str, str]]] = {
        panelist.role: set() for panelist in active_panelists
    }
    for critic_role, entry in second_entries.items():
        if entry["status"] != "success":
            continue
        for critique in entry["response"]["critiques"]:
            target = critique["target_panelist"]
            if target in expected_critiques_by_role:
                expected_critiques_by_role[target].add(
                    (critic_role, critique["id"])
                )
    third_prompts = {
        panelist.role: round_three_prompt(
            panelist.role,
            brief,
            SANDBOX_WORKSPACE,
            first_entries,
            second_entries,
        )
        for panelist in active_panelists
    }
    third_contexts = {
        role: ValidationContext(expected_critiques=expected)
        for role, expected in expected_critiques_by_role.items()
    }
    third = execute_round(
        round_number=3,
        round_name="final-view",
        panelists=active_panelists,
        prompts=third_prompts,
        codex_path=codex_path,
        bwrap_path=bwrap_path,
        workspace=workspace,
        auth_path=auth_path,
        custom_ca_mounts=custom_ca_mounts,
        system_codex_isolation=system_codex_isolation,
        schema_paths={
            panelist.role: schema_paths[panelist.role][3]
            for panelist in active_panelists
        },
        run_dir=run_dir,
        timeout=timeout,
        validation_contexts=third_contexts,
        processes=processes,
        private_dirs=private_dirs,
        record=record,
        record_path=record_path,
    )
    record["rounds"].append(third)
    record_attempt_events(record, third)
    write_json(record_path, record)
    fail_if_insufficient(record, record_path, third)

    final_roles = third["successful_panelists"]
    record["status"] = (
        "completed" if len(final_roles) == len(panelists) else "degraded"
    )
    record["successful_panelists"] = final_roles
    record["completed_at"] = utc_now()
    write_json(record_path, record)
    return {
        "run_dir": str(run_dir),
        "record_path": str(record_path),
        "status": record["status"],
        "successful_panelists": final_roles,
    }


def run_panel(args: argparse.Namespace) -> dict[str, Any]:
    run_state: dict[str, Any] = {}
    try:
        return _run_panel_impl(args, run_state)
    except (ConfigurationError, PanelRunError):
        raise
    except BaseException as error:
        if "record_path" not in run_state:
            raise
        processes = run_state["processes"]
        processes.abort_all()
        record_path = run_state["record_path"]
        if "record" in run_state:
            record = run_state["record"]
        else:
            record = {
                "record_version": 1,
                "status": "failed",
                "created_at": utc_now(),
                "run_dir": str(run_state["run_dir"]),
                "rounds": [],
                "retries": [],
                "attempt_failures": [],
                "panelist_failures": [],
            }
        record["status"] = "failed"
        record["completed_at"] = utc_now()
        record["successful_panelists"] = []
        record["failure"] = {
            "type": type(error).__name__,
            "message": str(error) or type(error).__name__,
        }
        write_json(record_path, record)
        raise PanelRunError(
            f"run aborted by {type(error).__name__}", record_path
        ) from error


def cleanup_run_dir(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ConfigurationError("refusing to clean up a symlink")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as error:
        raise ConfigurationError(f"run directory does not exist: {path}") from error
    if resolved.parent != RUN_ROOT or not resolved.name.startswith(RUN_PREFIX):
        raise ConfigurationError(
            f"refusing to remove non-panel directory outside {RUN_ROOT}/{RUN_PREFIX}*"
        )
    if not resolved.is_dir():
        raise ConfigurationError(f"run path is not a directory: {resolved}")
    if resolved.stat().st_uid != os.getuid():
        raise ConfigurationError(f"run directory is not owned by uid {os.getuid()}")
    marker = resolved / MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise ConfigurationError(f"run directory marker is missing: {marker}")
    try:
        marker_content = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"cannot read run directory marker: {error}") from error
    if marker_content != MARKER_CONTENT:
        raise ConfigurationError(f"run directory marker is invalid: {marker}")
    if marker.stat().st_uid != os.getuid():
        raise ConfigurationError(f"run directory marker is not owned by uid {os.getuid()}")
    shutil.rmtree(resolved)
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or clean up an isolated adversarial Codex panel."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run a three-round panel")
    run_parser.add_argument(
        "--brief", required=True, type=Path, help="UTF-8 decision brief file"
    )
    run_parser.add_argument(
        "--workspace", required=True, type=Path, help="read-only analysis workspace"
    )
    run_parser.add_argument(
        "--panelist",
        required=True,
        action="append",
        metavar="ROLE=MODEL:EFFORT",
        help="panelist configuration; repeat exactly 2 or 3 times",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="per-attempt timeout (default: 300)",
    )
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="remove one marked panel run directory"
    )
    cleanup_parser.add_argument("--run-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            summary = run_panel(args)
        elif args.command == "cleanup":
            removed = cleanup_run_dir(args.run_dir)
            summary = {"removed": str(removed)}
        else:
            parser.error(f"unsupported command: {args.command}")
            return 2
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PanelRunError as error:
        print(f"error: {error}; record={error.record_path}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
