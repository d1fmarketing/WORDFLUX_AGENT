# Testing Guidelines

This directory houses both unit and integration suites for the agent cockpit.

- `tests/unit/` mirrors the modules in `src/` and should exercise pure logic with fast, deterministic cases.
- `tests/integration/` simulates end-to-end flows across multiple modules or external services; prefer lightweight stubs over real network calls.
- Shared fixtures and data live in `tests/fixtures/`.

## Running Tests

Activate your virtual environment (`source .venv/bin/activate`), ensure development dependencies are installed (`make dev`), then run:

```bash
make test
```

For coverage targets referenced in `AGENTS.md`, use:

```bash
make coverage
```

Document any intentional coverage gaps or flaky scenarios here so the team has a single source of truth.
