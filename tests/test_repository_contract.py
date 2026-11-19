from __future__ import annotations

import unittest

import support


PLUGIN_ROOT = support.PLUGIN_ROOT


class RepositoryContractTests(unittest.TestCase):
    def test_public_plugin_has_contribution_files(self):
        for filename in (
            "plugin.yaml",
            "plugin.json",
            "README.md",
            "LICENSE",
            "hooks.py",
        ):
            self.assertTrue((PLUGIN_ROOT / filename).is_file(), filename)
        manifest = (PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("version: 2.1.0", manifest)
        marketplace_manifest = (PLUGIN_ROOT / "plugin.json").read_text(
            encoding="utf-8"
        )
        self.assertIn('"version": "2.1.0"', marketplace_manifest)

    def test_install_lifecycle_is_hook_owned(self):
        hooks = (PLUGIN_ROOT / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("def install() -> None:", hooks)
        self.assertIn("_stop_workers()", hooks)
        self.assertFalse((PLUGIN_ROOT / "execute.py").exists())

    def test_runtime_state_is_excluded_from_distribution(self):
        ignore = (PLUGIN_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("config.json", ignore.splitlines())
        self.assertIn(".toggle-*", ignore.splitlines())
        self.assertIn("__pycache__/", ignore.splitlines())

    def test_fanout_defaults_are_finite(self):
        defaults = (PLUGIN_ROOT / "default_config.yaml").read_text(
            encoding="utf-8"
        )
        config_ui = (PLUGIN_ROOT / "webui" / "config.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("max_parallel: 4", defaults)
        self.assertIn('max="16"', config_ui)
        self.assertNotIn("0 means no cap", config_ui)

    def test_store_uses_scoped_lifecycle_and_websocket_payload(self):
        store = (PLUGIN_ROOT / "webui" / "swarm-store.js").read_text(
            encoding="utf-8"
        )
        panel = (PLUGIN_ROOT / "webui" / "swarm-panel.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("async init()", store)
        self.assertIn("const payload = envelope?.data || {};", store)
        self.assertIn("data.parent_context_id !== expectedParent", store)
        self.assertIn("x-create=\"$store.swarmStore.onMount()\"", panel)
        self.assertIn("x-destroy=\"$store.swarmStore.cleanup()\"", panel)
        self.assertIn("overflow-wrap: anywhere", panel)

    def test_state_changing_apis_are_chat_scoped(self):
        for filename in (
            "swarm_cancel.py",
            "swarm_clear_completed.py",
            "swarm_retry_message.py",
            "swarm_send_message.py",
        ):
            source = (PLUGIN_ROOT / "api" / filename).read_text(
                encoding="utf-8"
            )
            self.assertIn("parent_context_id", source, filename)

    def test_readme_explains_orchestrator_boundary(self):
        readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Use one orchestrator per chat", readme)
        self.assertIn("Agent Harness Ultra", readme)
        self.assertIn("There is no separate Execute step.", readme)


if __name__ == "__main__":
    unittest.main()
