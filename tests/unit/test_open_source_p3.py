from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.config import DEFAULT_CONFIG, load_config
from attestflow.io import dump_data, load_data


class OpenSourceP3Tests(unittest.TestCase):
    def test_schema_migrate_reports_legacy_config_changes_and_can_write_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = root / "legacy-harness.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "project": {"name": "legacy-project"},
                        "paths": {"tasks": "tasks"},
                        "commands": {"unit": "python -m unittest"},
                        "policies": {},
                    }
                ),
                encoding="utf-8",
            )

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(
                        ["schema", "migrate", "--kind", "harness-config", "--from-json", str(legacy_path), "--write", "--json"]
                    )
            finally:
                cli.ROOT = original_root

            payload = json.loads(output.getvalue())
            migrated = load_data(legacy_path)
            self.assertEqual(exit_code, 0)
            self.assertTrue(payload["changed"])
            self.assertEqual(payload["kind"], "harness-config")
            self.assertIn("add schema_version", payload["migrations"])
            self.assertEqual(payload["output"]["schema_version"], 1)
            self.assertEqual(payload["output"]["paths"]["tasks"], "tasks")
            self.assertEqual(payload["output"]["paths"]["runs"], "harness/runs")
            self.assertEqual(migrated["schema_version"], 1)
            self.assertEqual(migrated["paths"]["sources"], "harness/sources")

    def test_contract_version_is_validated_and_json_schema_can_be_exported(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            invalid = root / "ci-invalid.json"
            valid = root / "ci-valid.json"
            invalid.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "contract_version": 99,
                        "status": "passed",
                        "summary": "ci passed",
                        "checks": [],
                    }
                ),
                encoding="utf-8",
            )
            valid.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "contract_version": 1,
                        "status": "passed",
                        "summary": "ci passed",
                        "checks": [],
                    }
                ),
                encoding="utf-8",
            )

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                error = io.StringIO()
                with redirect_stderr(error):
                    invalid_exit = cli.main(["contract", "validate", "ci-output", str(invalid)])
                valid_output = io.StringIO()
                with redirect_stdout(valid_output):
                    valid_exit = cli.main(["contract", "validate", "ci-output", str(valid)])
                schema_output = io.StringIO()
                with redirect_stdout(schema_output):
                    schema_exit = cli.main(["schema", "export", "--type", "ci-output", "--json"])
            finally:
                cli.ROOT = original_root

            schema = json.loads(schema_output.getvalue())
            self.assertEqual(invalid_exit, 1)
            self.assertIn("contract_version must be 1", error.getvalue())
            self.assertEqual(valid_exit, 0)
            self.assertEqual(schema_exit, 0)
            self.assertEqual(schema["title"], "Attestflow ci-output")
            self.assertEqual(schema["properties"]["contract_version"]["const"], 1)
            self.assertIn("usage", schema["properties"])
            self.assertEqual(schema["properties"]["usage"]["properties"]["input_tokens"]["minimum"], 0)
            self.assertIn("schema_version", schema["required"])
            self.assertIn("status", schema["required"])

    def test_openapi_export_and_plugin_registry_are_machine_readable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)
            plugin_dir = root / "harness" / "plugins" / "demo"
            plugin_dir.mkdir(parents=True)
            dump_data(
                {
                    "schema_version": 1,
                    "name": "demo-plugin",
                    "version": "0.1.0",
                    "capabilities": ["planner"],
                    "providers": {"session": ["demo-agent"]},
                    "adapters": ["python"],
                },
                plugin_dir / "plugin.json",
            )

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                plugin_output = io.StringIO()
                with redirect_stdout(plugin_output):
                    plugin_exit = cli.main(["plugin", "list", "--json"])
                openapi_output = io.StringIO()
                with redirect_stdout(openapi_output):
                    openapi_exit = cli.main(["schema", "openapi", "--json"])
            finally:
                cli.ROOT = original_root

            plugins = json.loads(plugin_output.getvalue())
            openapi = json.loads(openapi_output.getvalue())
            self.assertEqual(plugin_exit, 0)
            self.assertEqual(openapi_exit, 0)
            self.assertEqual(plugins["plugins"][0]["name"], "demo-plugin")
            self.assertEqual(plugins["plugins"][0]["manifest"], "harness/plugins/demo/plugin.json")
            self.assertEqual(openapi["openapi"], "3.1.0")
            self.assertIn("ci-output", openapi["components"]["schemas"])
            self.assertIn("task", openapi["components"]["schemas"])

    def test_governance_policy_reports_release_and_breaking_change_rules(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _configured_project(root)

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["governance", "policy", "--json"])
            finally:
                cli.ROOT = original_root

            policy = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(policy["provider_contract_version"], 1)
            self.assertEqual(policy["supported_schema_versions"], [1])
            self.assertIn("contract suite", " ".join(policy["stable_release_flow"]))
            self.assertIn("migration", policy["pre_1_0_breaking_changes"])
            self.assertIn("compatibility", policy["backward_compatibility"])


def _configured_project(root: Path) -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    for name in config["commands"]:
        config["commands"][name] = None
    dump_data(config, root / "harness.yml")
    return load_config(root)


if __name__ == "__main__":
    unittest.main()
