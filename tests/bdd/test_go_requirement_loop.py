from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from attestflow import cli


class GoRequirementLoopScenarioTests(unittest.TestCase):
    def test_raw_inline_requirement_creates_draft_spec_and_stops_for_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = cli.ROOT
            cli.ROOT = root
            stdout = io.StringIO()
            try:
                with redirect_stdout(stdout):
                    exit_code = cli.main(["go", "实现登录功能"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 2)
            self.assertIn("spec approval required", stdout.getvalue())
            self.assertTrue((root / "harness/specs/SPEC-0001/spec.md").exists())


if __name__ == "__main__":
    unittest.main()
