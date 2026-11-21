.PHONY: install lint type test cov eval gate check run demo docker

PY ?= python
OFFLINE = MONGODB_URI=mongomock:// LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash EMBEDDING_DIM=256 CHECKPOINTER=memory

install:            ## dev install (editable) + git hooks
	$(PY) -m pip install -r requirements-dev.txt && $(PY) -m pip install -e . --no-deps && pre-commit install

lint:               ## ruff
	$(PY) -m ruff check .

type:               ## mypy
	$(PY) -m mypy

test:               ## unit + e2e + API tests (offline)
	$(PY) -m pytest

cov:                ## tests with coverage gate
	$(PY) -m pytest --cov --cov-report=term-missing --cov-report=xml

gate:               ## offline retrieval + agent eval, fails on regression vs eval/baseline.json
	$(OFFLINE) $(PY) -m eval.run_eval --ingest data/sample --agent --judge --gate

check: lint type cov gate   ## everything CI runs

run:                ## API against .env settings
	$(PY) -m uvicorn verity.api.main:app --reload

demo:               ## fully offline demo with the sample corpus
	$(PY) -m scripts.serve_demo

docker:             ## build the container image
	docker build -t verity:local .
