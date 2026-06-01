from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import attestflow.cli as cli
from attestflow.context import resolve_dynamic_context_request
from attestflow.io import dump_data, load_data
from attestflow.token_economy import build_incremental_context, enforce_payload_budget, summarize_evidence_reference
from attestflow.usage import build_usage_report


class TokenEconomyTests(unittest.TestCase):
    def test_budget_gate_summarizes_large_context_and_writes_context_cache(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "schema_version": 1,
                "repository_context": {
                    "enabled": True,
                    "documents": [{"path": "README.md", "content": "alpha\n" * 2000, "truncated": False}],
                    "files": [{"path": "src/app.py", "content": "def run():\n    return 'ok'\n" * 1000, "truncated": False}],
                    "tree": ["README.md", "src/app.py"],
                    "dynamic_context": {"allowed_requests": ["file_slice"]},
                },
            }
            config = {
                "token_economy": {
                    "budgets": {"default_input_tokens": 300},
                    "context_cache": {"enabled": True, "path": "harness/context-cache"},
                }
            }

            optimized = enforce_payload_budget(root, config, "reviewer", payload)

            self.assertTrue(optimized["token_economy"]["budget_exceeded"])
            self.assertLess(
                optimized["token_economy"]["estimated_input_tokens"],
                optimized["token_economy"]["estimated_input_tokens_before"],
            )
            document = optimized["repository_context"]["documents"][0]
            self.assertNotIn("content", document)
            self.assertIn("summary", document)
            self.assertIn("cache_key", document)
            cache_file = root / "harness" / "context-cache" / f"{document['cache_key']}.json"
            self.assertTrue(cache_file.exists())
            self.assertIn("alpha", load_data(cache_file)["summary"])

    def test_dynamic_context_resolves_file_slices_symbols_and_semantic_search(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "payments.py").write_text(
                "\n".join(
                    [
                        "import json",
                        "",
                        "class PaymentService:",
                        "    def charge(self):",
                        "        return json.dumps({'ok': True})",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            file_slice = resolve_dynamic_context_request(
                root,
                {"context": {"max_tree_entries": 20, "max_file_bytes": 1000}},
                {"request_id": "r1", "type": "file_slice", "path": "src/payments.py", "start_line": 3, "end_line": 5},
            )
            symbol = resolve_dynamic_context_request(
                root,
                {"context": {"max_tree_entries": 20, "max_file_bytes": 1000}},
                {"request_id": "r2", "type": "symbol_lookup", "name": "PaymentService"},
            )
            search = resolve_dynamic_context_request(
                root,
                {"context": {"max_tree_entries": 20, "max_file_bytes": 1000}},
                {"request_id": "r3", "type": "semantic_search", "query": "payment service"},
            )

            self.assertEqual(file_slice["status"], "passed")
            self.assertIn("PaymentService", file_slice["items"][0]["content"])
            self.assertEqual(symbol["items"][0]["name"], "PaymentService")
            self.assertEqual(search["items"][0]["path"], "src/payments.py")

    def test_usage_report_aggregates_capability_and_session_usage(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_data(
                {"provider": "codex", "model": "gpt-5", "input_tokens": 100, "output_tokens": 25, "total_tokens": 125, "cost_usd": 0.12},
                root / "harness" / "capability-runs" / "reviewer-TASK-1" / "usage.json",
            )
            dump_data(
                {"provider": "codex", "model": "gpt-5", "input_tokens": 40, "output_tokens": 10, "total_tokens": 50, "cached_input_tokens": 20},
                root / "harness" / "runs" / "run-1" / "session-launch-usage.json",
            )

            report = build_usage_report(root, {"paths": {"capability_runs": "harness/capability-runs", "runs": "harness/runs"}})

            self.assertEqual(report["totals"]["input_tokens"], 140)
            self.assertEqual(report["totals"]["output_tokens"], 35)
            self.assertEqual(report["totals"]["total_tokens"], 175)
            self.assertEqual(report["totals"]["cached_input_tokens"], 20)
            self.assertAlmostEqual(report["totals"]["cost_usd"], 0.12)
            self.assertEqual(report["by_provider_model"]["codex/gpt-5"]["total_tokens"], 175)

    def test_usage_report_cli_outputs_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_data(
                {"provider": "codex", "model": "gpt-5", "input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                root / "harness" / "capability-runs" / "planner-1" / "usage.json",
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["usage", "report", "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["totals"]["total_tokens"], 15)

    def test_context_resolve_cli_outputs_requested_fragment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
            request = root / "request.json"
            dump_data(
                {"request_id": "slice-1", "type": "file_slice", "path": "src/app.py", "start_line": 2, "end_line": 2},
                request,
            )
            original_root = cli.ROOT
            cli.ROOT = root
            try:
                output = io.StringIO()
                with redirect_stdout(output):
                    exit_code = cli.main(["context", "resolve", "--from-json", str(request), "--json"])
            finally:
                cli.ROOT = original_root

            self.assertEqual(exit_code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["items"][0]["content"], "two\n")

    def test_incremental_context_summarizes_prior_capability_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_data(
                {"schema_version": 1, "status": "failed", "summary": "Tests still fail", "findings": [{"severity": "high"}]},
                root / "harness" / "capability-runs" / "reviewer-TASK-1" / "output.json",
            )
            task = {
                "id": "TASK-1",
                "files": {"read": ["src/app.py"], "write": ["src/app.py"]},
                "evidence": {"capabilities": {"reviewer": "harness/capability-runs/reviewer-TASK-1/output.json"}},
            }

            context = build_incremental_context(root, {"token_economy": {"incremental_context": {"enabled": True}}}, task)

            self.assertTrue(context["enabled"])
            self.assertEqual(context["focus_files"], ["src/app.py"])
            self.assertEqual(context["previous_capabilities"][0]["capability"], "reviewer")
            self.assertEqual(context["previous_capabilities"][0]["status"], "failed")
            self.assertEqual(context["previous_capabilities"][0]["findings_count"], 1)

    def test_evidence_summary_keeps_status_without_large_raw_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dump_data(
                {
                    "schema_version": 1,
                    "status": "passed",
                    "summary": "CI passed",
                    "logs": "line\n" * 2000,
                    "checks": [{"name": "unit", "status": "passed"}],
                },
                root / "harness" / "ci-runs" / "ci-1" / "output.json",
            )

            summary = summarize_evidence_reference(
                root,
                "harness/ci-runs/ci-1/output.json",
                {"token_economy": {"evidence_summary": {"enabled": True, "max_output_bytes": 120}}},
            )

            self.assertEqual(summary["output_summary"]["status"], "passed")
            self.assertEqual(summary["output_summary"]["checks_count"], 1)
            self.assertNotIn("logs", summary["output_summary"])
            self.assertNotIn("output", summary)


if __name__ == "__main__":
    unittest.main()
