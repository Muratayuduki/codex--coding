from __future__ import annotations

import json
import os
import shlex
import subprocess
import tomllib
import unittest
from pathlib import Path


CODEX_HOME = Path(__file__).resolve().parents[1]
CODEX_COMMAND = "codex.cmd" if os.name == "nt" else "codex"


class CodexConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = tomllib.loads((CODEX_HOME / "config.toml").read_text(encoding="utf-8"))

    def test_main_agent_keeps_max_reasoning(self) -> None:
        self.assertEqual(self.config["model"], "gpt-5.6-sol")
        self.assertEqual(self.config["model_reasoning_effort"], "max")
        self.assertEqual(self.config["plan_mode_reasoning_effort"], "max")

    def test_codex_accepts_the_live_config_in_strict_mode(self) -> None:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(CODEX_HOME)
        result = subprocess.run(
            [CODEX_COMMAND, "app-server", "--stdio", "--strict-config"],
            cwd=CODEX_HOME,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_secret_env_files_are_ignored_inside_tracked_trees(self) -> None:
        protected = (
            "user-skills/example/.env",
            "user-skills/example/.env.local",
            "user-skills/example/.dev.vars",
            "user-skills/example/.dev.vars.local",
            "user-skills/example/.envrc",
            "user-skills/example/.envrc.local",
        )
        templates = (
            "user-skills/example/.env.example",
            "user-skills/example/.dev.vars.template",
            "user-skills/example/.envrc.dist",
        )
        root_templates = (".env.example", ".dev.vars.template", ".envrc.dist")
        for path in protected:
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", path],
                    cwd=CODEX_HOME,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, path)
        for path in templates:
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", path],
                    cwd=CODEX_HOME,
                    check=False,
                )
                self.assertEqual(result.returncode, 1, path)
        for path in root_templates:
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", path],
                    cwd=CODEX_HOME,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, path)

    def test_hook_commands_reference_existing_scripts(self) -> None:
        hooks = self.config["hooks"]
        scripts = {
            "PreToolUse": "codex_env_guard.py",
            "UserPromptSubmit": "codex_readme_guard.py",
            "Stop": "codex_memo_guard.py",
        }
        for event, script in scripts.items():
            with self.subTest(event=event):
                handler = hooks[event][0]["hooks"][0]
                if os.name == "nt":
                    command = handler["commandWindows"]
                    self.assertIn(str(CODEX_HOME / "hooks" / script), command)
                else:
                    tokens = shlex.split(os.path.expandvars(handler["command"]))
                    self.assertGreaterEqual(len(tokens), 2)
                    self.assertTrue(Path(tokens[1]).is_file(), tokens[1])

    def test_hook_configuration_is_portable(self) -> None:
        hooks = self.config["hooks"]
        for event in ("PreToolUse", "UserPromptSubmit", "Stop"):
            with self.subTest(event=event):
                handler = hooks[event][0]["hooks"][0]
                self.assertIn("$HOME/.codex/hooks/", handler["command"])
                self.assertNotIn("/home/sota411", handler["command"])
                self.assertIn("C:\\vscode\\codex-config\\hooks\\", handler["commandWindows"])

    def test_mcp_settings_do_not_embed_static_credentials(self) -> None:
        allowed_env_keys = {
            "node_repl": {
                "BROWSER_USE_AVAILABLE_BACKENDS",
                "BROWSER_USE_CODEX_APP_BUILD_FLAVOR",
                "BROWSER_USE_CODEX_APP_VERSION",
                "CODEX_CLI_PATH",
                "CODEX_HOME",
                "NODE_REPL_INSTRUCTIONS_USE_CASE_BROWSER",
                "NODE_REPL_INSTRUCTIONS_USE_CASE_CHROME",
                "NODE_REPL_NATIVE_PIPE_CONNECT_TIMEOUT_MS",
                "NODE_REPL_NODE_MODULE_DIRS",
                "NODE_REPL_NODE_PATH",
                "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
                "NODE_REPL_TRUSTED_CODE_PATHS",
                "SKY_CUA_NATIVE_PIPE",
                "SKY_CUA_NATIVE_PIPE_DIRECTORY",
            }
        }
        for name, server in self.config.get("mcp_servers", {}).items():
            with self.subTest(server=name):
                self.assertNotIn("http_headers", server)
                if name in allowed_env_keys:
                    env = server.get("env")
                    self.assertIsInstance(env, dict)
                    self.assertEqual(set(env), allowed_env_keys[name])
                    self.assertTrue(all(isinstance(value, str) and value for value in env.values()))
                else:
                    self.assertNotIn("env", server)

    def test_custom_agent_profiles_are_valid_toml(self) -> None:
        expected_efforts = {
            "scout_fast": "low",
            "tester_fast": "low",
            "worker_standard": "medium",
            "reviewer_deep": "high",
            "specialist_max": "max",
        }
        actual: dict[str, str] = {}
        for path in sorted((CODEX_HOME / "agents").glob("*.toml")):
            profile = tomllib.loads(path.read_text(encoding="utf-8"))
            actual[profile["name"]] = profile["model_reasoning_effort"]
        self.assertEqual(actual, expected_efforts)

    def test_custom_agent_models_and_efforts_exist_in_current_catalog(self) -> None:
        result = subprocess.run(
            [CODEX_COMMAND, "debug", "models"],
            cwd=CODEX_HOME,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        catalog = json.loads(result.stdout)
        supported = {
            model["slug"]: {level["effort"] for level in model["supported_reasoning_levels"]}
            for model in catalog["models"]
        }
        for path in sorted((CODEX_HOME / "agents").glob("*.toml")):
            profile = tomllib.loads(path.read_text(encoding="utf-8"))
            with self.subTest(profile=profile["name"]):
                self.assertIn(profile["model"], supported)
                self.assertIn(
                    profile["model_reasoning_effort"],
                    supported[profile["model"]],
                )


if __name__ == "__main__":
    unittest.main()
