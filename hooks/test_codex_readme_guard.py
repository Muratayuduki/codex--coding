from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex_readme_guard.py")
MEMO_SCRIPT = Path(__file__).with_name("codex_memo_guard.py")
GLOBAL_PRE_COMMIT = Path(__file__).resolve().parents[1] / "git-hooks" / "pre-commit"


class ReadmeGuardTest(unittest.TestCase):
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
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=root,
            check=True,
        )
        (root / "README.md").write_text("# Test\n", encoding="utf-8")
        (root / "app.txt").write_text("initial\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def payload(
        self,
        cwd: Path,
        event: str,
        **extra: object,
    ) -> dict[str, object]:
        return {
            "cwd": str(cwd),
            "session_id": "session-1",
            "turn_id": "turn-1",
            "hook_event_name": event,
            "model": "gpt-5.6-sol",
            **extra,
        }

    def run_guard(
        self,
        mode: str,
        cwd: Path,
        state_dir: Path,
        payload: dict[str, object],
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CODEX_README_GUARD_STATE_DIR"] = str(state_dir)
        env["README_HOOK_MAX_CHANGED_LINES"] = "50"
        return subprocess.run(
            [sys.executable, str(SCRIPT), mode],
            input=json.dumps(payload),
            text=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def start_turn(self, root: Path, state_dir: Path) -> None:
        result = self.run_guard(
            "start",
            root,
            state_dir,
            self.payload(root, "UserPromptSubmit"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def stop_turn(
        self,
        root: Path,
        state_dir: Path,
        **extra: object,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_guard(
            "stop",
            root,
            state_dir,
            self.payload(root, "Stop", **extra),
        )

    def run_memo_stop(
        self,
        root: Path,
        state_dir: Path,
        **extra: object,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CODEX_README_GUARD_STATE_DIR"] = str(state_dir)
        env["README_HOOK_MAX_CHANGED_LINES"] = "50"
        return subprocess.run(
            [sys.executable, str(MEMO_SCRIPT), "stop"],
            input=json.dumps(self.payload(root, "Stop", **extra)),
            text=True,
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def assert_readme_blocked(
        self,
        result: subprocess.CompletedProcess[str],
    ) -> dict[str, object]:
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("README", output["reason"])
        self.assertNotIn("continue", output)
        return output

    def test_allows_small_main_turn_change(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text("initial\nsmall\n", encoding="utf-8")

            result = self.stop_turn(root, Path(state_dir))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_blocks_large_main_turn_change_without_readme_update(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(root, Path(state_dir))

            self.assert_readme_blocked(result)

    def test_allows_large_change_with_readme_update(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )
            (root / "README.md").write_text("# Test\n\nUpdated.\n", encoding="utf-8")

            result = self.stop_turn(root, Path(state_dir))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_allows_large_change_with_explicit_no_update_decision(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state = Path(state_dir)
            self.init_repo(root)
            self.start_turn(root, state)
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                state,
                last_assistant_message=(
                    "実装と検証が完了しました。\n\n"
                    "README更新判断: 不要\n"
                    "理由: 変更はテスト内部の整理だけで、利用方法と外部動作は変わりません。"
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(list(state.rglob("state.json")), [])

    def test_blocks_no_update_decision_without_reason(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message="README更新判断: 不要",
            )

            self.assert_readme_blocked(result)

    def test_blocks_no_update_decision_with_vague_reason(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "README更新判断: 不要\n"
                    "理由: 変更なし"
                ),
            )

            self.assert_readme_blocked(result)

    def test_blocks_no_update_decision_with_placeholder_reason(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "README更新判断: 不要\n"
                    "理由: <READMEを更新しない具体的な理由>"
                ),
            )

            self.assert_readme_blocked(result)

    def test_blocks_no_update_decision_before_following_text(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "README更新判断: 不要\n"
                    "理由: 変更はテスト内部の整理だけで、利用方法と外部動作は変わりません。\n\n"
                    "ただし、READMEは後から更新します。"
                ),
            )

            self.assert_readme_blocked(result)

    def test_blocks_no_update_decision_inside_unclosed_code_fence(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "宣言例です。\n\n```text\n"
                    "README更新判断: 不要\n"
                    "理由: 変更はテスト内部の整理だけで、利用方法と外部動作は変わりません。"
                ),
            )

            self.assert_readme_blocked(result)

    def test_blocks_no_update_decision_after_shorter_inner_fence(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "````markdown\n```\n"
                    "README更新判断: 不要\n"
                    "理由: 変更はテスト内部の整理だけで、利用方法と外部動作は変わりません。"
                ),
            )

            self.assert_readme_blocked(result)

    def test_blocks_no_update_decision_with_generic_reason(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "README更新判断: 不要\n"
                    "理由: README更新は必要ありません。"
                ),
            )

            self.assert_readme_blocked(result)

    def test_blocks_generic_reason_with_punctuation_variation(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(
                root,
                Path(state_dir),
                last_assistant_message=(
                    "README更新判断: 不要\n"
                    "理由: README更新は、必要ありません。"
                ),
            )

            self.assert_readme_blocked(result)

    def test_detects_changes_committed_during_turn(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "app.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "commit", "-m", "change"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            result = self.stop_turn(root, Path(state_dir))

            self.assert_readme_blocked(result)

    def test_ignores_paths_dirty_before_turn(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )
            self.start_turn(root, Path(state_dir))

            result = self.stop_turn(root, Path(state_dir))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_excludes_actual_env_files_from_snapshots_and_keeps_templates(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state_dir_path = Path(state_dir)
            self.init_repo(root)
            (root / ".env").write_text("production-value\n", encoding="utf-8")
            (root / ".dev.vars.local").write_text(
                "development-value\n",
                encoding="utf-8",
            )
            (root / ".env.example").write_text("EXAMPLE=value\n", encoding="utf-8")
            (root / ".env.example.local").write_text(
                "NOT_A_TEMPLATE=secret\n",
                encoding="utf-8",
            )
            (root / ".dev.vars.template").write_text(
                "TEMPLATE=value\n",
                encoding="utf-8",
            )

            self.start_turn(root, state_dir_path)

            state_file = next(state_dir_path.glob("*/state.json"))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            snapshots = state["initial_dirty_files"]
            self.assertNotIn(".env", snapshots)
            self.assertNotIn(".env.example.local", snapshots)
            self.assertNotIn(".dev.vars.local", snapshots)
            self.assertIn(".env.example", snapshots)
            self.assertIn(".dev.vars.template", snapshots)
            self.assertEqual(
                state["excluded_dirty_paths"],
                [".dev.vars.local", ".env", ".env.example.local"],
            )
            snapshot_bytes = b"".join(
                path.read_bytes() for path in state_file.parent.joinpath("snapshots").iterdir()
            )
            self.assertNotIn(b"production-value", snapshot_bytes)
            self.assertNotIn(b"development-value", snapshot_bytes)
            self.assertNotIn(b"NOT_A_TEMPLATE=secret", snapshot_bytes)
            self.assertIn(b"EXAMPLE=value", snapshot_bytes)
            self.assertIn(b"TEMPLATE=value", snapshot_bytes)

            result = self.stop_turn(root, state_dir_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not enforced on Windows")
    def test_uses_private_modes_for_state_and_snapshot_files(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state_dir_path = Path(state_dir)
            self.init_repo(root)
            state_dir_path.chmod(0o755)
            (root / ".env.example").write_text("EXAMPLE=value\n", encoding="utf-8")

            self.start_turn(root, state_dir_path)

            turn_dir = next(state_dir_path.iterdir())
            state_file = turn_dir / "state.json"
            snapshot_dir = turn_dir / "snapshots"
            snapshot_file = next(snapshot_dir.iterdir())
            self.assertEqual(state_dir_path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(turn_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(snapshot_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(snapshot_file.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "symlink creation requires optional Windows privileges")
    def test_fails_fast_for_symlinked_state_directory(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
            tempfile.TemporaryDirectory() as target_dir,
        ):
            root = Path(root_dir)
            state_dir_path = Path(state_dir) / "state-link"
            state_dir_path.symlink_to(target_dir, target_is_directory=True)
            self.init_repo(root)

            result = self.run_guard(
                "start",
                root,
                state_dir_path,
                self.payload(root, "UserPromptSubmit"),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("state directory must not be a symlink", result.stderr)

    def test_cleans_expired_turn_state_at_start(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state_dir_path = Path(state_dir)
            self.init_repo(root)
            self.start_turn(root, state_dir_path)
            expired_turn_dir = next(state_dir_path.iterdir())
            os.utime(expired_turn_dir, (0, 0))

            result = self.run_guard(
                "start",
                root,
                state_dir_path,
                self.payload(root, "UserPromptSubmit", session_id="session-2"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(expired_turn_dir.exists())

    def test_counts_new_changes_to_path_that_was_dirty_before_turn(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            (root / "app.txt").write_text("initial\nuser change\n", encoding="utf-8")
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"agent line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(root, Path(state_dir))

            self.assert_readme_blocked(result)

    def test_dirty_readme_updated_during_turn_satisfies_guard(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            (root / "README.md").write_text("# Test\n\nUser draft.\n", encoding="utf-8")
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"agent line {number}\n" for number in range(60)),
                encoding="utf-8",
            )
            (root / "README.md").write_text(
                "# Test\n\nUser draft.\n\nAgent update.\n",
                encoding="utf-8",
            )

            result = self.stop_turn(root, Path(state_dir))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    @unittest.skipIf(os.name == "nt", "Windows filenames cannot contain newlines")
    def test_handles_untracked_filename_containing_newline(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "odd\nname.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(root, Path(state_dir))

            self.assert_readme_blocked(result)

    @unittest.skipIf(os.name == "nt", "Windows filenames cannot contain newlines")
    def test_handles_tracked_filename_containing_newline(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            unusual_path = root / "tracked\nname.txt"
            unusual_path.write_text("initial\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked\nname.txt"], cwd=root, check=True)
            subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "commit", "-m", "odd path"],
                cwd=root,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.start_turn(root, Path(state_dir))
            unusual_path.write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(root, Path(state_dir))

            self.assert_readme_blocked(result)

    def test_plan_mode_does_not_create_turn_state(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state = Path(state_dir)
            self.init_repo(root)

            result = self.run_guard(
                "start",
                root,
                state,
                self.payload(root, "UserPromptSubmit", permission_mode="plan"),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(state.rglob("*")), [])

    def test_skips_subagent_stop(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            self.init_repo(root)
            self.start_turn(root, Path(state_dir))
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            result = self.stop_turn(root, Path(state_dir), agent_id="subagent-1")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_skips_non_git_working_directory(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)

            self.start_turn(root, Path(state_dir))
            result = self.stop_turn(root, Path(state_dir))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_global_pre_commit_does_not_require_readme_for_intermediate_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "app.txt"], cwd=root, check=True)
            env = os.environ.copy()
            env["CODEX_THREAD_ID"] = "test-thread"

            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"core.hooksPath={GLOBAL_PRE_COMMIT.parent}",
                    "commit",
                    "-m",
                    "intermediate",
                ],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_global_pre_commit_delegates_only_non_readme_local_hook(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            self.init_repo(root)
            local_hook = root / ".git" / "hooks" / "pre-commit"
            marker = root / "local-hook-ran"
            env = os.environ.copy()
            env["CODEX_THREAD_ID"] = "test-thread"

            local_hook.write_text(
                "#!/bin/sh\nprintf '%s\\n' ran > local-hook-ran\n",
                encoding="utf-8",
            )
            local_hook.chmod(0o755)
            (root / "app.txt").write_text("local hook\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.txt"], cwd=root, check=True)

            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"core.hooksPath={GLOBAL_PRE_COMMIT.parent}",
                    "commit",
                    "-m",
                    "run local hook",
                ],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker.exists())
            marker.unlink()

            local_hook.write_text(
                "#!/bin/sh\nprintf '%s\\n' ran > local-hook-ran\n"
                "# readme-freshness-hook",
                encoding="utf-8",
            )
            local_hook.chmod(0o755)
            (root / "app.txt").write_text("readme hook\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.txt"], cwd=root, check=True)

            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"core.hooksPath={GLOBAL_PRE_COMMIT.parent}",
                    "commit",
                    "-m",
                    "skip readme hook",
                ],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())

    def test_memo_stop_waits_until_readme_check_passes(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state = Path(state_dir)
            self.init_repo(root)
            self.start_turn(root, state)
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            blocked = self.run_memo_stop(root, state)

            self.assert_readme_blocked(blocked)

            (root / "README.md").write_text("# Test\n\nUpdated.\n", encoding="utf-8")
            allowed = self.run_memo_stop(root, state)

            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, "")

    def test_memo_stop_accepts_explicit_no_update_decision(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_dir,
            tempfile.TemporaryDirectory() as state_dir,
        ):
            root = Path(root_dir)
            state = Path(state_dir)
            self.init_repo(root)
            self.start_turn(root, state)
            (root / "app.txt").write_text(
                "".join(f"line {number}\n" for number in range(60)),
                encoding="utf-8",
            )

            allowed = self.run_memo_stop(
                root,
                state,
                last_assistant_message=(
                    "README更新判断: 不要\n"
                    "理由: 内部テストだけを整理し、利用者向けの操作と出力は変えていません。"
                ),
            )

            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, "")


if __name__ == "__main__":
    unittest.main()
