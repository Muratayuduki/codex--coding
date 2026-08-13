#!/usr/bin/env python3
"""Integration tests for the adversarial panel runner."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import run_panel as runner


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER = SCRIPT_DIR / "run_panel.py"
AUTH_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

args = sys.argv[1:]
prompt = sys.stdin.read()

def executable_probe(path):
    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except OSError as error:
        return {"spawned": False, "errno": error.errno}
    except subprocess.TimeoutExpired:
        return {"spawned": True, "returncode": None}
    return {"spawned": True, "returncode": completed.returncode}

def option(name):
    index = args.index(name)
    return args[index + 1]

output_path = Path(option("--output-last-message"))
match = re.fullmatch(r"round([123])_(.+)_attempt([12])\.json", output_path.name)
if match is None:
    raise SystemExit("unexpected output path")
round_number, role, attempt = match.groups()
mode_match = re.search(r"FAKE_MODE:([a-z0-9-]+)", prompt)
mode = mode_match.group(1) if mode_match else "success"
system_codex = Path("/usr/bin/codex")
system_codex_link = Path(os.readlink(system_codex))
if not system_codex_link.is_absolute():
    system_codex_link = system_codex.parent / system_codex_link
system_codex_target = Path(os.path.abspath(system_codex_link))
target_parts = system_codex_target.parts
package_marker = ("node_modules", "@openai", "codex")
package_index = next(
    index
    for index in range(len(target_parts) - len(package_marker) + 1)
    if target_parts[index:index + len(package_marker)] == package_marker
)
system_codex_package_root = Path(
    *target_parts[:package_index + len(package_marker)]
)
known_package_executables = {
    system_codex_target,
    system_codex_package_root / "bin" / "rg",
    system_codex_package_root
    / "node_modules"
    / "@openai"
    / "codex-linux-x64"
    / "vendor"
    / "x86_64-unknown-linux-musl"
    / "codex"
    / "codex",
    system_codex_package_root
    / "node_modules"
    / "@openai"
    / "codex-linux-x64"
    / "vendor"
    / "x86_64-unknown-linux-musl"
    / "path"
    / "rg",
}
discovered_package_executables = {
    path
    for path in system_codex_package_root.rglob("*")
    if path.is_file() and os.access(path, os.X_OK)
}
package_executables = known_package_executables | discovered_package_executables
custom_ca_names = (
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
)
invocation = {
    "args": args,
    "prompt": prompt,
    "round": int(round_number),
    "role": role,
    "attempt": int(attempt),
    "cwd": os.getcwd(),
    "env": dict(os.environ),
    "workspace_visible": Path("/workspace/visible.txt").is_file(),
    "own_private_visible": Path("/panel").is_dir(),
    "ca_cert_readable": Path("/etc/ssl/cert.pem").is_file(),
    "ca_bundle_readable": Path("/etc/ssl/certs/ca-certificates.crt").is_file(),
    "bin_sh_exists": Path("/bin/sh").is_file(),
    "bin_is_symlink": Path("/bin").is_symlink(),
    "main_codex_visible": Path("/codex").is_file(),
    "workspace_shell_readable": subprocess.run(
        ["/bin/sh", "-c", "test -r /workspace/visible.txt"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0,
    "system_codex_target": str(system_codex_target),
    "system_codex_package_root": str(system_codex_package_root),
    "system_codex_probe": executable_probe(system_codex),
    "system_codex_target_probe": executable_probe(system_codex_target),
    "package_executable_candidates": {
        str(path): {
            "visible": path.exists(),
            "probe": executable_probe(path),
        }
        for path in sorted(package_executables)
    },
    "custom_ca_environment": {
        name: os.environ[name] for name in custom_ca_names if name in os.environ
    },
    "custom_ca_readable": {
        name: Path(os.environ[name]).exists()
        for name in custom_ca_names
        if name in os.environ
    },
}
probe_paths = re.findall(r"PROBE_PATH:([^\s]+)", prompt)
invocation["probe_visibility"] = {
    path: Path(path).exists() for path in probe_paths
}
permission = next(
    (arg for arg in args if arg.startswith("permissions.adversarial-panelist=")),
    "",
)
denied_paths = re.findall(r'"(/[^"]+)"="deny"', permission)
invocation["denied_visibility"] = {
    path: Path(path).exists() for path in denied_paths
}
invocation_path = output_path.with_suffix(".invocation.json")
invocation_path.write_text(json.dumps(invocation), encoding="utf-8")

pid_dir_match = re.search(r"PID_DIR:([^\s]+)", prompt)
if pid_dir_match:
    pid_dir = Path(pid_dir_match.group(1))
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / f"child-{role}.pid").write_text(str(os.getpid()), encoding="utf-8")

first_attempt = attempt == "1" and role == "first-principles" and round_number == "1"
if mode == "grandchild-timeout" and first_attempt:
    heartbeat = output_path.parent / "grandchild-heartbeat"
    grandchild = subprocess.Popen([
        sys.executable,
        "-c",
        "from pathlib import Path; import time; p=Path('/panel/grandchild-heartbeat'); i=0\nwhile True: p.write_text(str(i)); i+=1; time.sleep(0.02)",
    ])
    time.sleep(60)
if mode == "background-grandchild" and first_attempt:
    heartbeat = output_path.parent / "background-grandchild-heartbeat"
    grandchild = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import time; p=Path('/panel/background-grandchild-heartbeat'); i=0\nwhile True: p.write_text(str(i)); i+=1; time.sleep(0.02)",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 1
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
if mode == "sleep-always":
    time.sleep(60)
if mode == "retry-timeout" and first_attempt:
    time.sleep(0.5)
if mode == "retry-nonzero" and first_attempt:
    print("planned non-zero failure", file=sys.stderr)
    raise SystemExit(17)
if mode == "retry-invalid" and first_attempt:
    output_path.write_text("{not-json", encoding="utf-8")
    raise SystemExit(0)
if mode == "fail-one" and role == "outside-view":
    print("planned panelist failure", file=sys.stderr)
    raise SystemExit(23)

if round_number == "1":
    result = {
        "position": f"position::{role}::round1",
        "claims": [{
            "id": f"claim-{role}",
            "claim": f"claim::{role}::round1",
            "basis": "inference",
            "evidence": f"evidence::{role}::round1",
            "confidence": "medium",
            "falsification_condition": f"falsify::{role}::round1",
        }],
        "risks": [f"risk::{role}::round1"],
        "verification_requests": [f"verify::{role}::round1"],
    }
elif round_number == "2":
    roles = ("first-principles", "outside-view", "falsifier")
    targets = [
        target for target in roles
        if target != role and f"position::{target}::round1" in prompt
    ]
    outcome = "no-valid-attack" if mode == "no-valid-attack" else "attack-established"
    result = {
        "critiques": [{
            "id": f"critique-{role}-to-{target}",
            "target_panelist": target,
            "target_claim_id": f"claim-{target}",
            "outcome": outcome,
            "issue_type": "none-found" if outcome == "no-valid-attack" else "unsupported-assumption",
            "analysis": f"analysis::{role}::to::{target}",
            "attempted_falsification": f"attempt::{role}::to::{target}",
            "result": f"result::{role}::to::{target}",
            "verification": f"verify::{role}::to::{target}",
            "severity": "low" if outcome == "no-valid-attack" else "high",
        } for target in targets],
    }
else:
    roles = ("first-principles", "outside-view", "falsifier")
    addressed = [
        critic for critic in roles
        if critic != role and f"critique-{critic}-to-{role}" in prompt
    ]
    result = {
        "final_position": f"final::{role}::round3",
        "position_changed": False,
        "position_change_source": "none",
        "position_change_reason": f"unchanged::{role}::round3",
        "position_change_evidence": "",
        "critique_responses": [{
            "critic_panelist": critic,
            "critique_id": f"critique-{critic}-to-{role}",
            "decision": "defend",
            "reason": f"reason::{role}::from::{critic}",
            "evidence": f"evidence::{role}::from::{critic}",
        } for critic in addressed],
        "residual_uncertainties": [f"uncertainty::{role}::round3"],
        "confidence": "medium",
        "falsification_conditions": [f"falsify::{role}::round3"],
    }

if mode == "retry-r1-basis-list" and role == "first-principles" and round_number == "1" and attempt == "1":
    result["claims"][0]["basis"] = []
if mode == "retry-r1-confidence-null" and role == "first-principles" and round_number == "1" and attempt == "1":
    result["claims"][0]["confidence"] = None
if mode == "retry-r2-outcome-object" and role == "first-principles" and round_number == "2" and attempt == "1":
    result["critiques"][0]["outcome"] = {}
if mode == "retry-r2-severity-list" and role == "first-principles" and round_number == "2" and attempt == "1":
    result["critiques"][0]["severity"] = []
if mode == "retry-r3-confidence-object" and role == "first-principles" and round_number == "3" and attempt == "1":
    result["confidence"] = {}
if mode == "retry-r3-decision-null" and role == "first-principles" and round_number == "3" and attempt == "1":
    result["critique_responses"][0]["decision"] = None
if mode == "retry-invalid-target" and role == "first-principles" and round_number == "2" and attempt == "1":
    result["critiques"][0]["target_claim_id"] = "missing-claim"
if mode == "retry-missing-peer" and role == "first-principles" and round_number == "2" and attempt == "1":
    result["critiques"] = result["critiques"][:1]
if mode == "retry-missing-critique-response" and role == "first-principles" and round_number == "3" and attempt == "1":
    result["critique_responses"] = result["critique_responses"][:-1]
if mode == "retry-changed-without-concession" and role == "first-principles" and round_number == "3" and attempt == "1":
    result["position_changed"] = True
    result["position_change_source"] = "critique"
if mode == "retry-unchanged-with-source" and role == "first-principles" and round_number == "3" and attempt == "1":
    result["position_change_source"] = "critique"
if mode == "new-evidence-change" and round_number == "3":
    result["position_changed"] = True
    result["position_change_source"] = "new-evidence"
    result["position_change_reason"] = f"new evidence changed {role}"
    result["position_change_evidence"] = f"artifact-hash::{role}"
if mode == "retry-empty-falsification" and role == "first-principles" and round_number == "3" and attempt == "1":
    result["falsification_conditions"] = []
output_path.write_text(json.dumps(result), encoding="utf-8")
'''


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="panel-runner-test-"))
        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir()
        self.codex_home = self.temp_dir / "codex-home"
        self.codex_home.mkdir()
        self.provider_socket_path = self.codex_home / "provider.sock"
        self.provider_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.provider_socket.bind(str(self.provider_socket_path))
        self.workspace = self.temp_dir / "workspace"
        self.workspace.mkdir()
        (self.workspace / "visible.txt").write_text("visible", encoding="utf-8")
        self.ambient_secret = self.temp_dir / "ambient-secret.txt"
        self.ambient_secret.write_text("secret", encoding="utf-8")
        self.other_run_artifact = Path("/tmp") / f"other-panel-{os.getpid()}"
        self.other_run_artifact.write_text("other", encoding="utf-8")
        self.brief = self.temp_dir / "brief.md"
        fake_codex = self.bin_dir / "codex"
        fake_codex.write_text(
            FAKE_CODEX.replace(
                "#!/usr/bin/env python3", f"#!{sys.executable}", 1
            ),
            encoding="utf-8",
        )
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
        models = {
            "models": [
                {
                    "slug": "model-alpha",
                    "supported_reasoning_levels": [
                        {"effort": "medium"},
                        {"effort": "high"},
                        {"effort": "ultra"},
                    ],
                },
                {
                    "slug": "model-beta",
                    "supported_reasoning_levels": [{"effort": "low"}],
                },
                {
                    "slug": "codex-auto-review",
                    "supported_reasoning_levels": [{"effort": "high"}],
                },
            ]
        }
        (self.codex_home / "models_cache.json").write_text(
            json.dumps(models), encoding="utf-8"
        )
        self.env = os.environ.copy()
        self.env["PATH"] = os.pathsep.join((str(self.bin_dir), self.env["PATH"]))
        self.env["CODEX_HOME"] = str(self.codex_home)
        self.env["PANEL_TOKEN"] = "must-not-leak"
        self.env["UNRELATED_SECRET"] = "must-not-leak"
        self.env["UNRELATED_SETTING"] = "must-not-leak"
        self.env["HTTPS_PROXY"] = "http://proxy.example"
        for name in AUTH_ENV_NAMES:
            self.env[name] = "must-not-leak"
        for name in runner.CUSTOM_CA_ENV_NAMES:
            self.env.pop(name, None)
        self.run_dirs: set[Path] = set()
        self.write_brief("success")

    def tearDown(self) -> None:
        self.provider_socket.close()
        for run_dir in self.run_dirs:
            if run_dir.exists():
                shutil.rmtree(run_dir)
        shutil.rmtree(self.temp_dir)
        if self.other_run_artifact.exists():
            self.other_run_artifact.unlink()

    def write_brief(self, mode: str, *lines: str) -> None:
        content = ["Choose whether to ship the migration.", f"FAKE_MODE:{mode}"]
        content.extend(
            (
                f"PROBE_PATH:{self.ambient_secret}",
                f"PROBE_PATH:{self.other_run_artifact}",
                f"PROBE_PATH:{self.env['HOME']}",
                f"PROBE_PATH:{self.provider_socket_path}",
            )
        )
        content.extend(lines)
        self.brief.write_text("\n".join(content) + "\n", encoding="utf-8")

    def command(self, *extra: str, timeout: str = "1") -> list[str]:
        return [
            sys.executable,
            str(RUNNER),
            "run",
            "--brief",
            str(self.brief),
            "--workspace",
            str(self.workspace),
            "--timeout",
            timeout,
            *extra,
        ]

    def track_record_from_result(self, completed: subprocess.CompletedProcess[str]) -> Path | None:
        if completed.returncode == 0:
            record_path = Path(json.loads(completed.stdout)["record_path"])
        else:
            match = re.search(r"record=([^\s;]+)", completed.stderr)
            if match is None:
                return None
            record_path = Path(match.group(1))
        self.run_dirs.add(record_path.parent)
        return record_path

    def invoke(
        self,
        *extra: str,
        mode: str = "success",
        timeout: str = "1",
    ) -> subprocess.CompletedProcess[str]:
        self.write_brief(mode)
        completed = subprocess.run(
            self.command(*extra, timeout=timeout),
            capture_output=True,
            text=True,
            env=self.env,
            timeout=15,
            check=False,
        )
        self.track_record_from_result(completed)
        return completed

    @staticmethod
    def panelists_three() -> tuple[str, ...]:
        return (
            "--panelist",
            "first-principles=model-alpha:high",
            "--panelist",
            "outside-view=model-alpha:medium",
            "--panelist",
            "falsifier=model-beta:low",
        )

    @staticmethod
    def panelists_two() -> tuple[str, ...]:
        return (
            "--panelist",
            "first-principles=model-alpha:high",
            "--panelist",
            "outside-view=model-alpha:medium",
        )

    def read_invocations(self) -> list[dict[str, object]]:
        paths = [
            path
            for run_dir in self.run_dirs
            if run_dir.exists()
            for path in run_dir.rglob("*.invocation.json")
        ]
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(paths)
        ]

    def assert_process_gone(self, pid: int) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not (Path("/proc") / str(pid)).exists():
                return
            time.sleep(0.02)
        self.fail(f"process {pid} survived process-group termination")

    @staticmethod
    def descendants(root_pid: int) -> set[int]:
        descendants: set[int] = set()
        frontier = {root_pid}
        while frontier:
            parents = frontier
            frontier = set()
            for stat_path in Path("/proc").glob("[0-9]*/stat"):
                try:
                    stat = stat_path.read_text(encoding="utf-8")
                except (FileNotFoundError, PermissionError):
                    continue
                pid_text, remainder = stat.split(" (", 1)
                _command, suffix = remainder.rsplit(") ", 1)
                fields = suffix.split()
                pid = int(pid_text)
                parent = int(fields[1])
                if parent in parents and pid not in descendants:
                    descendants.add(pid)
                    frontier.add(pid)
        return descendants

    def test_three_round_flow_is_isolated_filtered_and_trace_backed(self) -> None:
        completed = self.invoke(*self.panelists_three())

        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        run_dir = Path(summary["run_dir"])
        record = json.loads(Path(summary["record_path"]).read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "completed")
        self.assertEqual(len(record["rounds"]), 3)
        serialized_record = json.dumps(record)
        for forbidden in ('"prompt"', '"command"', '"stdout"', '"stderr"'):
            self.assertNotIn(forbidden, serialized_record)

        invocations = self.read_invocations()
        self.assertEqual(len(invocations), 9)
        round_one = [item for item in invocations if item["round"] == 1]
        for item in round_one:
            prompt = str(item["prompt"])
            other_roles = set(runner.ROLES) - {item["role"]}
            self.assertTrue(all(role not in prompt for role in other_roles))
            self.assertNotIn("position::", prompt)

        round_three = [item for item in invocations if item["round"] == 3]
        for item in round_three:
            role = item["role"]
            other_roles = set(runner.ROLES) - {role}
            for critic in other_roles:
                self.assertIn(f"critique-{critic}-to-{role}", item["prompt"])
            for target in other_roles:
                self.assertNotIn(f"-to-{target}", item["prompt"])

        for item in invocations:
            args = item["args"]
            self.assertEqual(args[:2], ["--strict-config", "exec"])
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--ephemeral", args)
            self.assertIn("--skip-git-repo-check", args)
            self.assertNotIn("--sandbox", args)
            private_dir = run_dir / "private" / item["role"]
            self.assertEqual(args[args.index("--cd") + 1], "/panel")
            self.assertEqual(item["cwd"], "/panel")
            self.assertEqual(
                Path(args[args.index("--output-schema") + 1]).parent,
                Path("/panel"),
            )
            self.assertEqual(
                Path(args[args.index("--output-last-message") + 1]).parent,
                Path("/panel"),
            )
            self.assertIn("/workspace", item["prompt"])
            self.assertIn("features.apps=false", args)
            self.assertIn("features.remote_plugin=false", args)
            self.assertIn("features.hooks=false", args)
            self.assertIn("features.multi_agent=false", args)
            self.assertIn("features.plugins=false", args)
            self.assertIn("features.plugin_sharing=false", args)
            shell_environment = next(
                arg
                for arg in args
                if arg.startswith("shell_environment_policy.include_only=")
            )
            self.assertNotIn("PROXY", shell_environment.upper())
            self.assertTrue(any('adversarial-panel",enabled=false' in arg for arg in args))
            self.assertIn('default_permissions="adversarial-panelist"', args)
            permission = next(
                arg
                for arg in args
                if arg.startswith("permissions.adversarial-panelist=")
            )
            self.assertIn('extends=":read-only"', permission)
            self.assertIn('"/panel/codex-home/auth.json"="deny"', permission)
            self.assertIn('"/panel/system-codex-mask"="deny"', permission)
            self.assertIn('"/proc"="deny"', permission)
            self.assertNotIn('"/codex"="deny"', permission)
            self.assertNotIn('"/usr/bin/codex"="deny"', permission)
            self.assertNotIn(
                f'{json.dumps(item["system_codex_target"])}="deny"', permission
            )
            self.assertIn(
                f'{json.dumps(str(run_dir / "panel_record.json"))}="deny"',
                permission,
            )
            self.assertNotIn(
                f'{json.dumps(str(private_dir))}="deny"', permission
            )
            for peer_role in set(runner.ROLES) - {item["role"]}:
                peer_dir = run_dir / "private" / peer_role
                if peer_dir.exists():
                    self.assertIn(
                        f'{json.dumps(str(peer_dir))}="deny"', permission
                    )
            child_env = item["env"]
            self.assertNotIn("PANEL_TOKEN", child_env)
            self.assertNotIn("UNRELATED_SECRET", child_env)
            self.assertNotIn("UNRELATED_SETTING", child_env)
            for name in AUTH_ENV_NAMES:
                self.assertNotIn(name, runner.SAFE_CODEX_ENV_NAMES)
                self.assertNotIn(name, runner.SAFE_TOOL_ENV_NAMES)
                self.assertNotIn(name, child_env)
            self.assertEqual(child_env["PATH"], "/usr/bin:/bin")
            self.assertEqual(child_env["HTTPS_PROXY"], "http://proxy.example")
            self.assertEqual(child_env["HOME"], "/panel/home")
            self.assertEqual(child_env["CODEX_HOME"], "/panel/codex-home")
            self.assertEqual(child_env["TMPDIR"], "/panel/tmp")
            self.assertTrue(item["workspace_visible"])
            self.assertTrue(item["own_private_visible"])
            self.assertTrue(item["ca_cert_readable"])
            self.assertTrue(item["ca_bundle_readable"])
            self.assertTrue(item["bin_sh_exists"])
            self.assertTrue(item["bin_is_symlink"])
            self.assertTrue(item["main_codex_visible"])
            self.assertTrue(item["workspace_shell_readable"])
            self.assertFalse(item["system_codex_probe"]["spawned"])
            self.assertIn(
                item["system_codex_probe"]["errno"], {errno.EACCES, errno.ENOENT}
            )
            self.assertFalse(item["system_codex_target_probe"]["spawned"])
            self.assertIn(
                item["system_codex_target_probe"]["errno"],
                {errno.EACCES, errno.ENOENT},
            )
            self.assertTrue(item["package_executable_candidates"])
            for candidate in item["package_executable_candidates"].values():
                self.assertFalse(candidate["visible"])
                self.assertFalse(candidate["probe"]["spawned"])
                self.assertIn(
                    candidate["probe"]["errno"], {errno.EACCES, errno.ENOENT}
                )
            self.assertTrue(
                all(not visible for visible in item["probe_visibility"].values())
            )
            self.assertFalse(
                item["probe_visibility"][str(self.provider_socket_path)]
            )
            self.assertTrue(
                all(
                    not visible
                    for path, visible in item["denied_visibility"].items()
                    if path
                    not in {
                        "/proc",
                        "/panel/system-codex-mask",
                    }
                )
            )
            self.assertTrue(item["denied_visibility"]["/proc"])

        for round_record in record["rounds"]:
            for entry in round_record["panelists"].values():
                for attempt in entry["attempts"]:
                    trace_path = Path(attempt["trace_path"])
                    self.assertTrue(trace_path.is_file())
                    trace = json.loads(trace_path.read_text(encoding="utf-8"))
                    self.assertIn("prompt", trace)
                    self.assertIn("command", trace)
                    self.assertIn("stdout", trace)
                    self.assertIn("stderr", trace)
                    self.assertEqual(trace["bwrap_command"][0], "/usr/bin/bwrap")
                    self.assertEqual(
                        trace["command"][:3], ["/codex", "--strict-config", "exec"]
                    )
                    self.assertIn("/etc/ca-certificates", trace["bwrap_command"])
                    symlink_index = trace["bwrap_command"].index("--symlink")
                    self.assertEqual(
                        trace["bwrap_command"][symlink_index + 1 : symlink_index + 3],
                        ["usr/bin", "/bin"],
                    )
                    codex_bind = trace["bwrap_command"].index("/codex")
                    package_root = trace["bwrap_command"].index(
                        "/usr/lib/node_modules/@openai/codex"
                    )
                    self.assertLess(codex_bind, package_root)
                    self.assertEqual(
                        trace["bwrap_command"][package_root - 2], "--ro-bind"
                    )
                    self.assertEqual(
                        Path(trace["bwrap_command"][package_root - 1]).name,
                        "system-codex-mask",
                    )

        isolation = record["actual_configuration"]["child_isolation"]
        self.assertEqual(isolation["isolation_runtime"], "/usr/bin/bwrap")
        self.assertEqual(
            isolation["system_codex_isolation"],
            {
                "mode": "npm-package-mask",
                "package_root": "/usr/lib/node_modules/@openai/codex",
            },
        )
        self.assertIn("/workspace", isolation["sandbox_mounts"])

    def test_model_effort_role_and_panel_size_validation_fail_fast(self) -> None:
        invalid_sets = (
            ("--panelist", "unknown=model-alpha:high", "--panelist", "outside-view=model-alpha:medium"),
            ("--panelist", "first-principles=missing:high", "--panelist", "outside-view=model-alpha:medium"),
            ("--panelist", "first-principles=model-alpha:ultra", "--panelist", "outside-view=model-alpha:medium"),
            ("--panelist", "first-principles=codex-auto-review:high", "--panelist", "outside-view=model-alpha:medium"),
            ("--panelist", "first-principles=model-alpha:high"),
            (
                "--panelist", "first-principles=model-alpha:high",
                "--panelist", "first-principles=model-alpha:medium",
            ),
            (
                "--panelist", "first-principles=model-alpha:high",
                "--panelist", "outside-view=model-alpha:high",
            ),
        )
        for panelists in invalid_sets:
            with self.subTest(panelists=panelists):
                completed = self.invoke(*panelists)
                self.assertEqual(completed.returncode, 2)
                self.assertIsNone(self.track_record_from_result(completed))

    def test_missing_bwrap_fails_before_run_directory_creation(self) -> None:
        env = self.env.copy()
        env["PATH"] = str(self.bin_dir)
        completed = subprocess.run(
            self.command(*self.panelists_two()),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("bwrap", completed.stderr)
        self.assertIsNone(self.track_record_from_result(completed))

    def test_system_codex_analysis_rejects_unsafe_symlinks_and_classifies_standalone(
        self,
    ) -> None:
        outside_target = self.temp_dir / "outside-system-codex"
        outside_target.write_text("outside\n", encoding="utf-8")
        outside_link = self.temp_dir / "outside-system-codex-link"
        outside_link.symlink_to(outside_target)

        with mock.patch.object(runner, "SYSTEM_CODEX_PATH", outside_link):
            with self.assertRaisesRegex(
                runner.ConfigurationError, "outside /usr"
            ):
                runner.resolve_system_codex_isolation()

        standalone = self.temp_dir / "standalone-system-codex"
        standalone.write_text("standalone\n", encoding="utf-8")
        with mock.patch.object(runner, "SYSTEM_CODEX_PATH", standalone):
            isolation = runner.resolve_system_codex_isolation()
            command = runner.build_bwrap_command(
                Path("/usr/bin/bwrap"),
                self.bin_dir / "codex",
                self.workspace,
                self.temp_dir,
                None,
                {},
                isolation,
                ["/codex", "--version"],
            )
        self.assertEqual(isolation.mode, "standalone-overlay")
        self.assertIsNone(isolation.package_root)
        standalone_index = command.index(str(standalone))
        self.assertEqual(
            command[standalone_index - 2 : standalone_index + 1],
            ["--ro-bind", "/dev/null", str(standalone)],
        )

    def test_system_npm_codex_cannot_be_selected_as_the_main_executable(self) -> None:
        env = self.env.copy()
        env["PATH"] = "/usr/bin"

        completed = subprocess.run(
            self.command(*self.panelists_two()),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("configure a standalone codex executable", completed.stderr)
        self.assertIsNone(self.track_record_from_result(completed))

    def test_custom_ca_file_is_mounted_at_a_fixed_sandbox_path(self) -> None:
        custom_ca = self.temp_dir / "custom-ca.pem"
        custom_ca.write_text("test custom CA\n", encoding="utf-8")
        env = self.env.copy()
        env["SSL_CERT_FILE"] = str(custom_ca)

        completed = subprocess.run(
            self.command(*self.panelists_two()),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )
        record_path = self.track_record_from_result(completed)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIsNotNone(record_path)
        sandbox_ca = str(runner.SANDBOX_CUSTOM_CA_DIR / "SSL_CERT_FILE")
        invocations = self.read_invocations()
        self.assertTrue(invocations)
        for invocation in invocations:
            self.assertEqual(
                invocation["custom_ca_environment"]["SSL_CERT_FILE"], sandbox_ca
            )
            self.assertTrue(invocation["custom_ca_readable"]["SSL_CERT_FILE"])
            permission = next(
                arg
                for arg in invocation["args"]
                if arg.startswith("permissions.adversarial-panelist=")
            )
            self.assertIn(f'{json.dumps(sandbox_ca)}="deny"', permission)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        trace_path = Path(
            record["rounds"][0]["panelists"]["first-principles"]["attempts"][0][
                "trace_path"
            ]
        )
        bwrap_command = json.loads(trace_path.read_text(encoding="utf-8"))[
            "bwrap_command"
        ]
        sandbox_index = bwrap_command.index(sandbox_ca)
        self.assertEqual(
            bwrap_command[sandbox_index - 2 : sandbox_index + 1],
            ["--ro-bind", str(custom_ca.resolve()), sandbox_ca],
        )

    def test_missing_custom_ca_path_fails_before_run_directory_creation(self) -> None:
        env = self.env.copy()
        missing = self.temp_dir / "missing-extra-ca.pem"
        env["NODE_EXTRA_CA_CERTS"] = str(missing)

        completed = subprocess.run(
            self.command(*self.panelists_two()),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("NODE_EXTRA_CA_CERTS does not exist", completed.stderr)
        self.assertIsNone(self.track_record_from_result(completed))

    def test_timeout_nonzero_invalid_json_and_enum_types_retry_once(self) -> None:
        cases = (
            ("retry-timeout", 1, "0.2"),
            ("retry-nonzero", 1, "1"),
            ("retry-invalid", 1, "1"),
            ("retry-r1-basis-list", 1, "1"),
            ("retry-r1-confidence-null", 1, "1"),
            ("retry-r2-outcome-object", 2, "1"),
            ("retry-r2-severity-list", 2, "1"),
            ("retry-r3-confidence-object", 3, "1"),
            ("retry-r3-decision-null", 3, "1"),
        )
        for mode, round_number, timeout in cases:
            with self.subTest(mode=mode):
                completed = self.invoke(*self.panelists_two(), mode=mode, timeout=timeout)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                record_path = Path(json.loads(completed.stdout)["record_path"])
                record = json.loads(record_path.read_text(encoding="utf-8"))
                entry = record["rounds"][round_number - 1]["panelists"]["first-principles"]
                self.assertEqual(len(entry["attempts"]), 2)
                self.assertFalse(entry["attempts"][0]["ok"])
                self.assertTrue(entry["attempts"][1]["ok"])

    def test_round_two_no_valid_attack_is_valid(self) -> None:
        completed = self.invoke(*self.panelists_two(), mode="no-valid-attack")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(
            Path(json.loads(completed.stdout)["record_path"]).read_text(encoding="utf-8")
        )
        for entry in record["rounds"][1]["panelists"].values():
            self.assertEqual(len(entry["attempts"]), 1)
            self.assertTrue(
                all(
                    critique["outcome"] == "no-valid-attack"
                    for critique in entry["response"]["critiques"]
                )
            )

    def test_semantically_incomplete_round_outputs_are_retried(self) -> None:
        cases = (
            ("retry-invalid-target", self.panelists_two(), 2),
            ("retry-missing-peer", self.panelists_three(), 2),
            ("retry-missing-critique-response", self.panelists_three(), 3),
            ("retry-changed-without-concession", self.panelists_two(), 3),
            ("retry-unchanged-with-source", self.panelists_two(), 3),
            ("retry-empty-falsification", self.panelists_two(), 3),
        )
        for mode, panelists, round_number in cases:
            with self.subTest(mode=mode):
                completed = self.invoke(*panelists, mode=mode)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                record = json.loads(
                    Path(json.loads(completed.stdout)["record_path"]).read_text(encoding="utf-8")
                )
                entry = record["rounds"][round_number - 1]["panelists"]["first-principles"]
                self.assertEqual(entry["attempts"][0]["error_type"], "invalid-output")
                self.assertTrue(entry["attempts"][1]["ok"])

    def test_new_evidence_change_can_defend_every_critique(self) -> None:
        completed = self.invoke(
            *self.panelists_two(), mode="new-evidence-change"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(
            Path(json.loads(completed.stdout)["record_path"]).read_text(
                encoding="utf-8"
            )
        )
        for entry in record["rounds"][2]["panelists"].values():
            response = entry["response"]
            self.assertTrue(response["position_changed"])
            self.assertEqual(response["position_change_source"], "new-evidence")
            self.assertTrue(response["position_change_evidence"])
            self.assertTrue(
                all(item["decision"] == "defend" for item in response["critique_responses"])
            )

    def test_one_of_three_can_fail_but_two_of_two_cannot(self) -> None:
        degraded = self.invoke(*self.panelists_three(), mode="fail-one")
        self.assertEqual(degraded.returncode, 0, degraded.stderr)
        degraded_record = json.loads(
            Path(json.loads(degraded.stdout)["record_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(degraded_record["status"], "degraded")

        failed = self.invoke(*self.panelists_two(), mode="fail-one")
        self.assertEqual(failed.returncode, 1)
        failed_record_path = self.track_record_from_result(failed)
        self.assertIsNotNone(failed_record_path)
        failed_record = json.loads(failed_record_path.read_text(encoding="utf-8"))
        self.assertEqual(failed_record["status"], "failed")

    def test_timeout_kills_grandchild_process_group(self) -> None:
        completed = self.invoke(
            *self.panelists_two(), mode="grandchild-timeout", timeout="0.2"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(json.loads(completed.stdout)["run_dir"])
        heartbeat = next(run_dir.rglob("grandchild-heartbeat"))
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.2)
        self.assertEqual(heartbeat.read_text(encoding="utf-8"), before)

    def test_success_kills_detached_io_background_grandchild(self) -> None:
        completed = self.invoke(
            *self.panelists_two(), mode="background-grandchild"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(json.loads(completed.stdout)["run_dir"])
        heartbeat = next(run_dir.rglob("background-grandchild-heartbeat"))
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.2)
        self.assertEqual(heartbeat.read_text(encoding="utf-8"), before)

    def test_worker_exception_kills_active_groups_and_records_failure(self) -> None:
        pid_dir = self.temp_dir / "worker-pids"
        self.write_brief("sleep-always", f"PID_DIR:{pid_dir}")
        args = runner.build_parser().parse_args(self.command(*self.panelists_two())[2:])
        original = runner.run_panelist

        def injected_worker(**kwargs):
            if kwargs["panelist"].role == "first-principles":
                time.sleep(0.3)
                raise RuntimeError("injected worker failure")
            return original(**kwargs)

        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch.object(runner, "run_panelist", side_effect=injected_worker):
                with self.assertRaises(runner.PanelRunError) as caught:
                    runner.run_panel(args)
        record_path = caught.exception.record_path
        self.run_dirs.add(record_path.parent)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failure"]["type"], "RuntimeError")
        for pid_path in pid_dir.glob("*.pid"):
            self.assert_process_gone(int(pid_path.read_text(encoding="utf-8")))

    def test_sigint_kills_active_groups_and_returns_failed_record(self) -> None:
        self.write_brief("sleep-always")
        process = subprocess.Popen(
            self.command(*self.panelists_two(), timeout="10"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env,
        )
        deadline = time.monotonic() + 5
        child_pids: set[int] = set()
        while time.monotonic() < deadline and len(child_pids) < 4:
            child_pids = self.descendants(process.pid)
            time.sleep(0.02)
        self.assertGreaterEqual(len(child_pids), 2)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        record_path = self.track_record_from_result(completed)
        self.assertIsNotNone(record_path)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failure"]["type"], "KeyboardInterrupt")
        for pid in child_pids:
            self.assert_process_gone(pid)

    def test_marker_interrupt_removes_incomplete_run_directory(self) -> None:
        target = Path("/tmp") / f"adversarial-panel-interrupt-{os.getpid()}"

        def create_target(*_args, **_kwargs):
            target.mkdir(mode=0o700)
            return str(target)

        with mock.patch.object(
            runner.tempfile, "mkdtemp", side_effect=create_target
        ):
            with mock.patch.object(
                runner.Path, "write_text", side_effect=KeyboardInterrupt()
            ):
                with self.assertRaises(KeyboardInterrupt):
                    runner.create_run_dir()
        self.assertFalse(target.exists())

    def test_initialization_interrupt_creates_failed_record(self) -> None:
        self.write_brief("success")
        args = runner.build_parser().parse_args(self.command(*self.panelists_two())[2:])
        with mock.patch.dict(os.environ, self.env, clear=True):
            with mock.patch.object(
                runner,
                "create_private_directories",
                side_effect=KeyboardInterrupt(),
            ):
                with self.assertRaises(runner.PanelRunError) as caught:
                    runner.run_panel(args)
        record_path = caught.exception.record_path
        self.run_dirs.add(record_path.parent)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failure"]["type"], "KeyboardInterrupt")

    def test_cleanup_only_removes_explicit_marked_run_dir(self) -> None:
        completed = self.invoke(*self.panelists_two())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        run_dir = Path(json.loads(completed.stdout)["run_dir"])

        cleanup = subprocess.run(
            [sys.executable, str(RUNNER), "cleanup", "--run-dir", str(run_dir)],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(cleanup.returncode, 0, cleanup.stderr)
        self.assertFalse(run_dir.exists())

        unsafe = Path("/tmp") / f"not-a-panel-run-{os.getpid()}"
        unsafe.mkdir(exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(unsafe) if unsafe.exists() else None)
        (unsafe / runner.MARKER_NAME).write_text(
            runner.MARKER_CONTENT, encoding="utf-8"
        )
        refused = subprocess.run(
            [sys.executable, str(RUNNER), "cleanup", "--run-dir", str(unsafe)],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(unsafe.exists())


if __name__ == "__main__":
    unittest.main()
