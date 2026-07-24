from __future__ import annotations

import threading
import unittest

import support  # noqa: F401
from usr.plugins.a0_swarm.helpers.registry import (
    SwarmAgent,
    SwarmAgentStatus,
    SwarmRegistry,
    utc_iso_now,
)


def make_agent(
    name: str,
    parent: str,
    run_id: str,
    status: SwarmAgentStatus = SwarmAgentStatus.PENDING,
) -> SwarmAgent:
    return SwarmAgent(
        agent_name=name,
        label=name,
        task=f"task for {name}",
        context_id=f"ctx-{name}",
        parent_context_id=parent,
        status=status,
        started_at=utc_iso_now(),
        run_id=run_id,
    )


class RegistryTests(unittest.TestCase):
    def setUp(self):
        SwarmRegistry._instance = SwarmRegistry()
        self.registry = SwarmRegistry.get()

    def test_snapshots_are_parent_scoped_and_keep_multiple_runs(self):
        first = self.registry.create_run("chat-a", title="first")
        second = self.registry.create_run("chat-a", title="second")
        other = self.registry.create_run("chat-b", title="other")
        self.registry.register(make_agent("SA1_1-aaa111", "chat-a", first.run_id))
        self.registry.register(make_agent("SA1_1-bbb222", "chat-a", second.run_id))
        self.registry.register(make_agent("SA1_1-ccc333", "chat-b", other.run_id))

        snapshot = self.registry.snapshot("chat-a")
        self.assertEqual(len(snapshot["runs"]), 2)
        self.assertEqual(
            {agent["agent_name"] for agent in snapshot["agents"]},
            {"SA1_1-aaa111", "SA1_1-bbb222"},
        )

    def test_messages_cannot_cross_runs(self):
        first = self.registry.create_run("chat-a")
        second = self.registry.create_run("chat-a")
        self.registry.register(make_agent("SA1_1-aaa111", "chat-a", first.run_id))
        self.registry.register(make_agent("SA1_2-bbb222", "chat-a", second.run_id))

        with self.assertRaisesRegex(ValueError, "not in this swarm run"):
            self.registry.create_message(
                first.run_id,
                "SA1_1-aaa111",
                "SA1_2-bbb222",
                "cross-run message",
            )

    def test_terminal_status_is_absorbing(self):
        run = self.registry.create_run("chat-a")
        agent = make_agent("SA1_1-aaa111", "chat-a", run.run_id)
        self.registry.register(agent)
        self.registry.update_status(
            agent.agent_name,
            SwarmAgentStatus.CANCELLED,
        )
        self.registry.update_status(
            agent.agent_name,
            SwarmAgentStatus.DONE,
        )
        self.assertEqual(
            self.registry.get_agent(agent.agent_name).status,
            SwarmAgentStatus.CANCELLED,
        )

    def test_clear_completed_only_affects_selected_parent(self):
        first = self.registry.create_run("chat-a")
        second = self.registry.create_run("chat-b")
        self.registry.register(
            make_agent(
                "SA1_1-aaa111",
                "chat-a",
                first.run_id,
                SwarmAgentStatus.DONE,
            )
        )
        self.registry.register(
            make_agent(
                "SA1_1-bbb222",
                "chat-b",
                second.run_id,
                SwarmAgentStatus.DONE,
            )
        )

        self.registry.clear_completed("chat-a")
        self.assertEqual(self.registry.snapshot("chat-a")["agents"], [])
        self.assertEqual(len(self.registry.snapshot("chat-b")["agents"]), 1)

    def test_concurrent_registration_and_snapshot_are_safe(self):
        run = self.registry.create_run("chat-a")
        failures = []

        def worker(index: int):
            try:
                name = f"SA1_{index + 1}-thread"
                self.registry.register(make_agent(name, "chat-a", run.run_id))
                self.registry.snapshot("chat-a")
            except Exception as exc:  # pragma: no cover - diagnostic capture
                failures.append(exc)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(failures, [])
        self.assertEqual(len(self.registry.snapshot("chat-a")["agents"]), 8)


if __name__ == "__main__":
    unittest.main()
