from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


SCRIPT = Path(__file__).with_name("codex_memo_guard.py")

sys.path.insert(0, str(SCRIPT.parent))
import codex_memo_guard as guard  # noqa: E402


def valid_body() -> str:
    return (
        "- 要件・完了条件: ユーザー要求と完了条件を具体的に記録しました\n"
        "- 調査ログ: 読んだファイルと実行したコマンドを具体的に記録しました\n"
        "- 候補と選定理由: 比較した案と採用理由と不採用理由を具体的に記録しました\n"
        "- 判断までの経緯: 調査事実から判断に至った流れを具体的に記録しました\n"
        "- 試行錯誤: 試したことと失敗した内容と修正内容を具体的に記録しました\n"
        "- 実装・変更内容: 編集ファイルと変更内容と影響範囲を具体的に記録しました\n"
        "- 確認結果: 実行した確認コマンドと結果を具体的に記録しました\n"
        "- 残課題・次回引き継ぎ: 未対応事項と次に見る場所を具体的に記録しました\n"
    )


def summary_body() -> str:
    return (
        "- 要件・完了条件: ユーザー要求と完了条件を具体的に記録しました\n"
        "- 調査ログ: 読んだファイルと実行したコマンドを具体的に記録しました\n"
        "- 判断までの経緯: 調査事実から判断に至った流れを具体的に記録しました\n"
        "- 実装・変更内容: 編集ファイルと変更内容と影響範囲を具体的に記録しました\n"
        "- 確認結果: 実行した確認コマンドと結果を具体的に記録しました\n"
        "- 残課題・次回引き継ぎ: 未対応事項と次に見る場所を具体的に記録しました\n"
    )


def block(marker: str, session_id: str = "session-1", turn_id: str = "turn-1") -> str:
    return (
        f"<!-- codex-memo:{marker} session={session_id} turn={turn_id} -->\n"
        f"{valid_body()}"
        f"<!-- /codex-memo:{marker} -->\n"
    )


def exec_command_record(cmd: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": cmd}),
        },
    }


def apply_patch_record(path: str = "a.txt") -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "apply_patch",
            "input": f"*** Begin Patch\n*** Update File: {path}\n+x\n*** End Patch",
        },
    }


def patch_apply_end_record(path: str, stdout: str) -> dict[str, object]:
    return {
        "type": "event_msg",
        "payload": {
            "type": "patch_apply_end",
            "success": True,
            "paths": [path],
            "stdout": stdout,
        },
    }


def tool_output_record(output: str) -> dict[str, object]:
    return {
        "type": "response_item",
        "payload": {"type": "function_call_output", "output": output},
    }


def turn_records(
    turn_id: str = "turn-1",
    user_message: str = "作業をお願いします",
    tool_records: tuple[dict[str, object], ...] = (),
    terminal: str | None = "task_complete",
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}},
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": user_message},
        },
        *tool_records,
        {
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "完了しました"},
        },
    ]
    if terminal is not None:
        records.append(
            {"type": "event_msg", "payload": {"type": terminal, "turn_id": turn_id}}
        )
    return records


class MemoGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._root_dir = tempfile.TemporaryDirectory()
        self._home_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._root_dir.name)
        self.home = Path(self._home_dir.name)
        self.addCleanup(self._root_dir.cleanup)
        self.addCleanup(self._home_dir.cleanup)
        self.addCleanup(self.wait_for_background_summarizer)

    def wait_for_background_summarizer(self) -> None:
        jobs = self.home / ".cache" / "codex-memo-guard" / "jobs"
        deadline = time.monotonic() + 6
        while jobs.is_dir() and any(jobs.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.05)
        if os.name == "nt":
            time.sleep(0.1)

    def write_session_file(
        self, records: list[dict[str, object]], session_id: str = "session-1"
    ) -> Path:
        path = (
            self.home
            / ".codex"
            / "sessions"
            / "2026"
            / "07"
            / "08"
            / f"rollout-2026-07-08T00-00-00-{session_id}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
            encoding="utf-8",
        )
        return path

    def write_stub_codex(
        self,
        body: str = "",
        sleep_seconds: int = 0,
        sleep_after_write_seconds: int = 0,
    ) -> Path:
        body_path = self.home / "stub-body.md"
        body_path.write_text(body or summary_body(), encoding="utf-8")
        if os.name == "nt":
            stub_path = self.home / "stub-codex.py"
            stub_path.write_text(
                "import shutil, sys, time\n"
                "args = sys.argv[1:]\n"
                "out = args[args.index('-o') + 1]\n"
                "sys.stdin.read()\n"
                f"time.sleep({sleep_seconds})\n"
                f"shutil.copyfile({str(body_path)!r}, out)\n"
                f"time.sleep({sleep_after_write_seconds})\n",
                encoding="utf-8",
            )
        else:
            stub_path = self.home / "stub-codex"
            stub_path.write_text(
                "#!/bin/sh\n"
                'out=""\n'
                'prev=""\n'
                'for arg in "$@"; do\n'
                '  if [ "$prev" = "-o" ]; then out="$arg"; fi\n'
                '  prev="$arg"\n'
                "done\n"
                "cat > /dev/null\n"
                f"sleep {sleep_seconds}\n"
                f'cp "{body_path}" "$out"\n'
                f"sleep {sleep_after_write_seconds}\n",
                encoding="utf-8",
            )
        stub_path.chmod(stub_path.stat().st_mode | stat.S_IXUSR)
        return stub_path

    def run_guard(
        self,
        mode: str,
        payload: dict[str, object] | None = None,
        extra_args: tuple[str, ...] = (),
        stub_codex: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if payload is None:
            payload = {
                "cwd": str(self.root),
                "session_id": "session-1",
                "turn_id": "turn-1",
            }
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        if stub_codex is not None:
            env["CODEX_MEMO_GUARD_CODEX"] = str(stub_codex)
        return subprocess.run(
            [sys.executable, str(SCRIPT), mode, *extra_args],
            input=json.dumps(payload),
            text=True,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def state_file(self) -> Path:
        return self.home / ".cache" / "codex-memo-guard" / "session-1-turn-1.json"

    def read_state(self) -> dict[str, object]:
        return json.loads(self.state_file().read_text(encoding="utf-8"))

    def job_file(self) -> Path:
        return (
            self.home
            / ".cache"
            / "codex-memo-guard"
            / "jobs"
            / "session-1-turn-1.json"
        )

    def output_file(self) -> Path:
        return self.job_file().with_suffix(".out.md")

    def log_file(self) -> Path:
        return (
            self.home
            / ".cache"
            / "codex-memo-guard"
            / "logs"
            / "session-1-turn-1.log"
        )

    def write_job_file(self, memo_path: Path) -> Path:
        path = self.job_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "session_id": "session-1",
                    "turn_id": "turn-1",
                    "cwd": str(self.root),
                    "memo_path": str(memo_path),
                    "change_reason": "apply_patch",
                    "excerpt": "[user]\nテスト用の抜粋です",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_missing_memo_skips_without_state(self) -> None:
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))

        stop = self.run_guard("stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertFalse(self.state_file().exists())
        self.assertFalse(self.job_file().exists())

    def test_plan_mode_and_subagent_payloads_skip(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))

        plan_payload = {
            "cwd": str(self.root),
            "session_id": "session-1",
            "turn_id": "turn-1",
            "plan_mode": True,
        }
        plan = self.run_guard("stop", plan_payload)
        self.assertEqual(plan.returncode, 0, plan.stderr)

        subagent_payload = {
            "cwd": str(self.root),
            "session_id": "session-1",
            "turn_id": "turn-1",
            "agent_id": "agent-1",
        }
        subagent = self.run_guard("stop", subagent_payload)
        self.assertEqual(subagent.returncode, 0, subagent.stderr)
        self.assertFalse(self.state_file().exists())
        self.assertFalse(self.job_file().exists())

    def test_read_only_turn_is_skipped(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(
            turn_records(tool_records=(exec_command_record("rg -n foo src"),))
        )

        stop = self.run_guard("stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(self.read_state()["summary_status"], "skipped:read-only-turn")
        self.assertFalse(self.job_file().exists())

    def test_nomemo_tag_skips_change_turn(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(
            turn_records(
                user_message="#nomemo 一時的な作業です",
                tool_records=(apply_patch_record(),),
            )
        )

        stop = self.run_guard("stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(self.read_state()["summary_status"], "skipped:nomemo")
        self.assertFalse(self.job_file().exists())

    def test_change_turn_spawns_summarizer(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))
        stub = self.write_stub_codex(sleep_seconds=3)

        stop = self.run_guard("stop", stub_codex=stub)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(self.read_state()["summary_status"], "spawned")
        job = json.loads(self.job_file().read_text(encoding="utf-8"))
        self.assertEqual(job["memo_path"], str(self.root / "memo.md"))
        self.assertEqual(job["change_reason"], "custom_tool_call:apply_patch")
        self.assertIn("作業をお願いします", job["excerpt"])

    def test_memo_tag_forces_read_only_turn(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(
            turn_records(
                user_message="#memo この質問も記録して",
                tool_records=(exec_command_record("cat README.md"),),
            )
        )
        stub = self.write_stub_codex(sleep_seconds=3)

        stop = self.run_guard("stop", stub_codex=stub)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(self.read_state()["summary_status"], "spawned")
        job = json.loads(self.job_file().read_text(encoding="utf-8"))
        self.assertEqual(job["change_reason"], "forced-by-#memo")

    def test_second_stop_call_does_not_respawn(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))
        stub = self.write_stub_codex(sleep_seconds=3)

        first = self.run_guard("stop", stub_codex=stub)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.job_file().unlink()

        second = self.run_guard("stop", stub_codex=stub)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse(self.job_file().exists())

    def test_docs_memo_is_used_before_root_memo(self) -> None:
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "memo.md").write_text("# docs memo\n", encoding="utf-8")
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))
        stub = self.write_stub_codex(sleep_seconds=3)

        stop = self.run_guard("stop", stub_codex=stub)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        job = json.loads(self.job_file().read_text(encoding="utf-8"))
        self.assertEqual(job["memo_path"], str(docs / "memo.md"))

    def test_summarize_appends_done_block(self) -> None:
        memo = self.root / "memo.md"
        memo.write_text("# 作業メモ\n", encoding="utf-8")
        job = self.write_job_file(memo)
        stub = self.write_stub_codex()

        summarize = self.run_guard(
            "summarize", extra_args=(str(job),), stub_codex=stub
        )
        self.assertEqual(summarize.returncode, 0, summarize.stderr)

        text = memo.read_text(encoding="utf-8")
        self.assertIn("<!-- codex-memo:done session=session-1 turn=turn-1 -->", text)
        self.assertIn("<!-- /codex-memo:done -->", text)
        self.assertIn("- 要件・完了条件:", text)
        self.assertEqual(self.read_state()["summary_status"], "done")
        self.assertFalse(job.exists())
        self.assertFalse(self.output_file().exists())
        if os.name != "nt":
            self.assertEqual(
                stat.S_IMODE((self.home / ".cache" / "codex-memo-guard").stat().st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(
                    (self.home / ".cache" / "codex-memo-guard" / "locks").stat().st_mode
                ),
                0o700,
            )
            self.assertEqual(stat.S_IMODE(self.state_file().stat().st_mode), 0o600)

        pre_commit = self.run_guard("pre-commit")
        self.assertEqual(pre_commit.returncode, 0, pre_commit.stderr)

    def test_summarize_is_idempotent_for_existing_done_block(self) -> None:
        memo = self.root / "memo.md"
        memo.write_text(f"# 作業メモ\n\n{block('done')}", encoding="utf-8")
        job = self.write_job_file(memo)
        stub = self.write_stub_codex()

        summarize = self.run_guard(
            "summarize", extra_args=(str(job),), stub_codex=stub
        )
        self.assertEqual(summarize.returncode, 0, summarize.stderr)
        text = memo.read_text(encoding="utf-8")
        self.assertEqual(text.count("codex-memo:done session=session-1"), 1)

    def test_summarize_fails_on_invalid_summary(self) -> None:
        memo = self.root / "memo.md"
        memo.write_text("# 作業メモ\n", encoding="utf-8")
        job = self.write_job_file(memo)
        stub = self.write_stub_codex(body="- 要件・完了条件: TODO\n")

        summarize = self.run_guard(
            "summarize", extra_args=(str(job),), stub_codex=stub
        )
        self.assertNotEqual(summarize.returncode, 0)
        self.assertIn("missing required fields", summarize.stderr)
        self.assertEqual(memo.read_text(encoding="utf-8"), "# 作業メモ\n")
        self.assertTrue(
            str(self.read_state()["summary_status"]).startswith("failed:")
        )
        self.assertFalse(job.exists())
        self.assertFalse(self.output_file().exists())

    def test_spawned_artifacts_are_private(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))
        stub = self.write_stub_codex(sleep_seconds=3)

        stop = self.run_guard("stop", stub_codex=stub)

        self.assertEqual(stop.returncode, 0, stop.stderr)
        state_root = self.home / ".cache" / "codex-memo-guard"
        for directory in (state_root, state_root / "jobs", state_root / "logs"):
            self.assertTrue(directory.is_dir())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for artifact in (self.state_file(), self.job_file(), self.log_file()):
            self.assertTrue(artifact.is_file())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(artifact.stat().st_mode), 0o600)

    def test_summary_output_is_private_before_cleanup(self) -> None:
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(turn_records(tool_records=(apply_patch_record(),)))
        stub = self.write_stub_codex(sleep_after_write_seconds=3)

        stop = self.run_guard("stop", stub_codex=stub)

        self.assertEqual(stop.returncode, 0, stop.stderr)
        deadline = time.monotonic() + 5
        while not self.output_file().exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.output_file().exists())
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.output_file().stat().st_mode), 0o600)

    def test_failed_background_summary_redacts_logs_and_cleans_temp_files(self) -> None:
        secret = "sk-proj-" + "0123456789abcdef"
        (self.root / "memo.md").write_text("# 作業メモ\n", encoding="utf-8")
        self.write_session_file(
            turn_records(
                user_message=f"OPENAI_API_KEY={secret} の設定を確認して",
                tool_records=(apply_patch_record(),),
            )
        )
        stub = self.write_stub_codex(body="- 要件・完了条件: TODO\n")

        stop = self.run_guard("stop", stub_codex=stub)

        self.assertEqual(stop.returncode, 0, stop.stderr)
        deadline = time.monotonic() + 5
        while self.job_file().exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.job_file().exists())
        self.assertFalse(self.output_file().exists())
        self.assertTrue(
            str(self.read_state()["summary_status"]).startswith("failed:")
        )
        self.assertNotIn(secret, self.state_file().read_text(encoding="utf-8"))
        self.assertNotIn(secret, self.log_file().read_text(encoding="utf-8"))

    def test_malformed_pending_marker_blocks_pre_commit(self) -> None:
        (self.root / "memo.md").write_text(
            "<!-- codex-memo:pending session=session-1 turn=turn-1 -->\n",
            encoding="utf-8",
        )

        pre_commit = self.run_guard("pre-commit")
        self.assertEqual(pre_commit.returncode, 1)
        self.assertIn("終了マーカー", pre_commit.stderr)

    def test_invalid_done_marker_blocks_pre_commit(self) -> None:
        (self.root / "memo.md").write_text(
            "<!-- codex-memo:done session=session-1 turn=turn-1 -->\n"
            "- 要件・完了条件: TODO\n"
            "<!-- /codex-memo:done -->\n",
            encoding="utf-8",
        )

        pre_commit = self.run_guard("pre-commit")
        self.assertEqual(pre_commit.returncode, 1)
        self.assertIn("詳細が不足", pre_commit.stderr)

    def test_malformed_done_marker_blocks_pre_commit(self) -> None:
        (self.root / "memo.md").write_text(
            "<!-- codex-memo:done session=session-1 turn=turn-1 -->\n",
            encoding="utf-8",
        )

        pre_commit = self.run_guard("pre-commit")
        self.assertEqual(pre_commit.returncode, 1)
        self.assertIn("終了マーカー", pre_commit.stderr)

    def test_unclosed_done_before_valid_done_blocks_pre_commit(self) -> None:
        (self.root / "memo.md").write_text(
            "<!-- codex-memo:done session=old turn=old -->\n"
            "- 要件・完了条件: 古い未完了メモです\n"
            f"{block('done')}",
            encoding="utf-8",
        )

        pre_commit = self.run_guard("pre-commit")
        self.assertEqual(pre_commit.returncode, 1)
        self.assertIn("終了マーカー", pre_commit.stderr)


class TurnSliceTest(unittest.TestCase):
    def write_session(self, records: list[dict[str, object]]) -> Path:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        path = Path(self._dir.name) / "session.jsonl"
        path.write_text(
            "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
            encoding="utf-8",
        )
        return path

    def test_slice_stops_at_task_complete(self) -> None:
        records = [
            *turn_records(turn_id="turn-0"),
            *turn_records(turn_id="turn-1"),
            *turn_records(turn_id="turn-2"),
        ]
        path = self.write_session(records)

        slice_records = guard.extract_turn_slice(path, "turn-1")
        self.assertEqual(
            slice_records[0]["payload"],
            {"type": "task_started", "turn_id": "turn-1"},
        )
        self.assertEqual(
            slice_records[-1]["payload"],
            {"type": "task_complete", "turn_id": "turn-1"},
        )
        self.assertEqual(len(slice_records), len(turn_records(turn_id="turn-1")))

    def test_slice_stops_at_turn_aborted(self) -> None:
        path = self.write_session(turn_records(terminal="turn_aborted"))

        slice_records = guard.extract_turn_slice(path, "turn-1")
        self.assertEqual(slice_records[-1]["payload"]["type"], "turn_aborted")

    def test_slice_falls_back_to_eof_without_terminal(self) -> None:
        path = self.write_session(turn_records(terminal=None))

        slice_records = guard.extract_turn_slice(path, "turn-1")
        self.assertEqual(slice_records[-1]["payload"]["type"], "agent_message")

    def test_missing_turn_raises(self) -> None:
        path = self.write_session(turn_records(turn_id="turn-0"))

        with self.assertRaises(ValueError):
            guard.extract_turn_slice(path, "turn-9")


class CommandClassificationTest(unittest.TestCase):
    def test_read_only_commands(self) -> None:
        read_only = [
            "rg -n 'foo' src",
            "cat a.txt | head -20",
            "git status && git diff --stat",
            "git -C /tmp log --oneline",
            "sed -n '1,50p' file.py",
            "find . -name '*.py'",
            "FOO=1 ls -la",
        ]
        for cmd in read_only:
            self.assertTrue(guard.is_read_only_command(cmd), cmd)

    def test_change_commands(self) -> None:
        changing = [
            "git checkout main",
            "npm install",
            "echo hi > out.txt",
            "sed -i 's/a/b/' file.py",
            "find . -name '*.pyc' -delete",
            "python3 setup.py",
            "cat in.txt | tee out.txt",
            "rm -rf build",
        ]
        for cmd in changing:
            self.assertFalse(guard.is_read_only_command(cmd), cmd)


class TranscriptExcerptTest(unittest.TestCase):
    def test_excerpt_respects_budget_and_keeps_user_message(self) -> None:
        big_output = {
            "type": "response_item",
            "payload": {"type": "function_call_output", "output": "x" * 10_000},
        }
        records = turn_records(
            user_message="ユーザーからの重要な指示です",
            tool_records=tuple(
                record
                for _ in range(200)
                for record in (exec_command_record("cat data.txt"), big_output)
            ),
        )

        excerpt = guard.build_transcript_excerpt(records)
        self.assertLessEqual(len(excerpt), guard.MAX_EXCERPT_CHARS)
        self.assertIn("ユーザーからの重要な指示です", excerpt)
        self.assertIn("[assistant final]", excerpt)

    def test_reasoning_records_are_excluded(self) -> None:
        records = turn_records(
            tool_records=(
                {
                    "type": "response_item",
                    "payload": {"type": "reasoning", "content": "内部思考テキスト"},
                },
            )
        )

        excerpt = guard.build_transcript_excerpt(records)
        self.assertNotIn("内部思考テキスト", excerpt)

    def test_excerpt_redacts_credentials_and_omits_tool_and_patch_bodies(self) -> None:
        openai_key = "sk-proj-" + "0123456789abcdef"
        bearer = "Bearer very-secret-bearer-token"
        tool_secret = "ghp_" + "012345678901234567890123456789012345"
        patch_secret = "AKIA" + "0123456789ABCDEF"
        records = turn_records(
            user_message=f"OPENAI_API_KEY={openai_key} を使ってください",
            tool_records=(
                exec_command_record(
                    f"curl -H 'Authorization: {bearer}' --api-key {openai_key}"
                ),
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "external_tool",
                        "arguments": json.dumps({"token": tool_secret}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "apply_patch",
                        "input": (
                            "*** Begin Patch\n"
                            "*** Update File: config.py\n"
                            f"+AWS_ACCESS_KEY_ID={patch_secret}\n"
                            "*** End Patch"
                        ),
                    },
                },
                patch_apply_end_record("config.py", f"applied {patch_secret}"),
                tool_output_record(f"raw tool output {tool_secret}"),
            ),
        )
        records.insert(
            -2,
            {
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": f"Authorization: {bearer}",
                },
            },
        )

        excerpt = guard.build_transcript_excerpt(records)

        for secret in (openai_key, bearer, tool_secret, patch_secret):
            self.assertNotIn(secret, excerpt)
        self.assertIn("OPENAI_API_KEY=[REDACTED]", excerpt)
        self.assertIn("[patch files: config.py]", excerpt)
        self.assertIn("[patch result success=True paths=config.py]", excerpt)
        self.assertIn("[tool output omitted]", excerpt)
        self.assertNotIn("AWS_ACCESS_KEY_ID", excerpt)


class PrivateStatePathTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX modes and symlink privileges differ on Windows")
    def test_private_directory_forces_mode_and_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            private_dir = root / "state"
            private_dir.mkdir(mode=0o755)

            guard.ensure_private_directory(private_dir)

            self.assertEqual(stat.S_IMODE(private_dir.stat().st_mode), 0o700)
            link = root / "state-link"
            link.symlink_to(private_dir, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
                guard.ensure_private_directory(link)

    @unittest.skipIf(os.name == "nt", "POSIX ownership is unavailable on Windows")
    def test_private_directory_rejects_other_owner(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            private_dir = Path(root_dir) / "state"
            private_dir.mkdir()

            with mock.patch.object(guard.os, "getuid", return_value=os.getuid() + 1):
                with self.assertRaisesRegex(PermissionError, "not owned"):
                    guard.ensure_private_directory(private_dir)

    @unittest.skipIf(os.name == "nt", "POSIX modes and symlink privileges differ on Windows")
    def test_private_file_forces_mode_and_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            private_file = root / "state.json"
            private_file.write_text("state", encoding="utf-8")
            private_file.chmod(0o644)

            guard.ensure_private_file(private_file, create=False)

            self.assertEqual(stat.S_IMODE(private_file.stat().st_mode), 0o600)
            link = root / "state-link.json"
            link.symlink_to(private_file)
            with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
                guard.ensure_private_file(link, create=False)


if __name__ == "__main__":
    unittest.main()
