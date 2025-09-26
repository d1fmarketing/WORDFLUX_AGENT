# Repository Guidelines

## Project Structure & Module Organization
Keep runtime code in `src/`, with agents orchestrated under `src/agents/` and shared utilities in `src/core/`. Store environment and tool configs in `configs/`, reusable prompts in `assets/prompts/`, and long-form references in `docs/`. Tests belong in `tests/unit/` and `tests/integration/`; mirror the `src/` layout so each module has an obvious test partner. Use `scripts/` for repeatable entrypoints such as dataset refreshers or local deployment helpers.

## Build, Test, and Development Commands
Create a clean environment before hacking: `python -m venv .venv && source .venv/bin/activate`. Install dependencies with `pip install -r requirements.txt` and optional extras via `pip install -r requirements-dev.txt`. Run fast feedback with `pytest -q` and gate merges with `pytest --maxfail=1 --cov=src --cov-report=term-missing`. Lint and format with `ruff check src tests` and `ruff format src tests`. Use `make dev` for the common sequence (install + lint + unit tests) and `make run-agent AGENT=name` to exercise an agent locally; wire additional commands into the `Makefile` as they stabilise.

## Coding Style & Naming Conventions
Target Python 3.11+, black-compatible formatting, and 4-space indentation. Prefer type hints and dataclasses for agent contracts. Modules are `snake_case.py`, classes `CamelCase`, async helpers `verb_noun_async`. Keep prompts versioned as `prompt-name@v1.txt` so rollbacks are trivial. Validate config files with JSON Schema fragments stored alongside the source that consumes them.

## Testing Guidelines
Write unit tests first, using `pytest` fixtures for synthetic tool responses. Integration tests should simulate end-to-end agent runs against lightweight stubs under `tests/integration/agents/`. Maintain ≥90% coverage on `src/agents` and ≥80% overall; document intentional gaps in `tests/README.md`. Name tests `test_<module>_<behavior>` and colocate scenario data in `tests/fixtures/`.

## Commit & Pull Request Guidelines
Follow Conventional Commits (`feat:`, `fix:`, `chore:`) and keep subject lines ≤72 characters. Reference issues with `Refs #123` in the body and describe observable behaviour changes, not internal refactors. Pull requests must include: summary checklist, test evidence (command + result), screenshots or logs for UI/CLI changes, and rollout notes if the change alters default agent behaviour. Draft PRs early, convert to ready once CI is green.

## Agent-Specific Notes
Document every new agent in `docs/agents/<agent-name>.md` covering purpose, triggers, and tool access. Standardise agent configuration via `configs/agents/<agent-name>.yaml`; use feature flags instead of hard-coded conditionals. When introducing third-party tools or APIs, add a security review note to `docs/security.md` and provide sandbox instructions for maintainers.
