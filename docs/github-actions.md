# GitHub Actions PR Gate

Attestflow core does not depend on GitHub, but an open-core repository can still publish a reusable GitHub Actions example.

Use `examples/github-actions/attestflow-pr.yml` as a starting point. The workflow does three deterministic checks:

1. Installs the local package.
2. Runs `python -m attestflow verify`.
3. Exports every completed task with `python -m attestflow evidence export TASK-* --out ...`.

The workflow exits with `1` when no completed task evidence exists, so a PR cannot pass without an auditable Attestflow evidence bundle. The final bundle is uploaded with `actions/upload-artifact`.

The repository CI also runs an install matrix before release hardening:

- macOS, Linux, and Windows run `python -m attestflow verify` after a normal source install.
- Linux covers local venv, pipx, uv, and source installs.
- macOS and Windows cover built wheel install.
- Tag or manual runs cover `pip install attestflow` from PyPI.
- Every install path runs `attestflow install-smoke --offline`; the source path also runs `--check-template-mirror` to catch drift between source templates and packaged templates.
