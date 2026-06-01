from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from attestflow.io import load_data


ROOT = Path(__file__).resolve().parents[2]


class BilingualOnboardingTests(unittest.TestCase):
    def test_github_readme_defaults_to_english_and_links_chinese(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese_readme = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        self.assertIn("Attestflow", readme)
        self.assertIn("[Chinese README](README.zh-CN.md)", readme)
        self.assertIn("Reusable development harness", readme)
        self.assertNotIn("Attestflow 的目标", readme)
        self.assertIn("Attestflow 的目标", chinese_readme)

    def test_bootstrap_script_initializes_english_harness_with_defaults(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            target.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["ATTESTFLOW_SKIP_INSTALL"] = "1"

            result = subprocess.run(
                [
                    bash,
                    str(ROOT / "scripts" / "bootstrap.sh"),
                    "--path",
                    str(target),
                    "--yes",
                    "--adapter",
                    "generic",
                    "--agent-provider",
                    "command",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("doctor passed", result.stdout)
            config = load_data(target / "harness.yml")
            self.assertEqual(config["project"]["language"], "en")
            self.assertEqual(config["project"]["adapter"], "generic")

    def test_bootstrap_script_passes_selected_chinese_language_to_init(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            self.skipTest("bash is not available")
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            target.mkdir()
            (target / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["ATTESTFLOW_SKIP_INSTALL"] = "1"

            result = subprocess.run(
                [
                    bash,
                    str(ROOT / "scripts" / "bootstrap.sh"),
                    "--path",
                    str(target),
                    "--language",
                    "zh-CN",
                    "--yes",
                    "--adapter",
                    "auto",
                    "--agent-provider",
                    "command",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = load_data(target / "harness.yml")
            self.assertEqual(config["project"]["language"], "zh-CN")
            self.assertEqual(config["project"]["adapter"], "python")
            self.assertTrue((target / "harness" / "adapters" / "python" / "README.md").exists())
            self.assertTrue((target / "harness" / "adapters" / "python" / "README.zh-CN.md").exists())

    def test_bootstrap_script_runs_under_zsh_when_available(self) -> None:
        zsh = shutil.which("zsh")
        if zsh is None:
            self.skipTest("zsh is not available")
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            target.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["ATTESTFLOW_SKIP_INSTALL"] = "1"

            result = subprocess.run(
                [
                    zsh,
                    str(ROOT / "scripts" / "bootstrap.sh"),
                    "--path",
                    str(target),
                    "--language",
                    "en",
                    "--yes",
                    "--adapter",
                    "generic",
                    "--agent-provider",
                    "command",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            config = load_data(target / "harness.yml")
            self.assertEqual(config["project"]["language"], "en")


if __name__ == "__main__":
    unittest.main()
