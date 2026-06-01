# Contributing

[中文](CONTRIBUTING.zh-CN.md)

Attestflow is an AI-first development harness. Contributions should preserve the core boundary:

- Generative work belongs to programming agent providers.
- Attestflow core owns deterministic validation, IDs, state, locks, verification, evidence, and recovery.
- Runtime task JSON is written by Attestflow, not by providers.

## Development setup

```bash
python3 -m unittest discover -s tests
python3 -m attestflow verify
```

The project intentionally has no runtime Python dependencies.

## Change rules

- Add or update tests before behavior changes.
- Keep provider integrations behind command contracts or built-in adapters.
- Do not add SaaS control-plane behavior to the open-source core.
- Do not add network calls to core verification paths.
- Keep templates and packaged templates in sync.

## Pull request checklist

- Tests pass with `python3 -m unittest discover -s tests`.
- `python3 -m attestflow verify` passes.
- Docs are updated for new commands, contracts, provider behavior, or examples.
- New provider behavior leaves `input.json`, `stdout.log`, `stderr.log`, and `output.json` evidence.
