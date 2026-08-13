from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex_env_guard.py")


class EnvGuardTest(unittest.TestCase):
    def run_guard(
        self,
        mode: str,
        cwd: Path,
        payload: dict[str, object] | None = None,
        *,
        extra_env: dict[str, str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        input_text = "" if payload is None else json.dumps(payload)
        env = os.environ.copy()
        for name, value in (extra_env or {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        return subprocess.run(
            [sys.executable, str(SCRIPT), mode],
            input=input_text,
            text=True,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def bash_payload(
        self,
        cwd: Path,
        command: str,
        *,
        workdir: Path | None = None,
    ) -> dict[str, object]:
        tool_input: dict[str, object] = {"command": command}
        if workdir is not None:
            tool_input["workdir"] = str(workdir)
        return {
            "cwd": str(cwd),
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": tool_input,
        }

    def apply_patch_payload(
        self,
        cwd: Path,
        patch: str,
        *,
        input_key: str = "patch",
    ) -> dict[str, object]:
        return {
            "cwd": str(cwd),
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {input_key: patch},
        }

    def assert_blocks(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "PreToolUse")
        self.assertEqual(hook_output["permissionDecision"], "deny")
        self.assertIn("env", hook_output["permissionDecisionReason"])

    def assert_allows(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def init_repo(self, root: Path) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def write_and_stage(self, root: Path, relative_path: str, content: str = "test\n") -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "add", "-f", "--", relative_path],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def codex_env(self, root: Path) -> dict[str, str]:
        return {"CODEX_HOME": str(root)}

    def test_bash_blocks_common_env_file_operations(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            commands = [
                "cat .env",
                "sed -n '1,5p' .env.local",
                "cp .env backup.env",
                "rm .dev.vars",
                "cat user-skills/example/.envrc",
                "mv .env .env.bak",
                "echo TOKEN=secret > .env",
            ]

            for command in commands:
                with self.subTest(command=command):
                    result = self.run_guard("pre-tool-use", root, self.bash_payload(root, command))
                    self.assert_blocks(result)

    def test_bash_allows_env_templates(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            commands = [
                "cat .env.example",
                "echo TOKEN= > .env.sample",
                "cp .dev.vars.example .dev.vars.template",
                "cat user-skills/example/.envrc.dist",
            ]

            for command in commands:
                with self.subTest(command=command):
                    result = self.run_guard("pre-tool-use", root, self.bash_payload(root, command))
                    self.assert_allows(result)

    def test_shell_command_blocks_powershell_env_file_operations(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            commands = [
                "Get-Content .env.local",
                "Set-Content -LiteralPath .env -Value secret",
                "Copy-Item .env backup.env",
                "Remove-Item .dev.vars",
                "'TOKEN=secret' | Out-File .env",
            ]

            for command in commands:
                with self.subTest(command=command):
                    payload = self.bash_payload(root, command)
                    payload["tool_name"] = "shell_command"
                    result = self.run_guard("pre-tool-use", root, payload)
                    self.assert_blocks(result)

    def test_apply_patch_blocks_real_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            patch = (
                "*** Begin Patch\n"
                "*** Add File: .env\n"
                "+TOKEN=secret\n"
                "*** End Patch\n"
            )

            result = self.run_guard("pre-tool-use", root, self.apply_patch_payload(root, patch))

            self.assert_blocks(result)

    def test_apply_patch_allows_env_template_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            patch = (
                "*** Begin Patch\n"
                "*** Add File: .env.example\n"
                "+TOKEN=\n"
                "*** End Patch\n"
            )

            result = self.run_guard("pre-tool-use", root, self.apply_patch_payload(root, patch))

            self.assert_allows(result)

    def test_apply_patch_accepts_command_key_used_by_current_host(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            patch = (
                "*** Begin Patch\n"
                "*** Update File: README.md\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )

            result = self.run_guard(
                "pre-tool-use",
                root,
                self.apply_patch_payload(root, patch, input_key="command"),
            )

            self.assert_allows(result)

    def test_git_commit_uses_tool_workdir_for_staged_env_check(self) -> None:
        with (
            tempfile.TemporaryDirectory() as outside_dir,
            tempfile.TemporaryDirectory() as repo_dir,
        ):
            outside = Path(outside_dir)
            repo = Path(repo_dir)
            self.init_repo(repo)
            self.write_and_stage(repo, ".env", "TOKEN=secret\n")

            result = self.run_guard(
                "pre-tool-use",
                outside,
                self.bash_payload(outside, "git commit -m test", workdir=repo),
            )

            self.assert_blocks(result)

    def test_git_commit_honors_git_dash_c(self) -> None:
        with (
            tempfile.TemporaryDirectory() as outside_dir,
            tempfile.TemporaryDirectory() as repo_dir,
        ):
            outside = Path(outside_dir)
            repo = Path(repo_dir)
            self.init_repo(repo)
            self.write_and_stage(repo, ".dev.vars", "TOKEN=secret\n")

            result = self.run_guard(
                "pre-tool-use",
                outside,
                self.bash_payload(outside, f"git -C {repo} commit -m test"),
            )

            self.assert_blocks(result)

    def test_plain_git_commit_outside_repo_without_workdir_is_delegated(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)

            result = self.run_guard(
                "pre-tool-use",
                outside,
                self.bash_payload(outside, "git commit -m test"),
            )

            self.assert_allows(result)

    def test_delegated_git_commit_rejects_hook_bypass_flags(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            commands = [
                "git commit --no-verify -m test",
                "git commit --no-v -m test",
                "git commit --no-verif -m test",
                "git commit -n -m test",
                "git commit -an -m test",
                "git commit -S -n -m test",
                "git commit -u -n -m test",
                "git commit -zn -m test",
                "git -c core.hooksPath=/dev/null commit -m test",
                "git -ccore.hooksPath=/dev/null commit -m test",
                "git --config-env=core.hooksPath=HOOKS_PATH commit -m test",
                (
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath "
                    "GIT_CONFIG_VALUE_0=/dev/null git commit -m test"
                ),
            ]

            for command in commands:
                with self.subTest(command=command):
                    result = self.run_guard(
                        "pre-tool-use",
                        outside,
                        self.bash_payload(outside, command),
                    )
                    self.assert_blocks(result)
                    self.assertIn("hook bypass", result.stdout)

    def test_delegated_git_commit_allows_bypass_like_message_values(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            commands = [
                "git commit -m 'docs: explain core.hooksPath'",
                "git commit -m -an",
                "git commit --message=-an",
                "git commit -- core.hooksPath-notes.md",
            ]

            for command in commands:
                with self.subTest(command=command):
                    result = self.run_guard(
                        "pre-tool-use",
                        outside,
                        self.bash_payload(outside, command),
                    )
                    self.assert_allows(result)

    def test_delegated_git_commit_still_rejects_env_path_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)

            result = self.run_guard(
                "pre-tool-use",
                outside,
                self.bash_payload(outside, "git commit -- .env.local"),
            )

            self.assert_blocks(result)

    def test_git_commit_blocks_staged_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            self.write_and_stage(root, ".env", "TOKEN=secret\n")

            result = self.run_guard("pre-tool-use", root, self.bash_payload(root, "git commit -m test"))

            self.assert_blocks(result)

    def test_pre_commit_blocks_staged_env_file_in_normal_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            self.write_and_stage(root, ".env", "TOKEN=secret\n")

            result = self.run_guard(
                "pre-commit",
                root,
                extra_env={"CODEX_HOME": str(root / "elsewhere")},
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn(".env", result.stderr)

    def test_pre_commit_blocks_staged_envrc_in_normal_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            self.write_and_stage(root, "nested/.envrc", "export DATABASE_URL=secret\n")

            result = self.run_guard(
                "pre-commit",
                root,
                extra_env={"CODEX_HOME": str(root / "elsewhere")},
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("nested/.envrc", result.stderr)

    def test_pre_commit_allows_staged_env_template_in_normal_repo(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            self.write_and_stage(root, ".env.example", "TOKEN=\n")

            result = self.run_guard(
                "pre-commit",
                root,
                extra_env={"CODEX_HOME": str(root / "elsewhere")},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_codex_repo_allows_only_managed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            allowed_paths = [
                ".gitattributes",
                ".gitignore",
                "AGENTS.md",
                "config.toml",
                "agents/scout_fast.toml",
                "hooks/codex_env_guard.py",
                "user-skills/example/SKILL.md",
                "rules/default.rules",
                "README.md",
                "bootstrap.sh",
                "git-hooks/pre-commit",
                "tests/bootstrap_test.sh",
            ]
            for path in allowed_paths:
                self.write_and_stage(root, path)

            result = self.run_guard("pre-commit", root, extra_env=self.codex_env(root))

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_codex_repo_rejects_forced_runtime_and_secret_paths(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            rejected_paths = [
                "auth.json",
                "sessions/2026/session.jsonl",
                "history.jsonl",
                "state_5.sqlite",
                "state_5.sqlite-wal",
                "cache/artifact.bin",
                "plugins/cache/plugin.json",
            ]
            for path in rejected_paths:
                self.write_and_stage(root, path)

            result = self.run_guard("pre-commit", root, extra_env=self.codex_env(root))

            self.assertEqual(result.returncode, 1)
            for path in rejected_paths:
                with self.subTest(path=path):
                    self.assertIn(path, result.stderr)

    def test_codex_repo_uses_home_dot_codex_when_codex_home_is_unset(self) -> None:
        with tempfile.TemporaryDirectory() as home_dir:
            home = Path(home_dir)
            root = home / ".codex"
            root.mkdir()
            self.init_repo(root)
            self.write_and_stage(root, "auth.json")

            result = self.run_guard(
                "pre-commit",
                root,
                extra_env={"CODEX_HOME": None, "HOME": str(home)},
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("auth.json", result.stderr)

    def test_codex_repo_rejects_known_credential_in_allowed_file(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            fake_github_token = "ghp_" + ("a" * 36)
            self.write_and_stage(root, "README.md", f"token={fake_github_token}\n")

            result = self.run_guard("pre-commit", root, extra_env=self.codex_env(root))

            self.assertEqual(result.returncode, 1)
            self.assertIn("README.md", result.stderr)
            self.assertIn("GitHub token", result.stderr)
            self.assertNotIn(fake_github_token, result.stderr)

    def test_normal_repo_does_not_enable_codex_credential_scan(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            fake_github_token = "ghp_" + ("a" * 36)
            self.write_and_stage(root, "README.md", f"token={fake_github_token}\n")

            result = self.run_guard(
                "pre-commit",
                root,
                extra_env={"CODEX_HOME": str(root / "elsewhere")},
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_global_hook_style_wrapper_keeps_local_pre_commit(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            self.write_and_stage(root, "README.md", "# test\n")
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            local_hook = root / ".git" / "hooks" / "pre-commit"
            local_hook.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "printf '%s\\n' ran > local-hook-ran\n",
                encoding="utf-8",
            )
            local_hook.chmod(0o755)

            hooks_dir = root / "global-hooks"
            hooks_dir.mkdir()
            wrapper = hooks_dir / "pre-commit"
            wrapper.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                f'"{Path(sys.executable).as_posix()}" "{SCRIPT.as_posix()}" pre-commit\n'
                'repo_root="$(git rev-parse --show-toplevel)"\n'
                'git_common_dir="$(git rev-parse --git-common-dir)"\n'
                'case "$git_common_dir" in\n'
                '  /*) local_hook="$git_common_dir/hooks/pre-commit" ;;\n'
                '  *) local_hook="$repo_root/$git_common_dir/hooks/pre-commit" ;;\n'
                "esac\n"
                'cd "$repo_root"\n'
                'if [ -x "$local_hook" ]; then\n'
                '  "$local_hook"\n'
                "fi\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            (root / "README.md").write_text("# test\n\nchange\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "README.md"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            result = subprocess.run(
                ["git", "-c", f"core.hooksPath={hooks_dir}", "commit", "-m", "change"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "local-hook-ran").exists())


if __name__ == "__main__":
    unittest.main()
