from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import attestflow.cli as cli
from attestflow.cli import cmd_init
from attestflow.io import dump_data, load_data


ROOT = Path(__file__).resolve().parents[2]


class OpenSourceP1Tests(unittest.TestCase):
    def test_contract_validate_cli_accepts_valid_output_and_reports_specific_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "capability-output.json"
            invalid = root / "invalid-capability-output.json"
            dump_data(
                {
                    "schema_version": 1,
                    "status": "passed",
                    "summary": "review passed",
                    "findings": [],
                    "evidence": [],
                },
                valid,
            )
            dump_data({"schema_version": 1, "status": "maybe", "summary": ""}, invalid)

            output = io.StringIO()
            with redirect_stdout(output):
                valid_exit = cli.main(["contract", "validate", "capability-output", str(valid)])

            error = io.StringIO()
            with redirect_stderr(error):
                invalid_exit = cli.main(["contract", "validate", "capability-output", str(invalid)])

            self.assertEqual(valid_exit, 0)
            self.assertIn("contract capability-output valid", output.getvalue())
            self.assertEqual(invalid_exit, 1)
            self.assertIn("capability-output.status must be one of: passed, failed, blocked", error.getvalue())
            self.assertIn("capability-output.summary must be non-empty", error.getvalue())

    def test_contract_validate_covers_core_provider_output_types(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = {
                "planner-output": {
                    "schema_version": 1,
                    "goal": "ship",
                    "tasks": [
                        {
                            "title": "Task",
                            "purpose": "Ship a scoped task.",
                            "scope": ["implementation"],
                            "out_of_scope": ["unrelated work"],
                            "requirements": {"confirmed": ["needed"], "unresolved": [], "assumptions": []},
                            "bdd_scenarios": ["Given input, when run, then output is correct."],
                            "unit_tests": ["unit tests pass"],
                            "acceptance": ["task is done"],
                            "dependencies": [],
                            "files": {"read": [], "write": ["README.md"]},
                        }
                    ],
                },
                "session-launch-output": {"schema_version": 1, "status": "launched", "summary": "started"},
                "session-resume-output": {"schema_version": 1, "status": "resumed", "summary": "continued"},
                "git-output": {
                    "schema_version": 1,
                    "status": "published",
                    "summary": "published",
                    "branch": "codex/publish",
                    "commit_before": "abc",
                    "commit_after": "def",
                    "pushed": True,
                    "changes": [],
                },
                "ci-output": {"schema_version": 1, "status": "passed", "summary": "CI passed", "checks": []},
                "pr-output": {"schema_version": 1, "status": "open", "summary": "PR open", "checks": []},
                "release-output": {"schema_version": 1, "status": "released", "summary": "released", "artifacts": []},
            }
            for contract_type, payload in fixtures.items():
                path = root / f"{contract_type}.json"
                dump_data(payload, path)
                with self.subTest(contract_type=contract_type):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        exit_code = cli.main(["contract", "validate", contract_type, str(path)])
                    self.assertEqual(exit_code, 0)
                    self.assertIn(f"contract {contract_type} valid", output.getvalue())

    def test_contract_validate_accepts_provider_usage_and_rejects_invalid_token_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "capability-output.json"
            invalid = root / "invalid-capability-output.json"
            dump_data(
                {
                    "schema_version": 1,
                    "status": "passed",
                    "summary": "review passed",
                    "findings": [],
                    "evidence": [],
                    "usage": {
                        "provider": "codex",
                        "model": "gpt-5",
                        "input_tokens": 1200,
                        "output_tokens": 300,
                        "total_tokens": 1500,
                        "cost_usd": 0.0123,
                    },
                },
                valid,
            )
            dump_data(
                {
                    "schema_version": 1,
                    "status": "passed",
                    "summary": "review passed",
                    "findings": [],
                    "evidence": [],
                    "usage": {"input_tokens": -1},
                },
                invalid,
            )

            output = io.StringIO()
            with redirect_stdout(output):
                valid_exit = cli.main(["contract", "validate", "capability-output", str(valid)])

            error = io.StringIO()
            with redirect_stderr(error):
                invalid_exit = cli.main(["contract", "validate", "capability-output", str(invalid)])

            self.assertEqual(valid_exit, 0)
            self.assertEqual(invalid_exit, 1)
            self.assertIn("capability-output.usage.input_tokens must be a non-negative integer", error.getvalue())

    def test_contract_validate_task_uses_runtime_task_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            task_path = Path(tmp) / "TASK-0001.json"
            dump_data({"schema_version": 1, "id": "TASK-0001", "state": "ready"}, task_path)

            error = io.StringIO()
            with redirect_stderr(error):
                exit_code = cli.main(["contract", "validate", "task", str(task_path)])

            self.assertEqual(exit_code, 1)
            self.assertIn("task.missing required fields", error.getvalue())

    def test_evidence_export_writes_task_bundle_manifest_and_referenced_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_harness_config(root)
            _write_done_task_with_evidence(root)
            output_dir = root / "artifacts" / "TASK-0001"

            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["evidence", "export", "TASK-0001", "--out", str(output_dir)])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("exported evidence TASK-0001", output.getvalue())
            manifest = load_data(output_dir / "manifest.json")
            self.assertEqual(manifest["task_id"], "TASK-0001")
            self.assertEqual(manifest["run_id"], "run-1")
            self.assertIn("task.json", manifest["files"])
            self.assertTrue((output_dir / "task.json").exists())
            self.assertTrue((output_dir / "runs" / "run-1" / "evidence.md").exists())
            self.assertTrue((output_dir / "runs" / "run-1" / "metadata.yml").exists())
            self.assertTrue((output_dir / "runs" / "run-1" / "ledger.jsonl").exists())
            self.assertTrue((output_dir / "capability-runs" / "reviewer-TASK-0001" / "output.json").exists())

    def test_init_supports_go_and_rust_adapters_with_default_commands(self) -> None:
        with TemporaryDirectory() as go_tmp, TemporaryDirectory() as rust_tmp:
            go_root = Path(go_tmp)
            rust_root = Path(rust_tmp)
            (go_root / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
            (rust_root / "Cargo.toml").write_text("[package]\nname = \"demo\"\nversion = \"0.1.0\"\n", encoding="utf-8")

            go_exit = cmd_init(SimpleNamespace(path=str(go_root), adapter="go", agent_provider="command", agent_command=None))
            rust_exit = cmd_init(
                SimpleNamespace(path=str(rust_root), adapter="rust", agent_provider="command", agent_command=None)
            )
            go_config = load_data(go_root / "harness.yml")
            rust_config = load_data(rust_root / "harness.yml")

            self.assertEqual(go_exit, 0)
            self.assertEqual(go_config["project"]["adapter"], "go")
            self.assertEqual(go_config["project"]["module"], "go")
            self.assertEqual(go_config["commands"]["unit"], "go test ./...")
            self.assertTrue((go_root / "harness" / "adapters" / "go" / "README.md").exists())
            self.assertEqual(rust_exit, 0)
            self.assertEqual(rust_config["project"]["adapter"], "rust")
            self.assertEqual(rust_config["project"]["module"], "rust")
            self.assertEqual(rust_config["commands"]["unit"], "cargo test")
            self.assertEqual(rust_config["commands"]["typecheck"], "cargo check --all-targets --all-features")
            self.assertTrue((rust_root / "harness" / "adapters" / "rust" / "README.md").exists())

    def test_init_supports_monorepo_pnpm_turbo_and_nx_adapter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n", encoding="utf-8")
            (root / "turbo.json").write_text('{"tasks": {"build": {}}}\n', encoding="utf-8")
            (root / "nx.json").write_text('{"tasksRunnerOptions": {}}\n', encoding="utf-8")
            (root / "package.json").write_text(
                '{"scripts":{"test":"vitest run","lint":"eslint .","typecheck":"tsc --noEmit","build":"turbo build"}}\n',
                encoding="utf-8",
            )

            exit_code = cmd_init(SimpleNamespace(path=str(root), adapter="monorepo", agent_provider="command", agent_command=None))
            config = load_data(root / "harness.yml")

            self.assertEqual(exit_code, 0)
            self.assertEqual(config["project"]["adapter"], "monorepo")
            self.assertEqual(config["project"]["package_manager"], "pnpm")
            self.assertEqual(config["project"]["workspace_tools"], ["pnpm-workspace", "turborepo", "nx"])
            self.assertEqual(config["commands"]["unit"], "pnpm -r test")
            self.assertEqual(config["commands"]["lint"], "pnpm -r run lint")
            self.assertEqual(config["commands"]["typecheck"], "pnpm -r run typecheck")
            self.assertEqual(config["commands"]["project_verify"], "pnpm -r run build")
            self.assertTrue((root / "harness" / "adapters" / "monorepo" / "README.md").exists())

    def test_init_supports_docker_and_bazel_adapters_with_default_commands(self) -> None:
        with TemporaryDirectory() as docker_tmp, TemporaryDirectory() as bazel_tmp:
            docker_root = Path(docker_tmp)
            bazel_root = Path(bazel_tmp)
            (docker_root / "Dockerfile").write_text("FROM python:3.13-slim\n", encoding="utf-8")
            (docker_root / "compose.yaml").write_text("services:\n  app:\n    build: .\n", encoding="utf-8")
            (bazel_root / "MODULE.bazel").write_text('module(name = "demo")\n', encoding="utf-8")
            (bazel_root / "BUILD.bazel").write_text("# build targets\n", encoding="utf-8")

            docker_exit = cmd_init(
                SimpleNamespace(path=str(docker_root), adapter="docker", agent_provider="command", agent_command=None)
            )
            bazel_exit = cmd_init(
                SimpleNamespace(path=str(bazel_root), adapter="bazel", agent_provider="command", agent_command=None)
            )
            docker_config = load_data(docker_root / "harness.yml")
            bazel_config = load_data(bazel_root / "harness.yml")

            self.assertEqual(docker_exit, 0)
            self.assertEqual(docker_config["project"]["adapter"], "docker")
            self.assertEqual(docker_config["project"]["container"], "docker")
            self.assertEqual(docker_config["project"]["compose_file"], "compose.yaml")
            self.assertTrue(docker_config["execution"]["docker"]["enabled"])
            self.assertTrue(docker_config["policies"]["docker_required"])
            self.assertEqual(docker_config["commands"]["project_verify"], "docker build .")
            self.assertTrue((docker_root / "harness" / "adapters" / "docker" / "README.md").exists())
            self.assertEqual(bazel_exit, 0)
            self.assertEqual(bazel_config["project"]["adapter"], "bazel")
            self.assertEqual(bazel_config["project"]["build_system"], "bazel")
            self.assertEqual(bazel_config["commands"]["unit"], "bazel test //...")
            self.assertEqual(bazel_config["commands"]["project_verify"], "bazel build //...")
            self.assertTrue((bazel_root / "harness" / "adapters" / "bazel" / "README.md").exists())

    def test_init_supports_common_language_adapters_with_default_commands(self) -> None:
        scenarios = {
            "java": {
                "files": {"pom.xml": "<project></project>\n"},
                "project": {"build_tool": "maven"},
                "commands": {"unit": "mvn test", "project_verify": "mvn verify"},
            },
            "kotlin": {
                "files": {"build.gradle.kts": "plugins { kotlin(\"jvm\") version \"2.0.0\" }\n", "gradlew": "#!/bin/sh\n"},
                "project": {"build_tool": "gradle"},
                "commands": {"unit": "./gradlew test", "project_verify": "./gradlew build"},
            },
            "dotnet": {
                "files": {"demo.csproj": "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n"},
                "project": {"module": "dotnet"},
                "commands": {"unit": "dotnet test", "project_verify": "dotnet build"},
            },
            "swift": {
                "files": {"Package.swift": "// swift-tools-version: 6.0\n"},
                "project": {"module": "swift"},
                "commands": {"unit": "swift test", "project_verify": "swift build"},
            },
            "dart": {
                "files": {"pubspec.yaml": "name: demo\n"},
                "project": {"module": "dart"},
                "commands": {"unit": "dart test", "typecheck": "dart analyze"},
            },
            "ruby": {
                "files": {"Gemfile": "source 'https://rubygems.org'\n", "Rakefile": "task :test\n"},
                "project": {"module": "ruby"},
                "commands": {"unit": "bundle exec rake test", "project_verify": "bundle exec rake"},
            },
            "php": {
                "files": {"composer.json": '{"scripts":{"test":"phpunit"}}\n'},
                "project": {"module": "php"},
                "commands": {"unit": "composer test", "project_verify": "composer validate"},
            },
        }
        for adapter, scenario in scenarios.items():
            with self.subTest(adapter=adapter), TemporaryDirectory() as tmp:
                root = Path(tmp)
                for relative, content in scenario["files"].items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content, encoding="utf-8")

                exit_code = cmd_init(
                    SimpleNamespace(path=str(root), adapter=adapter, agent_provider="command", agent_command=None)
                )
                config = load_data(root / "harness.yml")

                self.assertEqual(exit_code, 0)
                self.assertEqual(config["project"]["adapter"], adapter)
                for key, value in scenario["project"].items():
                    self.assertEqual(config["project"][key], value)
                for key, value in scenario["commands"].items():
                    self.assertEqual(config["commands"][key], value)
                self.assertTrue((root / "harness" / "adapters" / adapter / "README.md").exists())

    def test_init_parser_accepts_all_builtin_adapters(self) -> None:
        adapters = {
            "generic",
            "python",
            "node",
            "go",
            "rust",
            "monorepo",
            "docker",
            "bazel",
            "java",
            "kotlin",
            "dotnet",
            "swift",
            "dart",
            "ruby",
            "php",
        }
        parser = cli.build_parser()

        for adapter in adapters:
            with self.subTest(adapter=adapter):
                args = parser.parse_args(["init", "--path", ".", "--adapter", adapter])
                self.assertEqual(args.adapter, adapter)

    def test_github_actions_pr_example_blocks_without_evidence_and_uploads_bundle(self) -> None:
        workflow = ROOT / "examples" / "github-actions" / "attestflow-pr.yml"
        self.assertTrue(workflow.exists())
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("attestflow verify", text)
        self.assertIn("attestflow evidence export", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("No completed Attestflow task evidence found", text)
        self.assertIn("exit 1", text)

    def test_autopilot_status_prints_next_command_for_recovery_states(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_harness_config(root)
            metadata_path = root / "harness" / "autopilot-runs" / "run-1" / "metadata.json"
            metadata_path.parent.mkdir(parents=True)
            dump_data(
                {
                    "run_id": "run-1",
                    "status": "paused",
                    "pause_reason": "max_steps reached",
                    "steps": 1,
                    "blocked": [],
                    "failed": [],
                },
                metadata_path,
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["autopilot", "--status"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            self.assertIn("next:", output.getvalue())
            self.assertIn("python -m attestflow autopilot --resume", output.getvalue())

    def test_install_smoke_cli_verifies_templates_path_and_offline_init(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = cli.main(
                [
                    "install-smoke",
                    "--offline",
                    "--check-template-mirror",
                    "--skip-path-check",
                ]
            )

        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("install smoke passed", text)
        self.assertIn("offline", text)
        self.assertIn("template mirror", text)

    def test_install_smoke_cli_fails_when_console_script_is_not_on_path(self) -> None:
        original_path = os.environ.get("PATH")
        os.environ["PATH"] = ""
        try:
            error = io.StringIO()
            with redirect_stderr(error):
                exit_code = cli.main(["install-smoke", "--offline"])
        finally:
            if original_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = original_path

        self.assertEqual(exit_code, 1)
        self.assertIn("attestflow console script was not found on PATH", error.getvalue())

    def test_ci_platform_install_matrix_covers_required_modes(self) -> None:
        workflow = ROOT / ".github" / "workflows" / "ci.yml"
        text = workflow.read_text(encoding="utf-8")

        self.assertIn("runs-on: ${{ matrix.os }}", text)
        for os_name in ("ubuntu-latest", "macos-latest", "windows-latest"):
            self.assertIn(os_name, text)
        for install_mode in ("source", "venv", "wheel", "pipx", "uv", "pypi"):
            self.assertIn(f"install-mode: {install_mode}", text)
        self.assertIn("attestflow install-smoke --offline", text)
        self.assertIn("attestflow install-smoke --check-template-mirror", text)
        self.assertIn('PATH="$PWD/.venv/bin:$PATH" attestflow install-smoke --offline', text)
        self.assertIn('PIPX_BIN_DIR="$(python -m pipx environment --value PIPX_BIN_DIR)"', text)
        self.assertIn('PATH="$PIPX_BIN_DIR:$PATH" attestflow install-smoke --offline', text)

    def test_package_data_explicitly_includes_dot_github_template(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("templates/base/.github/workflows/*.yml", pyproject)

    def test_template_mirror_check_uses_checkout_templates_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_template = root / "templates" / "base" / "harness.yml"
            source_template.parent.mkdir(parents=True)
            source_template.write_text("schema_version: 999\n", encoding="utf-8")
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                errors = cli._template_mirror_errors()
            finally:
                os.chdir(original_cwd)

        self.assertTrue(any("base/harness.yml" in error for error in errors), errors)


def _write_harness_config(root: Path) -> None:
    dump_data(
        {
            "schema_version": 1,
            "project": {"name": "demo", "adapter": "generic"},
            "paths": {
                "tasks": "harness/tasks",
                "runs": "harness/runs",
                "capability_runs": "harness/capability-runs",
                "autopilot_runs": "harness/autopilot-runs",
            },
            "commands": {},
            "policies": {},
        },
        root / "harness.yml",
    )


def _write_done_task_with_evidence(root: Path) -> None:
    task_dir = root / "harness" / "tasks" / "done"
    task_dir.mkdir(parents=True)
    run_dir = root / "harness" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    capability_dir = root / "harness" / "capability-runs" / "reviewer-TASK-0001"
    capability_dir.mkdir(parents=True)
    (run_dir / "evidence.md").write_text("# Evidence\n\n- ID: TASK-0001\n- Run: run-1\n", encoding="utf-8")
    dump_data({"run_id": "run-1", "task_id": "TASK-0001", "status": "closed"}, run_dir / "metadata.yml")
    (run_dir / "ledger.jsonl").write_text('{"event":"closed"}\n', encoding="utf-8")
    dump_data(
        {"schema_version": 1, "status": "passed", "summary": "review passed", "findings": [], "evidence": []},
        capability_dir / "output.json",
    )
    dump_data(
        {
            "schema_version": 1,
            "id": "TASK-0001",
            "title": "Ship feature",
            "state": "done",
            "priority": 100,
            "type": "feature",
            "purpose": "Ship a feature",
            "scope": ["implement feature"],
            "out_of_scope": ["unrelated work"],
            "requirements": {"confirmed": ["feature is needed"], "unresolved": [], "assumptions": []},
            "bdd_scenarios": ["Given a user, when they run it, then it works"],
            "unit_tests": ["unit tests pass"],
            "acceptance": ["feature is done"],
            "dependencies": [],
            "files": {"read": [], "write": ["feature.py"]},
            "agents": {"owner": "orchestrator", "allowed_roles": []},
            "external_inputs": {"credentials": [], "services": [], "user_decisions": []},
            "evidence": {
                "run_id": "run-1",
                "packet": "harness/runs/run-1/evidence.md",
                "verify": "harness/runs/run-1/metadata.yml",
                "capabilities": {"reviewer": "harness/capability-runs/reviewer-TASK-0001/output.json"},
            },
            "links": {"issues": [], "prs": [], "docs": []},
            "risks": [],
            "notes": [],
            "created_at": "2026-05-31T00:00:00+00:00",
            "updated_at": "2026-05-31T00:00:00+00:00",
        },
        task_dir / "TASK-0001.json",
    )


if __name__ == "__main__":
    unittest.main()
