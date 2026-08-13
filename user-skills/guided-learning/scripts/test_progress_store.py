#!/usr/bin/env python3
"""Tests for progress_store.py using only the Python standard library."""

from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest import mock

import progress_store as store


class ProgressStoreCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state_dir = Path(self.temporary.name) / "state"

    def run_cli(
        self,
        *arguments: str,
        input_value: object | None = None,
    ) -> tuple[int, dict[str, object], dict[str, object] | None]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO(
            "" if input_value is None else json.dumps(input_value, ensure_ascii=False)
        )
        argv = ["--state-dir", str(self.state_dir), *arguments]
        with (
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
            mock.patch.object(store.sys, "stdin", stdin),
        ):
            result = store.main(argv)
        stdout_value: dict[str, object] = {}
        if stdout.getvalue():
            stdout_value = json.loads(stdout.getvalue())
        stderr_value = None
        if stderr.getvalue():
            stderr_value = json.loads(stderr.getvalue())
        return result, stdout_value, stderr_value

    def create(
        self, topic: str = "Python", goal: str = "関数を説明できる"
    ) -> dict[str, object]:
        code, output, error = self.run_cli(
            "create",
            "--input",
            "-",
            input_value={
                "topic": topic,
                "goal": goal,
                "learner_profile": {"level": "beginner"},
                "sources": [{"title": "公式資料", "url": "https://example.test"}],
                "nearby_steps": [{"id": "step-1", "title": "導入"}],
                "recommended_route": ["step-1"],
                "current_step_id": "step-1",
                "current_task": {"prompt": "関数とは何ですか"},
                "next_action": "最初の問いに答える",
            },
        )
        self.assertEqual(code, 0, error)
        self.assertIsNone(error)
        return output["track"]  # type: ignore[return-value]

    def read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_json(self, path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.chmod(path, 0o600)

    def leave_pending_update_with_entry(
        self,
        track_id: str,
        entry_name: str,
    ) -> Path:
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        entry_path = track_path.parent / entry_name
        original_atomic_write = store._atomic_write_json
        interrupted = False

        def atomic_write(path: Path, value: dict[str, object]) -> None:
            nonlocal interrupted
            if Path(path) == track_path and not interrupted:
                interrupted = True
                self.write_json(entry_path, value)
                raise RuntimeError("simulated process crash before replace")
            original_atomic_write(path, value)

        with mock.patch.object(store, "_atomic_write_json", side_effect=atomic_write):
            with self.assertRaisesRegex(RuntimeError, "simulated process crash"):
                store.update_track(
                    self.state_dir,
                    track_id,
                    0,
                    {"set": {"next_action": "クラッシュ後に回復する"}},
                )

        self.assertTrue(interrupted)
        self.assertTrue((self.state_dir / ".transaction.json").exists())
        self.assertTrue(entry_path.exists())
        return entry_path

    def test_create_load_and_update_with_history(self) -> None:
        created = self.create()
        track_id = created["id"]
        self.assertEqual(created["revision"], 0)
        self.assertEqual(created["status"], "active")

        code, loaded, error = self.run_cli("load", str(track_id))
        self.assertEqual(code, 0, error)
        self.assertEqual(loaded["track"], created)

        code, updated, error = self.run_cli(
            "update",
            str(track_id),
            "--expected-revision",
            "0",
            "--input",
            "-",
            input_value={
                "set": {
                    "next_action": "反例を説明する",
                    "mastery": {"functions": "practicing"},
                },
                "append_history": [
                    {
                        "task": "関数を説明する",
                        "learner_response": "処理をまとめたもの",
                        "feedback": "入力と出力にも触れると正確です",
                        "attainment": "未確認: 入力と出力の説明が不足している",
                    }
                ],
            },
        )
        self.assertEqual(code, 0, error)
        track = updated["track"]
        self.assertEqual(track["revision"], 1)
        self.assertEqual(track["next_action"], "反例を説明する")
        self.assertEqual(len(track["history"]), 1)
        self.assertIn("recorded_at", track["history"][0])

        code, loaded, error = self.run_cli("load", str(track_id))
        self.assertEqual(code, 0, error)
        self.assertEqual(loaded["track"], track)

    def test_update_can_change_topic_and_goal(self) -> None:
        created = self.create()
        track_id = str(created["id"])

        updated = store.update_track(
            self.state_dir,
            track_id,
            0,
            {
                "set": {
                    "topic": "Python設計",
                    "goal": "関数の設計判断を説明できる",
                }
            },
        )

        self.assertEqual(updated["topic"], "Python設計")
        self.assertEqual(updated["goal"], "関数の設計判断を説明できる")

    def test_multiple_tracks_and_active_track(self) -> None:
        first = self.create("Python", "関数を理解する")
        second = self.create("SQL", "集計できる")
        code, output, error = self.run_cli("list")
        self.assertEqual(code, 0, error)
        self.assertEqual(len(output["tracks"]), 2)
        self.assertEqual(output["active_track_id"], second["id"])
        self.assertEqual(
            {item["id"] for item in output["tracks"]},
            {first["id"], second["id"]},
        )
        self.assertNotIn("archived", output)

    def test_revision_conflict_preserves_current_track(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        code, updated, error = self.run_cli(
            "update",
            track_id,
            "--expected-revision",
            "0",
            "--input",
            "-",
            input_value={"set": {"next_action": "新しい行動"}},
        )
        self.assertEqual(code, 0, error)
        code, output, error = self.run_cli(
            "update",
            track_id,
            "--expected-revision",
            "0",
            "--input",
            "-",
            input_value={"set": {"next_action": "上書きしてはいけない"}},
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "revision_conflict")
        code, loaded, error = self.run_cli("load", track_id)
        self.assertEqual(code, 0, error)
        self.assertEqual(loaded["track"], updated["track"])

    def test_concurrent_updates_with_same_revision_allow_only_one_commit(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        barrier = threading.Barrier(2)

        def update(label: str) -> tuple[str, object]:
            barrier.wait()
            try:
                track = store.update_track(
                    self.state_dir,
                    track_id,
                    0,
                    {"set": {"next_action": label}},
                )
            except store.StoreError as error:
                return "error", error.code
            return "ok", track

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, ("first", "second")))

        successes = [value for status, value in results if status == "ok"]
        errors = [value for status, value in results if status == "error"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(errors, ["revision_conflict"])
        final = store.load_track(self.state_dir, track_id)
        self.assertEqual(final["revision"], 1)
        self.assertIn(final["next_action"], {"first", "second"})

    def test_corrupted_json_is_reported_without_overwrite(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        corrupted = "{not valid JSON\n"
        track_path.write_text(corrupted, encoding="utf-8")
        os.chmod(track_path, 0o600)

        code, output, error = self.run_cli("load", track_id)
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "corrupted_json")
        self.assertEqual(track_path.read_text(encoding="utf-8"), corrupted)

    def test_unknown_schema_is_rejected_without_overwrite(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        value = json.loads(track_path.read_text(encoding="utf-8"))
        value["schema_version"] = 99
        original = json.dumps(value, ensure_ascii=False)
        track_path.write_text(original, encoding="utf-8")
        os.chmod(track_path, 0o600)

        code, output, error = self.run_cli("load", track_id)
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "unknown_schema")
        self.assertEqual(track_path.read_text(encoding="utf-8"), original)

    def test_unknown_index_schema_is_rejected_without_overwrite(self) -> None:
        self.create()
        index_path = self.state_dir / "index.json"
        value = json.loads(index_path.read_text(encoding="utf-8"))
        value["schema_version"] = 99
        original = json.dumps(value, ensure_ascii=False)
        index_path.write_text(original, encoding="utf-8")
        os.chmod(index_path, 0o600)

        code, output, error = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "unknown_schema")
        self.assertEqual(index_path.read_text(encoding="utf-8"), original)

    def test_archive_list_and_restore(self) -> None:
        first = self.create("Python", "関数を理解する")
        second = self.create("SQL", "集計できる")
        first_id = str(first["id"])
        second_id = str(second["id"])

        code, output, error = self.run_cli(
            "archive", second_id, "--expected-revision", "0"
        )
        self.assertEqual(code, 0, error)
        self.assertEqual(output["track"]["status"], "archived")
        self.assertEqual(output["track"]["revision"], 1)
        self.assertEqual(
            self.state_dir.joinpath("archive", f"{second_id}.json").exists(),
            True,
        )
        self.assertFalse(
            self.state_dir.joinpath("tracks", f"{second_id}.json").exists()
        )

        code, listing, error = self.run_cli("list", "--include-archived")
        self.assertEqual(code, 0, error)
        self.assertEqual(listing["active_track_id"], first_id)
        self.assertEqual([item["id"] for item in listing["archived"]], [second_id])

        code, output, error = self.run_cli("restore", second_id)
        self.assertEqual(code, 0, error)
        self.assertEqual(output["track"]["status"], "active")
        self.assertEqual(output["track"]["revision"], 2)
        code, listing, error = self.run_cli("list", "--include-archived")
        self.assertEqual(code, 0, error)
        self.assertEqual(listing["active_track_id"], second_id)
        self.assertEqual(listing["archived"], [])

    def test_export_and_confirmed_delete(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        code, exported, error = self.run_cli("export", track_id)
        self.assertEqual(code, 0, error)
        self.assertEqual(exported["track"], created)

        code, output, error = self.run_cli(
            "delete", track_id, "--confirm", "track-00000000"
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "confirmation_mismatch")
        self.assertTrue(self.state_dir.joinpath("tracks", f"{track_id}.json").exists())

        code, output, error = self.run_cli("delete", track_id, "--confirm", track_id)
        self.assertEqual(code, 0, error)
        self.assertTrue(output["deleted"])
        code, output, error = self.run_cli("load", track_id)
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "track_not_found")

    def test_validate_detects_orphan_and_validates_consistent_store(self) -> None:
        created = self.create()
        code, output, error = self.run_cli("validate")
        self.assertEqual(code, 0, error)
        self.assertTrue(output["valid"])
        self.assertEqual(output["active_tracks"], 1)

        orphan_id = "orphan-12345678"
        source = self.state_dir / "tracks" / f"{created['id']}.json"
        orphan = self.state_dir / "tracks" / f"{orphan_id}.json"
        value = json.loads(source.read_text(encoding="utf-8"))
        value["id"] = orphan_id
        orphan.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(orphan, 0o600)
        code, output, error = self.run_cli("validate")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "inconsistent_state")

    def test_missing_index_with_track_files_is_not_reinitialized(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        original_track = track_path.read_bytes()
        index_path = self.state_dir / "index.json"
        index_path.unlink()

        code, output, error = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "inconsistent_state")
        self.assertFalse(index_path.exists())
        self.assertEqual(track_path.read_bytes(), original_track)

    def test_empty_directories_allow_initial_index(self) -> None:
        code, output, error = self.run_cli("list")
        self.assertEqual(code, 0, error)
        self.assertEqual(output["tracks"], [])
        self.assertTrue((self.state_dir / "index.json").exists())
        self.assertFalse((self.state_dir / ".transaction.json").exists())

    def test_mutations_reject_index_track_metadata_mismatch(self) -> None:
        original_state_dir = self.state_dir
        try:
            for operation in ("update", "archive", "restore", "delete"):
                with (
                    self.subTest(operation=operation),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    self.state_dir = Path(temporary) / "state"
                    created = self.create()
                    track_id = str(created["id"])
                    status = "active"
                    revision = 0
                    if operation == "restore":
                        code, archived, error = self.run_cli(
                            "archive",
                            track_id,
                            "--expected-revision",
                            "0",
                        )
                        self.assertEqual(code, 0, error)
                        status = "archived"
                        revision = int(archived["track"]["revision"])

                    index_path = self.state_dir / "index.json"
                    index = self.read_json(index_path)
                    collection = "tracks" if status == "active" else "archived"
                    index[collection][track_id]["goal"] = "indexだけの不一致"
                    self.write_json(index_path, index)
                    directory = "tracks" if status == "active" else "archive"
                    track_path = self.state_dir / directory / f"{track_id}.json"
                    before_index = index_path.read_bytes()
                    before_track = track_path.read_bytes()

                    if operation == "update":
                        code, output, error = self.run_cli(
                            "update",
                            track_id,
                            "--expected-revision",
                            str(revision),
                            "--input",
                            "-",
                            input_value={"set": {"next_action": "変更禁止"}},
                        )
                    elif operation == "archive":
                        code, output, error = self.run_cli(
                            "archive",
                            track_id,
                            "--expected-revision",
                            str(revision),
                        )
                    elif operation == "restore":
                        code, output, error = self.run_cli("restore", track_id)
                    else:
                        code, output, error = self.run_cli(
                            "delete",
                            track_id,
                            "--confirm",
                            track_id,
                        )

                    self.assertEqual(code, 1)
                    self.assertEqual(output, {})
                    self.assertEqual(error["error"]["code"], "inconsistent_state")
                    self.assertEqual(index_path.read_bytes(), before_index)
                    self.assertEqual(track_path.read_bytes(), before_track)
                    self.assertFalse((self.state_dir / ".transaction.json").exists())
        finally:
            self.state_dir = original_state_dir

    def test_history_fields_must_be_nonempty_strings(self) -> None:
        code, output, error = self.run_cli(
            "create",
            "--input",
            "-",
            input_value={
                "topic": "Python",
                "goal": "関数を説明する",
                "history": [
                    {
                        "task": "説明する",
                        "learner_response": "回答",
                        "feedback": "改善点",
                        "attainment": False,
                    }
                ],
            },
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_schema")
        self.assertFalse(self.state_dir.exists())

        created = self.create()
        track_id = str(created["id"])
        code, output, error = self.run_cli(
            "update",
            track_id,
            "--expected-revision",
            "0",
            "--input",
            "-",
            input_value={
                "append_history": [
                    {
                        "task": "説明する",
                        "learner_response": "回答",
                        "feedback": "   ",
                        "attainment": "進行中",
                    }
                ]
            },
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_schema")
        code, loaded, error = self.run_cli("load", track_id)
        self.assertEqual(code, 0, error)
        self.assertEqual(loaded["track"]["revision"], 0)

    def test_stored_history_is_validated_on_load_and_validate(self) -> None:
        original_state_dir = self.state_dir
        try:
            for command in ("load", "validate"):
                with (
                    self.subTest(command=command),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    self.state_dir = Path(temporary) / "state"
                    created = self.create()
                    track_id = str(created["id"])
                    code, output, error = self.run_cli(
                        "update",
                        track_id,
                        "--expected-revision",
                        "0",
                        "--input",
                        "-",
                        input_value={
                            "append_history": [
                                {
                                    "task": "説明する",
                                    "learner_response": "回答",
                                    "feedback": "改善点",
                                    "attainment": "進行中",
                                }
                            ]
                        },
                    )
                    self.assertEqual(code, 0, error)
                    track_path = self.state_dir / "tracks" / f"{track_id}.json"
                    track = self.read_json(track_path)
                    track["history"][0]["attainment"] = False
                    self.write_json(track_path, track)

                    if command == "load":
                        code, output, error = self.run_cli("load", track_id)
                    else:
                        code, output, error = self.run_cli("validate")
                    self.assertEqual(code, 1)
                    self.assertEqual(output, {})
                    self.assertEqual(error["error"]["code"], "invalid_schema")
        finally:
            self.state_dir = original_state_dir

    def test_invalid_utf8_state_file_is_structured_error(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        path = self.state_dir / "tracks" / f"{track_id}.json"
        path.write_bytes(b"\xff\xfe\x00")
        os.chmod(path, 0o600)

        code, output, error = self.run_cli("load", track_id)
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_encoding")
        self.assertEqual(error["error"]["details"]["path"], str(path))

    def test_state_open_permission_error_is_structured_with_path(self) -> None:
        self.create()
        index_path = self.state_dir / "index.json"
        original_open = store._path_open

        def open_file(path: Path, flags: int, mode: int | None = None) -> int:
            if os.fspath(path) == os.fspath(index_path):
                raise PermissionError(13, "permission denied", os.fspath(path))
            return original_open(path, flags, mode)

        with mock.patch.object(store, "_path_open", side_effect=open_file):
            code, output, error = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_permissions")
        self.assertEqual(error["error"]["details"]["path"], str(index_path))

    def test_public_index_active_archive_and_lock_are_rejected(self) -> None:
        original_state_dir = self.state_dir
        try:
            for target in (
                "index",
                "active",
                "archive",
                "lock",
                "root_directory",
                "tracks_directory",
                "archive_directory",
            ):
                with (
                    self.subTest(target=target),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    self.state_dir = Path(temporary) / "state"
                    created = self.create()
                    track_id = str(created["id"])
                    if target == "archive":
                        code, output, error = self.run_cli(
                            "archive",
                            track_id,
                            "--expected-revision",
                            "0",
                        )
                        self.assertEqual(code, 0, error)
                        path = self.state_dir / "archive" / f"{track_id}.json"
                    elif target == "active":
                        path = self.state_dir / "tracks" / f"{track_id}.json"
                    elif target == "lock":
                        path = self.state_dir / ".lock"
                    elif target == "root_directory":
                        path = self.state_dir
                    elif target == "tracks_directory":
                        path = self.state_dir / "tracks"
                    elif target == "archive_directory":
                        path = self.state_dir / "archive"
                    else:
                        path = self.state_dir / "index.json"
                    os.chmod(path, 0o755 if path.is_dir() else 0o644)

                    code, output, error = self.run_cli("load", track_id)
                    self.assertEqual(code, 1)
                    self.assertEqual(output, {})
                    self.assertEqual(error["error"]["code"], "invalid_permissions")
                    self.assertEqual(error["error"]["details"]["path"], str(path))
        finally:
            self.state_dir = original_state_dir

    def test_concurrent_first_use_creates_secure_consistent_store(self) -> None:
        def initialize(_: int) -> dict[str, object]:
            return store.list_tracks(self.state_dir)

        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(initialize, range(36)))

        self.assertTrue(all(result["tracks"] == [] for result in results))
        validation = store.validate_store(self.state_dir)
        self.assertTrue(validation["valid"])
        self.assertFalse((self.state_dir / ".transaction.json").exists())

    def test_journal_recovers_every_mutation_after_index_write_interruption(
        self,
    ) -> None:
        store.list_tracks(self.state_dir)
        original_atomic_write = store._atomic_write_json

        def interrupt_index_once(action: object) -> None:
            interrupted = False

            def atomic_write(path: Path, value: dict[str, object]) -> None:
                nonlocal interrupted
                if Path(path) == self.state_dir / "index.json" and not interrupted:
                    interrupted = True
                    raise store.StoreError("injected_interruption", "test interruption")
                original_atomic_write(path, value)

            with mock.patch.object(
                store, "_atomic_write_json", side_effect=atomic_write
            ):
                with self.assertRaises(store.StoreError) as raised:
                    action()  # type: ignore[operator]
            self.assertEqual(raised.exception.code, "injected_interruption")
            self.assertTrue(interrupted)
            journal_path = self.state_dir / ".transaction.json"
            self.assertTrue(journal_path.exists())
            self.assertEqual(stat.S_IMODE(journal_path.stat().st_mode), 0o600)

        payload = {"topic": "Git rebase", "goal": "安全に実行できる"}
        interrupt_index_once(lambda: store.create_track(self.state_dir, payload))
        journal = self.read_json(self.state_dir / ".transaction.json")
        track_id = str(journal["writes"]["index.json"]["active_track_id"])
        listing = store.list_tracks(self.state_dir)
        self.assertEqual([item["id"] for item in listing["tracks"]], [track_id])
        self.assertFalse((self.state_dir / ".transaction.json").exists())

        interrupt_index_once(
            lambda: store.update_track(
                self.state_dir,
                track_id,
                0,
                {"set": {"next_action": "競合を練習する"}},
            )
        )
        updated = store.load_track(self.state_dir, track_id)
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["next_action"], "競合を練習する")

        interrupt_index_once(lambda: store.archive_track(self.state_dir, track_id, 1))
        listing = store.list_tracks(self.state_dir, include_archived=True)
        self.assertEqual(listing["tracks"], [])
        self.assertEqual([item["id"] for item in listing["archived"]], [track_id])

        interrupt_index_once(lambda: store.restore_track(self.state_dir, track_id))
        restored = store.load_track(self.state_dir, track_id)
        self.assertEqual(restored["status"], "active")
        self.assertEqual(restored["revision"], 3)

        interrupt_index_once(
            lambda: store.delete_track(self.state_dir, track_id, track_id)
        )
        listing = store.list_tracks(self.state_dir, include_archived=True)
        self.assertEqual(listing["tracks"], [])
        self.assertEqual(listing["archived"], [])
        self.assertFalse((self.state_dir / ".transaction.json").exists())
        validation = store.validate_store(self.state_dir)
        self.assertTrue(validation["valid"])

    def test_journal_recovers_interruption_after_index_before_delete(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        active_path = self.state_dir / "tracks" / f"{track_id}.json"
        original_unlink = store._unlink_and_fsync
        interrupted = False

        def unlink(path: Path, *, missing_ok: bool) -> None:
            nonlocal interrupted
            if Path(path) == active_path and not interrupted:
                interrupted = True
                raise store.StoreError("injected_interruption", "test interruption")
            original_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(store, "_unlink_and_fsync", side_effect=unlink):
            with self.assertRaises(store.StoreError):
                store.archive_track(self.state_dir, track_id, 0)

        self.assertTrue(interrupted)
        self.assertTrue((self.state_dir / ".transaction.json").exists())
        self.assertTrue(active_path.exists())
        self.assertTrue((self.state_dir / "archive" / f"{track_id}.json").exists())

        listing = store.list_tracks(self.state_dir, include_archived=True)
        self.assertEqual(listing["tracks"], [])
        self.assertEqual([item["id"] for item in listing["archived"]], [track_id])
        self.assertFalse(active_path.exists())
        self.assertFalse((self.state_dir / ".transaction.json").exists())

    def test_recovery_removes_only_transaction_target_temp_and_fsyncs(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        temporary = self.leave_pending_update_with_entry(
            track_id,
            f".{track_path.name}.{'a' * 16}.tmp",
        )
        original_fsync = store._fsync_directory
        fsynced: list[Path] = []

        def fsync(path: Path) -> None:
            fsynced.append(Path(path))
            original_fsync(path)

        with mock.patch.object(store, "_fsync_directory", side_effect=fsync):
            recovered = store.load_track(self.state_dir, track_id)

        self.assertEqual(recovered["revision"], 1)
        self.assertEqual(recovered["next_action"], "クラッシュ後に回復する")
        self.assertFalse(temporary.exists())
        self.assertFalse((self.state_dir / ".transaction.json").exists())
        self.assertIn(track_path.parent, fsynced)

    def test_recovery_keeps_non_target_dotfile_and_fails_closed(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        unrelated = self.leave_pending_update_with_entry(
            track_id,
            f".unrelated.json.{'b' * 16}.tmp",
        )

        with self.assertRaises(store.StoreError) as raised:
            store.load_track(self.state_dir, track_id)

        self.assertEqual(raised.exception.code, "unexpected_state_entry")
        self.assertTrue(unrelated.exists())
        self.assertTrue((self.state_dir / ".transaction.json").exists())

    def test_journal_survives_post_replace_fsync_failure_and_recovers(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        original_fsync = store._fsync_directory
        interrupted = False

        def fsync(path: Path) -> None:
            nonlocal interrupted
            if Path(path) == self.state_dir / "tracks" and not interrupted:
                interrupted = True
                raise OSError("injected fsync failure")
            original_fsync(path)

        with mock.patch.object(store, "_fsync_directory", side_effect=fsync):
            with self.assertRaises(store.StoreError) as raised:
                store.update_track(
                    self.state_dir,
                    track_id,
                    0,
                    {"set": {"next_action": "journalから回復"}},
                )

        self.assertEqual(raised.exception.code, "state_write_failed")
        self.assertTrue(interrupted)
        self.assertTrue((self.state_dir / ".transaction.json").exists())
        raw_track = self.read_json(self.state_dir / "tracks" / f"{track_id}.json")
        self.assertEqual(raw_track["revision"], 1)

        recovered = store.load_track(self.state_dir, track_id)
        self.assertEqual(recovered["revision"], 1)
        self.assertEqual(recovered["next_action"], "journalから回復")
        self.assertFalse((self.state_dir / ".transaction.json").exists())

    def test_journal_survives_post_replace_chmod_failure_and_recovers(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        original_chmod = store._path_chmod
        interrupted = False

        def chmod(path: Path, mode: int) -> None:
            nonlocal interrupted
            if Path(path) == track_path and not interrupted:
                interrupted = True
                raise OSError("injected chmod failure")
            original_chmod(path, mode)

        with mock.patch.object(store, "_path_chmod", side_effect=chmod):
            with self.assertRaises(store.StoreError) as raised:
                store.update_track(
                    self.state_dir,
                    track_id,
                    0,
                    {"set": {"next_action": "chmod失敗から回復"}},
                )

        self.assertEqual(raised.exception.code, "state_write_failed")
        self.assertTrue(interrupted)
        self.assertTrue((self.state_dir / ".transaction.json").exists())
        self.assertEqual(self.read_json(track_path)["revision"], 1)

        recovered = store.load_track(self.state_dir, track_id)
        self.assertEqual(recovered["revision"], 1)
        self.assertEqual(recovered["next_action"], "chmod失敗から回復")
        self.assertFalse((self.state_dir / ".transaction.json").exists())

    def test_corrupt_and_unknown_transaction_stop_without_overwrite(self) -> None:
        original_state_dir = self.state_dir
        try:
            for case in ("corrupt", "unknown"):
                with (
                    self.subTest(case=case),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    self.state_dir = Path(temporary) / "state"
                    created = self.create()
                    track_id = str(created["id"])
                    index_path = self.state_dir / "index.json"
                    track_path = self.state_dir / "tracks" / f"{track_id}.json"
                    journal_path = self.state_dir / ".transaction.json"
                    before_index = index_path.read_bytes()
                    before_track = track_path.read_bytes()
                    if case == "corrupt":
                        journal_path.write_bytes(b"{broken")
                        expected_code = "corrupted_json"
                    else:
                        journal_path.write_text(
                            json.dumps({"schema_version": 99}),
                            encoding="utf-8",
                        )
                        expected_code = "unknown_schema"
                    os.chmod(journal_path, 0o600)
                    before_journal = journal_path.read_bytes()

                    code, output, error = self.run_cli("list")
                    self.assertEqual(code, 1)
                    self.assertEqual(output, {})
                    self.assertEqual(error["error"]["code"], expected_code)
                    self.assertEqual(index_path.read_bytes(), before_index)
                    self.assertEqual(track_path.read_bytes(), before_track)
                    self.assertEqual(journal_path.read_bytes(), before_journal)
        finally:
            self.state_dir = original_state_dir

    def test_pending_transaction_does_not_overwrite_invalid_existing_state(
        self,
    ) -> None:
        original_state_dir = self.state_dir
        original_atomic_write = store._atomic_write_json
        try:
            for target in ("unknown_index", "corrupt_track"):
                with (
                    self.subTest(target=target),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    self.state_dir = Path(temporary) / "state"
                    created = self.create()
                    track_id = str(created["id"])
                    interrupted = False

                    def atomic_write(path: Path, value: dict[str, object]) -> None:
                        nonlocal interrupted
                        if (
                            Path(path) == self.state_dir / "index.json"
                            and not interrupted
                        ):
                            interrupted = True
                            raise store.StoreError(
                                "injected_interruption",
                                "test interruption",
                            )
                        original_atomic_write(path, value)

                    with mock.patch.object(
                        store,
                        "_atomic_write_json",
                        side_effect=atomic_write,
                    ):
                        with self.assertRaises(store.StoreError):
                            store.update_track(
                                self.state_dir,
                                track_id,
                                0,
                                {"set": {"next_action": "保留中の更新"}},
                            )
                    self.assertTrue(interrupted)
                    journal_path = self.state_dir / ".transaction.json"
                    self.assertTrue(journal_path.exists())

                    if target == "unknown_index":
                        invalid_path = self.state_dir / "index.json"
                        invalid_value = self.read_json(invalid_path)
                        invalid_value["schema_version"] = 99
                        self.write_json(invalid_path, invalid_value)
                        expected_code = "unknown_schema"
                    else:
                        invalid_path = self.state_dir / "tracks" / f"{track_id}.json"
                        invalid_path.write_bytes(b"{broken")
                        os.chmod(invalid_path, 0o600)
                        expected_code = "corrupted_json"
                    before_invalid = invalid_path.read_bytes()
                    before_journal = journal_path.read_bytes()

                    code, output, error = self.run_cli("list")
                    self.assertEqual(code, 1)
                    self.assertEqual(output, {})
                    self.assertEqual(error["error"]["code"], expected_code)
                    self.assertEqual(invalid_path.read_bytes(), before_invalid)
                    self.assertEqual(journal_path.read_bytes(), before_journal)
        finally:
            self.state_dir = original_state_dir

    def test_delete_transaction_cannot_drop_unrelated_track_from_index(self) -> None:
        first = self.create("Python", "関数を説明する")
        second = self.create("SQL", "集計する")
        first_id = str(first["id"])
        second_id = str(second["id"])
        index_path = self.state_dir / "index.json"
        before_index = self.read_json(index_path)
        before_index_bytes = index_path.read_bytes()
        final_index = json.loads(json.dumps(before_index))
        final_index["tracks"] = {}
        final_index["active_track_id"] = None
        journal = {
            "schema_version": 1,
            "operation": "delete",
            "before": {
                "index": before_index,
                "files": {f"tracks/{first_id}.json": first},
            },
            "writes": {"index.json": final_index},
            "deletes": [f"tracks/{first_id}.json"],
            "created_at": "2026-07-15T00:00:00Z",
        }
        journal_path = self.state_dir / ".transaction.json"
        self.write_json(journal_path, journal)
        first_path = self.state_dir / "tracks" / f"{first_id}.json"
        second_path = self.state_dir / "tracks" / f"{second_id}.json"
        before_first = first_path.read_bytes()
        before_second = second_path.read_bytes()
        before_journal = journal_path.read_bytes()

        code, output, error = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_transaction")
        self.assertEqual(index_path.read_bytes(), before_index_bytes)
        self.assertEqual(first_path.read_bytes(), before_first)
        self.assertEqual(second_path.read_bytes(), before_second)
        self.assertEqual(journal_path.read_bytes(), before_journal)

    def test_state_dir_ancestor_symlink_is_rejected_without_writing_target(
        self,
    ) -> None:
        original_state_dir = self.state_dir
        try:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                real_parent = base / "real-parent"
                real_parent.mkdir(mode=0o700)
                linked_parent = base / "linked-parent"
                linked_parent.symlink_to(real_parent, target_is_directory=True)
                self.state_dir = linked_parent / "state"

                code, output, error = self.run_cli("list")
                self.assertEqual(code, 1)
                self.assertEqual(output, {})
                self.assertEqual(error["error"]["code"], "symlink_rejected")
                self.assertEqual(
                    error["error"]["details"]["path"],
                    str(linked_parent),
                )
                self.assertFalse((real_parent / "state").exists())
        finally:
            self.state_dir = original_state_dir

    def test_ancestor_swap_cannot_redirect_state_writes_after_validation(self) -> None:
        original_state_dir = self.state_dir
        original_mkdir = store.os.mkdir
        try:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                shared = base / "shared"
                tenant = shared / "tenant"
                tenant.mkdir(parents=True, mode=0o700)
                pinned_parent = base / "pinned-parent"
                attacker = base / "attacker"
                attacker.mkdir(mode=0o700)
                self.state_dir = tenant / "state"
                swapped = False

                def mkdir(
                    path: object,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> None:
                    nonlocal swapped
                    is_state_creation = (
                        dir_fd is None and os.fspath(path) == os.fspath(self.state_dir)
                    ) or (dir_fd is not None and os.fspath(path) == "state")
                    if is_state_creation and not swapped:
                        tenant.rename(pinned_parent)
                        tenant.symlink_to(attacker, target_is_directory=True)
                        swapped = True
                    if dir_fd is None:
                        original_mkdir(path, mode)
                    else:
                        original_mkdir(path, mode, dir_fd=dir_fd)

                with mock.patch.object(store.os, "mkdir", side_effect=mkdir):
                    code, output, error = self.run_cli("list")

                self.assertTrue(swapped)
                self.assertEqual(code, 0, error)
                self.assertEqual(output["tracks"], [])
                self.assertFalse((attacker / "state").exists())
                self.assertTrue((pinned_parent / "state" / "index.json").exists())
        finally:
            self.state_dir = original_state_dir

    def test_missing_state_below_nonsticky_shared_directory_is_rejected(self) -> None:
        original_state_dir = self.state_dir
        try:
            with tempfile.TemporaryDirectory() as temporary:
                shared = Path(temporary) / "shared"
                shared.mkdir(mode=0o777)
                os.chmod(shared, 0o777)
                self.state_dir = shared / "state"

                code, output, error = self.run_cli("list")
                self.assertEqual(code, 1)
                self.assertEqual(output, {})
                self.assertEqual(
                    error["error"]["code"],
                    "unsafe_state_ancestor",
                )
                self.assertFalse(self.state_dir.exists())
        finally:
            self.state_dir = original_state_dir

    def test_update_transaction_must_increment_revision(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        index_path = self.state_dir / "index.json"
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        before_index = self.read_json(index_path)
        before_index_bytes = index_path.read_bytes()
        before_track = self.read_json(track_path)
        before_track_bytes = track_path.read_bytes()
        final_track = json.loads(json.dumps(before_track))
        final_track["next_action"] = "revisionを増やさない不正更新"
        final_index = json.loads(json.dumps(before_index))
        final_index["tracks"][track_id] = {
            "topic": final_track["topic"],
            "goal": final_track["goal"],
            "status": final_track["status"],
            "revision": final_track["revision"],
            "updated_at": final_track["updated_at"],
        }
        journal = {
            "schema_version": 1,
            "operation": "update",
            "before": {
                "index": before_index,
                "files": {f"tracks/{track_id}.json": before_track},
            },
            "writes": {
                "index.json": final_index,
                f"tracks/{track_id}.json": final_track,
            },
            "deletes": [],
            "created_at": "2026-07-15T00:00:00Z",
        }
        journal_path = self.state_dir / ".transaction.json"
        self.write_json(journal_path, journal)
        before_journal = journal_path.read_bytes()

        code, output, error = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_transaction")
        self.assertEqual(index_path.read_bytes(), before_index_bytes)
        self.assertEqual(track_path.read_bytes(), before_track_bytes)
        self.assertEqual(journal_path.read_bytes(), before_journal)

    def test_update_transaction_preserves_history_and_timestamp_ownership(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        store.update_track(
            self.state_dir,
            track_id,
            0,
            {
                "append_history": [
                    {
                        "task": "一つ目の課題",
                        "learner_response": "一つ目の回答",
                        "feedback": "一つ目の助言",
                        "attainment": "進行中",
                    },
                    {
                        "task": "二つ目の課題",
                        "learner_response": "二つ目の回答",
                        "feedback": "二つ目の助言",
                        "attainment": "到達",
                    },
                ]
            },
        )
        before_index = self.read_json(self.state_dir / "index.json")
        before_track = self.read_json(self.state_dir / "tracks" / f"{track_id}.json")
        original_history = json.loads(json.dumps(before_track["history"]))
        modified_history = json.loads(json.dumps(original_history))
        modified_history[0]["feedback"] = "既存履歴の改変"
        appended_with_wrong_timestamp = json.loads(json.dumps(original_history))
        appended_with_wrong_timestamp.append(
            {
                "task": "三つ目の課題",
                "learner_response": "三つ目の回答",
                "feedback": "三つ目の助言",
                "attainment": "進行中",
                "recorded_at": "2099-01-01T00:00:01Z",
            }
        )
        timestamp = "2099-01-01T00:00:00Z"
        cases = {
            "deleted": original_history[:-1],
            "modified": modified_history,
            "reordered": list(reversed(original_history)),
            "suffix_timestamp_mismatch": appended_with_wrong_timestamp,
            "index_timestamp_mismatch": original_history,
        }

        for case, final_history in cases.items():
            with self.subTest(case=case):
                final_track = json.loads(json.dumps(before_track))
                final_track["revision"] += 1
                final_track["updated_at"] = timestamp
                final_track["next_action"] = "不正なjournalを拒否する"
                final_track["history"] = final_history
                final_index = json.loads(json.dumps(before_index))
                final_index["tracks"][track_id] = store._metadata(final_track)
                final_index["updated_at"] = (
                    before_index["updated_at"]
                    if case == "index_timestamp_mismatch"
                    else timestamp
                )
                transaction = {
                    "schema_version": 1,
                    "operation": "update",
                    "before": {
                        "index": before_index,
                        "files": {f"tracks/{track_id}.json": before_track},
                    },
                    "writes": {
                        "index.json": final_index,
                        f"tracks/{track_id}.json": final_track,
                    },
                    "deletes": [],
                    "created_at": timestamp,
                }

                with self.assertRaises(store.StoreError) as raised:
                    store._validate_transaction(transaction)
                self.assertEqual(raised.exception.code, "invalid_transaction")

    def test_archive_and_delete_transactions_preserve_active_selection(self) -> None:
        first = self.create("First", "最初を学ぶ")
        second = self.create("Second", "二番目を学ぶ")
        third = self.create("Third", "三番目を学ぶ")
        first_id = str(first["id"])
        remaining_ids = {str(second["id"]), str(third["id"])}
        before_index = self.read_json(self.state_dir / "index.json")
        before_track = self.read_json(self.state_dir / "tracks" / f"{first_id}.json")
        timestamp = "2099-01-01T00:00:00Z"

        for operation in ("archive", "delete"):
            with self.subTest(operation=operation):
                final_index = json.loads(json.dumps(before_index))
                del final_index["tracks"][first_id]
                final_index["updated_at"] = timestamp
                if operation == "archive":
                    moved_track = json.loads(json.dumps(before_track))
                    moved_track["status"] = "archived"
                    moved_track["revision"] += 1
                    moved_track["updated_at"] = timestamp
                    final_index["archived"][first_id] = store._metadata(moved_track)
                    expected_active = store._choose_active(final_index["tracks"])
                    writes = {
                        "index.json": final_index,
                        f"archive/{first_id}.json": moved_track,
                    }
                    deletes = [f"tracks/{first_id}.json"]
                    before_files = {
                        f"archive/{first_id}.json": None,
                        f"tracks/{first_id}.json": before_track,
                    }
                else:
                    expected_active = before_index["active_track_id"]
                    writes = {"index.json": final_index}
                    deletes = [f"tracks/{first_id}.json"]
                    before_files = {f"tracks/{first_id}.json": before_track}
                final_index["active_track_id"] = next(
                    track_id
                    for track_id in remaining_ids
                    if track_id != expected_active
                )
                transaction = {
                    "schema_version": 1,
                    "operation": operation,
                    "before": {
                        "index": before_index,
                        "files": before_files,
                    },
                    "writes": writes,
                    "deletes": deletes,
                    "created_at": timestamp,
                }

                with self.assertRaises(store.StoreError) as raised:
                    store._validate_transaction(transaction)
                self.assertEqual(raised.exception.code, "invalid_transaction")

    def test_lone_surrogate_input_is_rejected_without_state_change(self) -> None:
        surrogate = "\ud800"
        code, output, error = self.run_cli(
            "create",
            "--input",
            "-",
            input_value={"topic": surrogate, "goal": "安全に拒否する"},
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_unicode")
        self.assertFalse(self.state_dir.exists())

        created = self.create()
        track_id = str(created["id"])
        index_path = self.state_dir / "index.json"
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        before_index = index_path.read_bytes()
        before_track = track_path.read_bytes()
        code, output, error = self.run_cli(
            "update",
            track_id,
            "--expected-revision",
            "0",
            "--input",
            "-",
            input_value={"set": {"next_action": surrogate}},
        )
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_unicode")
        self.assertEqual(index_path.read_bytes(), before_index)
        self.assertEqual(track_path.read_bytes(), before_track)
        self.assertFalse((self.state_dir / ".transaction.json").exists())

    def test_lone_surrogate_in_existing_json_is_structured_error(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        track_path = self.state_dir / "tracks" / f"{track_id}.json"
        track = self.read_json(track_path)
        track["next_action"] = "\ud800"
        encoded = json.dumps(track, ensure_ascii=True).encode("ascii")
        track_path.write_bytes(encoded)
        os.chmod(track_path, 0o600)

        code, output, error = self.run_cli("load", track_id)
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_unicode")
        self.assertEqual(track_path.read_bytes(), encoded)

    def test_track_unlink_fsyncs_parent_directory(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        original_fsync = store._fsync_directory
        fsynced: list[Path] = []

        def fsync(path: Path) -> None:
            fsynced.append(Path(path))
            original_fsync(path)

        with mock.patch.object(store, "_fsync_directory", side_effect=fsync):
            store.delete_track(self.state_dir, track_id, track_id)

        self.assertIn(self.state_dir / "tracks", fsynced)
        self.assertIn(self.state_dir, fsynced)

    def test_created_permissions_are_private(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        for directory in (
            self.state_dir,
            self.state_dir / "tracks",
            self.state_dir / "archive",
        ):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for path in (
            self.state_dir / ".lock",
            self.state_dir / "index.json",
            self.state_dir / "tracks" / f"{track_id}.json",
        ):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_symlink_track_is_rejected(self) -> None:
        created = self.create()
        track_id = str(created["id"])
        path = self.state_dir / "tracks" / f"{track_id}.json"
        target = self.state_dir / "real.json"
        path.replace(target)
        path.symlink_to(target)

        code, output, error = self.run_cli("load", track_id)
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "symlink_rejected")

    def test_path_traversal_track_id_is_rejected(self) -> None:
        self.create()
        code, output, error = self.run_cli("load", "../index")
        self.assertEqual(code, 1)
        self.assertEqual(output, {})
        self.assertEqual(error["error"]["code"], "invalid_track_id")


class AtomicWriteTest(unittest.TestCase):
    def test_replace_failure_preserves_original_and_removes_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            target = directory / "state.json"
            store._atomic_write_json(target, {"value": "old"})
            original = target.read_bytes()

            with mock.patch.object(store.os, "replace", side_effect=OSError("failed")):
                with self.assertRaises(store.StoreError) as raised:
                    store._atomic_write_json(target, {"value": "new"})

            self.assertEqual(raised.exception.code, "state_write_failed")

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(
                [entry.name for entry in directory.iterdir()],
                ["state.json"],
            )

    def test_cleanup_failure_does_not_hide_primary_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            target = directory / "state.json"
            store._atomic_write_json(target, {"value": "old"})
            original = target.read_bytes()

            with (
                mock.patch.object(
                    store.os, "replace", side_effect=OSError("replace failed")
                ),
                mock.patch.object(
                    Path, "unlink", side_effect=OSError("cleanup failed")
                ),
            ):
                with self.assertRaises(store.StoreError) as raised:
                    store._atomic_write_json(target, {"value": "new"})

            self.assertEqual(raised.exception.code, "state_write_failed")
            self.assertIn("replace failed", raised.exception.details["reason"])
            self.assertIn(
                "cleanup failed",
                raised.exception.details["cleanup_error"]["reason"],
            )
            self.assertEqual(target.read_bytes(), original)

    def test_cleanup_stat_failure_cannot_hide_primary_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            os.chmod(directory, 0o700)
            target = directory / "state.json"
            store._atomic_write_json(target, {"value": "old"})
            original = target.read_bytes()
            original_exists = store._path_exists

            def exists(path: Path) -> bool:
                if Path(path).name.endswith(".tmp"):
                    raise OSError("temporary lstat failed")
                return original_exists(path)

            with (
                mock.patch.object(
                    store.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
                mock.patch.object(store, "_path_exists", side_effect=exists),
            ):
                with self.assertRaises(store.StoreError) as raised:
                    store._atomic_write_json(target, {"value": "new"})

            self.assertEqual(raised.exception.code, "state_write_failed")
            self.assertIn("replace failed", raised.exception.details["reason"])
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(
                [entry.name for entry in directory.iterdir()],
                ["state.json"],
            )


if __name__ == "__main__":
    unittest.main()
