## What & why

## How it was verified
- [ ] `make check` (ruff, mypy, tests + coverage, offline eval gate)
- [ ] Retrieval/agent change: eval numbers before → after (attach `eval/results/*.json` or paste the summary)
- [ ] Prompt change: ran the real-model eval subset for the affected categories

## Risk
- [ ] Touches tenant isolation (engagement filters, IDs)  - [ ] Touches prompts / tool specs  - [ ] Schema or index change (needs `scripts.setup_db`)
