# Build-guide: the golden set (Phase 3)

> **Mode: guide-then-review** — the "you author, I review coverage" variant (ARCHITECTURE
> §11 Phase 3). The items are *yours* to write: only you know what's in your KB, so I can't
> hand you the questions. This guide gives you the schema, the coverage plan, the craft, the
> gotchas, and a verification block. You author → I review coverage/quality before Phase 4.
>
> **Where this lands:** `evals/datasets/golden_set.jsonl` in *this* repo. Phase 3 is **pure
> data** — no code. Replace the three `EXAMPLE` placeholder rows already in the file.
>
> **The cardinal rule:** once you start comparing RAG versions against this set, it is
> **frozen**. Get it right *before* you lean on it — editing it later silently invalidates
> every historical comparison (Gotcha #1).

---

## 0. What you're building and why

~25 fixed, version-controlled Q&A items — the **single yardstick** for "did my change
help?". You re-run the whole set through RAGAS after every RAG change and compare against the
previous version. Clean before/after numbers come *only* from identical inputs, which is
exactly why a fixed set exists: live traffic asks different questions each time, so live
traces are never comparable as an eval (CLAUDE.md §6 "Don't").

This file is the source of truth. Phase 4's harness reads it; Phase 4's `langfuse_sync`
mirrors it into a Langfuse **Dataset** so the run-comparison UI works.

---

## 1. The schema — one JSON object per line (JSONL)

| field | required | type | what it's for |
|---|---|---|---|
| `id` | ✅ | str | stable unique key (`q001`, `q002`, …). Keys the Langfuse Dataset item and links runs to traces across versions. **Never renumber.** |
| `question` | ✅ | str | the user question. Becomes RAGAS `user_input`. |
| `ground_truth` | ✅ | str | your reference answer, grounded in the KB. Becomes RAGAS `reference`. Drives **context recall** + **answer correctness**, and the judge scores against it. |
| `ground_truth_contexts` | optional | list[str] | the *ideal* chunk text(s). Only for hard IR metrics (Hit Rate@k / MRR), a later optional add. Leave `[]` for now (§6). |
| `type` | ✅ | str | one of `factual` \| `multi_hop` \| `summary` \| `refusal`. Drives coverage and triggers the custom refusal check. |

### The key mental model (read this once, carefully)

The golden set holds **inputs + the reference answer** — *not* the system's outputs. At eval
time `run_eval.py` POSTs your `question` to the **live RAG** and reads back `answer` and
`retrieved_contexts` *fresh* every run:

```
you author:        question ──► RAGAS user_input
                   ground_truth ──► RAGAS reference
                   type (+ optional ground_truth_contexts)

RAG produces        answer ──────────────► RAGAS response          } produced fresh
at eval time:       retrieved_contexts ──► RAGAS retrieved_contexts } each run
```

You never author `response` / `retrieved_contexts`. They're regenerated each run — that's
the whole point: the same frozen inputs, run against a *changed* RAG, yield comparable
scores. (Field names per the `evals/run_eval.py` stub and `contract/README.md`.)

---

## 2. Coverage — the four types and the mix

Each type deliberately stresses a different part of the pipeline. A suggested ~25-item
spread (tune to your KB's shape):

| `type` | ~count | what it stresses |
|---|---|---|
| `factual` | ~10 | single-chunk retrieval + grounded extraction (one clear answer) |
| `multi_hop` | ~6 | retrieving **and synthesizing across ≥2 chunks** |
| `summary` | ~4 | **broad** retrieval + condensation over many chunks |
| `refusal` | ~5 | **abstention** on out-of-KB questions — the anti-hallucination test |

**Why refusals carry outsized weight for a personal KB:** a system that confidently invents
answers for things *not* in your notes is worse than one that says "not covered." Protecting
abstention is one of the highest-value behaviors here, so these earn real effort, not
filler.

---

## 3. Authoring each type (the craft)

- **`factual`** — pick one specific fact in your KB; ask a question with a single, clear
  answer. `ground_truth` = the grounded reference (state the fact; you may name the source
  filename your RAG is told to cite — see §5).
- **`multi_hop`** — design it so a correct answer *requires combining ≥2 chunks* (e.g. info
  spread across two documents). `ground_truth` = the synthesized answer. If one chunk
  answers it, it's really a `factual`.
- **`summary`** — ask broad ("summarize what the KB says about X"). `ground_truth` = the key
  points that ought to appear. Tests breadth of retrieval, not pinpoint precision.
- **`refusal`** — a question whose answer is **genuinely not in your KB**, but **plausibly
  adjacent** (a near-miss is a real test; an absurd off-topic question is too easy to refuse).
  `ground_truth` = the abstention (§5). Verifies the system says "not covered" instead of
  hallucinating.

---

## 4. Writing good ground truth (it IS the judge's answer key)

`ground_truth` feeds the reference-based metrics (**context recall**, **answer correctness**)
and the LLM judge compares the RAG's answer against it. A vague or wrong reference produces
vague or wrong scores — garbage in, garbage out.

- Keep it **concise, factual, KB-grounded** — the content that *must* be present, not
  stylistic prose. One canonical answer.
- `answer_correctness` is **semantic**, so you don't need exact wording — but the facts must
  be right and complete. Don't editorialize beyond what the KB supports.

---

## 5. The refusal answer + the `refusal_correct` signal (decide the shape now)

Your RAG's abstention is **free-form, not a fixed string.** From `personal-rag-system`'s
`app/prompts.py`, the system prompt instructs it to *"let the user know that the context
doesn't cover this specific portion of the query"* and to **cite the source filename** when
it *does* answer. So a refusal looks *like* "the context doesn't cover this…", but the exact
words vary per call, and a correct refusal **cites no source**.

Consequences for you now:

- **`ground_truth` for refusals:** write it in that same spirit and keep it **consistent**
  across all refusal items, e.g. *"The provided context does not cover this; it isn't in the
  knowledge base."*
- **`refusal_correct()`** (the `metrics.py` TODO you'll implement in Phase 4) must **not**
  rely on exact-string match against free-form output. Plan a robust signal:
  - a keyword/substring check for abstention markers (`"doesn't cover"`, `"not in the
    context"`, `"no information"`, `"unable to find"`), **and/or**
  - **"no source filename cited"** as a corroborating signal (grounded answers cite one;
    refusals don't), **and/or**
  - the judge as a fallback for borderline cases.

You implement the detector in Phase 4 — but author the refusal `ground_truth` *now* so it's
consistent with whatever signal you pick.

---

## 6. `ground_truth_contexts` — skip it for now

Only needed for **Hit Rate@k / MRR**, the precise-retrieval IR metrics that are an explicit
*later optional* add (ARCHITECTURE §7.3) — and only if you start tuning retrieval and want
labelled ideal chunks. Leave it `[]`. Don't hand-label ideal chunk texts until you're
actually doing that tuning; it's the most tedious part of golden-set authoring and buys
nothing for the core metric set.

---

## Gotchas

> ### ⚠️ Gotcha #1 — Freeze it once you compare
> The moment you use the set for before/after, stop editing items (rewording, adding,
> removing). "faithfulness 0.81 → 0.89" only means something if the questions were
> **identical** across runs. Grow the set deliberately, and treat a grown set as a **new
> baseline** (don't compare new-set runs to old-set runs).

> ### ⚠️ Gotcha #2 — Refusal ground truth must match *real* behavior
> Your abstention is free-form (§5). Don't author a refusal `ground_truth` your RAG would
> never say, and don't build `refusal_correct` around an exact phrase. Align the reference,
> the detector, and the actual output.

> ### ⚠️ Gotcha #3 — Don't leak the answer into the question
> Write what a real user would ask. "According to `notes.md` which says Y, what is Y?" tests
> nothing. The retriever should have to *find* the chunk.

> ### ⚠️ Gotcha #4 — KB drift staleness
> The set assumes a **fixed KB**. If you re-ingest or edit documents, a `factual` item's
> correct answer can change and your `ground_truth` goes stale. Note which KB state the set
> is written against; if the KB changes materially, audit the affected items (and remember
> Gotcha #1 — that's a new baseline).

> ### ⚠️ Gotcha #5 — Stable, unique IDs
> `q001…`, unique, never renumbered. They key the Langfuse Dataset items and stitch a run's
> scores to the right trace across versions. Reusing or renumbering ids corrupts the history.

> ### ⚠️ Gotcha #6 — 25 is a floor, and the judge jitters
> A small set means per-run score wobble (a hosted judge reduces it but doesn't remove it). This is *why* you read
> **trends, not single deltas** (ARCHITECTURE §7.6): don't act on a lone 0.02–0.05 swing;
> re-run borderline evals 2–3×. A larger, well-spread set tightens the signal — grow toward
> it deliberately.

> ### ⚠️ Gotcha #7 — Balance, don't pad
> Don't hit the count with 20 trivial factuals. Spread difficulty *within* each type, and
> keep the type mix close to §2. Every item should test something a change could plausibly
> break.

---

## Verification block (run before declaring Phase 3 done)

1. **Valid JSONL** — every line parses:
   ```bash
   python -c "import json;[json.loads(l) for l in open('evals/datasets/golden_set.jsonl') if l.strip()];print('valid jsonl')"
   ```
2. **Schema, unique ids, type mix, no placeholders left:**
   ```bash
   python - <<'PY'
   import json, collections
   rows=[json.loads(l) for l in open('evals/datasets/golden_set.jsonl') if l.strip()]
   req={'id','question','ground_truth','type'}
   assert all(req <= r.keys() for r in rows), 'a row is missing a required field'
   ids=[r['id'] for r in rows]; assert len(ids)==len(set(ids)), 'duplicate id'
   assert all(r['type'] in {'factual','multi_hop','summary','refusal'} for r in rows), 'bad type'
   assert not any('EXAMPLE' in r['question'] for r in rows), 'placeholder EXAMPLE rows remain'
   print('count:', len(rows))
   print('types:', dict(collections.Counter(r['type'] for r in rows)))
   print('ok')
   PY
   ```
3. **Eyeball test** — read 2–3 items per type: would a human agree the `ground_truth`
   answers the `question` and is grounded in the KB? Are the `refusal` items *genuinely*
   out-of-KB (and adjacent, not absurd)?
4. **Refusal consistency** — all `refusal` ground truths phrased consistently and aligned
   with the `refusal_correct` signal you plan in §5.

**Done when:** ~25 items, all four types present in roughly the §2 mix, zero `EXAMPLE`
placeholders, the validator passes, and the refusals are real out-of-KB negatives. Then ping
me to **review coverage** before we start Phase 4.

---

## How this feeds Phase 4 (author with the end in mind)

- **`run_eval.py`:** load JSONL → POST each `question` (stream=false) → read `answer` +
  `retrieved_contexts` → build a RAGAS sample `{user_input, response, retrieved_contexts,
  reference}` → `evaluate(...)` with the metric set; the custom `refusal_correct` keys on
  `type == "refusal"`.
- **`langfuse_sync.py`:** mirror the set once into a Langfuse **Dataset** (one item per
  question) so the Dataset run-comparison view works; each run links items → traces and
  writes one score per metric.

That's why `id` and `type` matter beyond this file — they're the join keys for the whole
regression loop.

---

## Scope guardrail

Phase 3 touches **only** `evals/datasets/golden_set.jsonl` (replace the three `EXAMPLE`
rows). No harness code yet — `ragas_setup.py` / `metrics.py` / `run_eval.py` /
`langfuse_sync.py` are Phase 4 (`eval-harness.md`). Author data → validate → review.

---

## References

- ARCHITECTURE §7.1 (golden set), §7.3 (metric set), §7.6 (reading results responsibly),
  §11 Phase 3.
- RAGAS sample shape (`user_input` / `response` / `retrieved_contexts` / `reference`) and
  metric imports — verify against the installed RAGAS version (ARCHITECTURE §12).
- Your RAG's abstention + citation instruction: `personal-rag-system` `app/prompts.py`
  (system message).
- Contract the harness calls over HTTP: `contract/README.md`.
