# GitHub Actions PR Gate

Attestflow core does not depend on GitHub, but an open-core repository can still publish a reusable GitHub Actions example.

Use `examples/github-actions/attestflow-pr.yml` as a starting point. The workflow does three deterministic checks:

1. Installs the local package.
2. Runs `python -m attestflow verify`.
3. Exports every completed task with `python -m attestflow evidence export TASK-* --out ...`.

The workflow exits with `1` when no completed task evidence exists, so a PR cannot pass without an auditable Attestflow evidence bundle. The final bundle is uploaded with `actions/upload-artifact`.
