# Contributing

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
make install                                      # dev deps, editable install, pre-commit hooks
make check                                        # ruff, mypy, tests + coverage gate, offline eval gate
```

Everything runs offline by default in tests (in-memory MongoDB, hash embeddings, a deterministic stand-in for
the LLM). You don't need any keys.

## Workflow

1. Branch from `main`. Keep each PR to one change.
2. Retrieval, prompt or agent changes need **evaluation evidence** in the PR: the before and after
   summaries from `python -m eval.run_eval` (offline). For prompt changes, also run the real-model subset of
   the affected categories.
3. If a change regresses a gated metric on purpose, explain why in the PR, then refresh the baseline with
   `--save-baseline`.
4. Schema or index changes must update `db.ensure_indexes` and say that `scripts.setup_db` needs to run.
5. Every bug fix comes with a test. Every wrong answer becomes a golden question (use the *Answer-quality
   issue* template).

## Conventions

- Python 3.10+, ruff (line length 120), mypy on `verity/`.
- Use the `verity.*` loggers with structured extras (`log.info("...", extra={...})`). Never log secrets or
  raw document text.
- Every database query on client data is filtered by `engagement_id`.
- Model calls go through `llm/gateway.generate()` and nowhere else.
- Commit messages are in the imperative mood and explain *why* in the body.

## Releases

Follow [Semantic Versioning](https://semver.org). Update [CHANGELOG.md](CHANGELOG.md), bump `version` in
`pyproject.toml` and `api/main.py`, then tag `vX.Y.Z`. The deploy workflow rolls the release out.
