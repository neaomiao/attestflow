# GitHub Actions PR Gate

[中文](github-actions.zh-CN.md)

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

## Runtime integration

Configure the built-in preset when Attestflow should read or act on GitHub Actions evidence:

```yaml
integrations:
  ci_provider:
    provider: github-actions
    provider_options:
      repository: owner/repo
      branch: feature/my-change
      head_sha: abc123
      workflow: ci.yml
      event: pull_request
```

Supported actions:

```bash
python -m attestflow ci status --head-sha abc123 --branch feature/my-change --workflow ci.yml
python -m attestflow ci await --head-sha abc123 --max-wait-seconds 600
python -m attestflow ci logs --run-id 123456789
python -m attestflow ci artifacts --run-id 123456789 --download-dir attestflow-artifacts
python -m attestflow ci rerun --run-id 123456789 --failed
python -m attestflow ci dispatch --workflow ci.yml --ref feature/my-change --input task=TASK-0001
```

`status` no longer depends on whichever workflow run happens to be newest. Use branch, head SHA, workflow, event, or run id to bind CI evidence to the exact PR or commit being shipped. Failed runs include best-effort job details, annotations, and failed logs so intake/planner can turn a CI failure source into a repair task without manual log gathering.
