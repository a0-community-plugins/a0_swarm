from __future__ import annotations

import unittest

import support  # noqa: F401
from usr.plugins.a0_swarm.helpers.task_specs import (
    ABSOLUTE_MAX_PARALLEL,
    DEFAULT_MAX_PARALLEL,
    HARNESS_RUN_CONTEXT_KEY,
    HARNESS_WORKER_CONTEXT_KEY,
    SWARM_WORKER_CONTEXT_KEY,
    effective_max_parallel,
    normalize_tasks,
    normalize_workspace_mode,
    orchestration_conflict,
    result_byte_cap,
)


class FakeContext:
    def __init__(self, data=None):
        self.data = data or {}

    def get_data(self, key, recursive=True):
        return self.data.get(key)


class TaskSpecTests(unittest.TestCase):
    def test_parallel_limit_is_finite_and_absolutely_bounded(self):
        self.assertEqual(effective_max_parallel(None), DEFAULT_MAX_PARALLEL)
        self.assertEqual(effective_max_parallel(0), DEFAULT_MAX_PARALLEL)
        self.assertEqual(effective_max_parallel(-4), DEFAULT_MAX_PARALLEL)
        self.assertEqual(effective_max_parallel(8), 8)
        self.assertEqual(effective_max_parallel(99), ABSOLUTE_MAX_PARALLEL)

    def test_result_cap_is_bounded(self):
        self.assertEqual(result_byte_cap(None), 64 * 1024)
        self.assertEqual(result_byte_cap(0), 64 * 1024)
        self.assertEqual(result_byte_cap(2), 2 * 1024)
        self.assertEqual(result_byte_cap(9999), 1024 * 1024)

    def test_task_payloads_are_normalized(self):
        tasks = normalize_tasks(
            [{"task": "  investigate  ", "profile": "  selected  "}],
            max_parallel=4,
        )
        self.assertEqual(
            tasks,
            [{
                "label": "Agent-1",
                "task": "investigate",
                "profile": "selected",
                "endpoint": "",
            }],
        )

    def test_invalid_or_excessive_tasks_fail_closed(self):
        bad_payloads = [
            None,
            [],
            ["not-an-object"],
            [{"label": "empty", "task": "   "}],
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    normalize_tasks(payload, max_parallel=4)
        with self.assertRaisesRegex(ValueError, "at most 1"):
            normalize_tasks(
                [{"task": "one"}, {"task": "two"}],
                max_parallel=1,
            )

    def test_workspace_mode_defaults_safely(self):
        self.assertEqual(normalize_workspace_mode("isolated"), "isolated")
        self.assertEqual(normalize_workspace_mode("INHERIT"), "inherit")
        self.assertEqual(normalize_workspace_mode("unknown"), "none")

    def test_nested_orchestrators_are_rejected(self):
        self.assertIn(
            "cannot start another swarm",
            orchestration_conflict(
                FakeContext({SWARM_WORKER_CONTEXT_KEY: {"run_id": "run-1"}})
            ),
        )
        self.assertIn(
            "Harness workers",
            orchestration_conflict(
                FakeContext({HARNESS_WORKER_CONTEXT_KEY: {"sub_task_id": "st-1"}})
            ),
        )
        self.assertIn(
            "Harness run is active",
            orchestration_conflict(
                FakeContext(
                    {HARNESS_RUN_CONTEXT_KEY: {"status": "active"}}
                )
            ),
        )
        self.assertEqual(
            orchestration_conflict(
                FakeContext(
                    {HARNESS_RUN_CONTEXT_KEY: {"status": "completed"}}
                )
            ),
            "",
        )


if __name__ == "__main__":
    unittest.main()
