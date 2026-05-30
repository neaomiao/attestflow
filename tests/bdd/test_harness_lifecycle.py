from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow.config import DEFAULT_CONFIG
from attestflow.tasks import select_next_task, start_task
from tests.unit.test_task_lifecycle import ready_task, write_task


class HarnessLifecycleScenarioTests(unittest.TestCase):
    def test_scenario_ready_task_can_be_selected_and_started(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = DEFAULT_CONFIG.copy()
            config["root"] = root

            write_task(root, "ready", "TASK-0001", ready_task("TASK-0001"))

            selected = select_next_task(root, config)
            self.assertIsNotNone(selected)
            self.assertEqual(selected.task["id"], "TASK-0001")

            run = start_task(root, config, "TASK-0001", actor_role="orchestrator")

            self.assertTrue(run.path.exists())
            self.assertTrue((root / "harness" / "tasks" / "in_progress" / "TASK-0001.json").exists())


if __name__ == "__main__":
    unittest.main()
