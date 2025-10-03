PYTHON ?= python3
VENV ?= .venv
PIP ?= $(VENV)/bin/pip
PYTEST ?= $(VENV)/bin/pytest
RUFF ?= $(VENV)/bin/ruff

.PHONY: venv install dev lint format test coverage run-agent worker chat-test chat-smoke clean

venv:
	@if [ ! -d "$(VENV)" ]; then \
		$(PYTHON) -m venv $(VENV); \
		echo "Created virtualenv at $(VENV)"; \
	fi

install: venv
	$(PIP) install -r requirements.txt

dev: install
	$(PIP) install -r requirements-dev.txt

lint:
	$(RUFF) check src tests

format:
	$(RUFF) format src tests

test:
	$(PYTEST) -q

coverage:
	$(PYTEST) --cov=src --cov-report=term-missing

run-agent: dev
	$(VENV)/bin/python -m scripts.run_agent --agent echo --message "Hello from WordFlux"

worker: dev
	$(VENV)/bin/python -m scripts.run_worker --once

chat-test: dev
	WF_LLM_PROVIDER=mock $(PYTEST) tests/unit/test_llm_bridge.py tests/unit/test_chat_api.py -xvs

chat-smoke: dev
	WF_LLM_PROVIDER=mock $(VENV)/bin/python scripts/chat_smoke.py

clean:
	rm -rf $(VENV)
