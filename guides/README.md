# guides/

Build-guides for the **guide-then-review** parts of this project (CLAUDE.md §4).

## The workflow

For substantive, learning-relevant code, the loop is:

1. **A detailed markdown build-guide is written here** (`guides/<filename>.md`) — concepts +
   sketches for the tricky parts + gotchas + a verification block.
2. **The owner implements it themselves** in the relevant file.
3. **Review, then apply fixes** before moving on.

The owner is *not* handed finished implementations of these parts — the point is to learn
the mechanism, not just wire up the tool.

## What gets a guide

| Component | Mode |
|---|---|
| Observability hook (in the **RAG repo**: `app/observability.py` + call-site + custom latency callback) | guide-then-review |
| `evals/` harness (`golden_set`, `ragas_setup.py`, `metrics.py`, `run_eval.py`, `langfuse_sync.py`) | guide-then-review |
| Self-host infra (`infra/langfuse/`) | written directly (plumbing) |
| Custom dashboard (`dashboard/app.py`) | written directly, on request |

## Guides expected (rough order, per ARCHITECTURE §11)

- `observability-hook.md` — Phase 1 (lands in the RAG repo)
- `evaluation-concepts.md` — primer (read before Phase 3/4): the eval process, components, and how RAGAS works
- `golden-set.md` — Phase 3 (authoring the fixed `golden_set.jsonl`; "you author, I review coverage")
- `eval-harness.md` — Phase 4 (the `evals/` runner, RAGAS setup, metrics, Langfuse sync)
