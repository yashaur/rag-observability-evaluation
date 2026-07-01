# Primer: evaluation concepts + how RAGAS works

> Reference notes for the mental model behind the *evaluation* half of this project — the
> companion to [observability-concepts.md](observability-concepts.md). Read this **before**
> `golden-set.md` (Phase 3) and `eval-harness.md` (Phase 4): it explains what the pieces are,
> how a single evaluation run flows end to end, and — the part worth slowing down for — how
> RAGAS actually turns a free-form answer into a number. Grounded in the installed stack
> (`ragas 0.4.3`, `langchain 1.3.10`, `langchain-ollama 1.1.0`, `langfuse 4.x`).
>
> RAGAS rewrote its API across `0.1 → 0.2 → 0.4`, so exact class/function names here are the
> correct *shape* — **verify against the installed `0.4.3` when you wire Phase 4** (CLAUDE.md
> §6). See the environment note in Part 4.

---

## Part 0 — the one-sentence version

Evaluation = take a **fixed list of questions you already know the right answers to**, run
them through the RAG system, and have a **judge LLM score the outputs** on a few axes — so that
when you change the RAG, you can re-run the *same* list and see, in numbers, whether it got
**better or worse**.

Everything below expands that sentence.

---

## Part 1 — what "evaluation" means here (and what it is *not*)

This project has two subsystems that share one Langfuse backend (ARCHITECTURE §2):

| | **Monitoring** (observability) | **Evaluation** |
|---|---|---|
| Question it answers | "What's happening / what just broke?" | "Is the new version **better** than the old one?" |
| When | online, continuous, on live traffic | offline, on demand, on a fixed set |
| Inputs | whatever real users ask (different every time) | a **frozen** golden set (identical every run) |
| Output | traces + latency/token scores | RAGAS metric scores per question, per run |

The crucial idea is **comparability**. You can't conclude "my reranker change helped" by
eyeballing live traces — every live request asks a *different* question, so two live traces are
never apples-to-apples. The only way to get a clean before/after delta is to **hold the inputs
constant** and change one thing (the RAG). That frozen input set is the **golden set**, and one
pass of the whole set through the RAG + RAGAS is a **run**.

```
                 eval-driven development loop
   ┌────────────────────────────────────────────────────────┐
   │  change the RAG (add reranker, swap model, tweak prompt) │
   │            │                                             │
   │            ▼                                             │
   │  run the SAME golden set ── RAGAS ──► scores (this run)  │
   │            │                                             │
   │            ▼                                             │
   │  compare vs the previous run  ──►  better? keep.         │
   │                                    worse?  revert.       │
   └────────────────────────────────────────────────────────┘
```

> **Why "fixed" is sacred:** the moment you reword/add/remove a golden-set item, runs before
> and after that edit are no longer comparable — the yardstick changed. This is the single
> discipline that makes the numbers mean anything (more in `golden-set.md`).

---

## Part 2 — the components (the cast)

| Component | Lives in | Role |
|---|---|---|
| **Golden set** | `evals/datasets/golden_set.jsonl` | the fixed inputs **+ reference answers**. The yardstick. |
| **RAG under test** | the *other* repo, reached over **HTTP** | the black box being graded. Never imported (`contract/`). |
| **RAGAS** | `ragas` library | turns (question, answer, contexts, reference) into metric scores. |
| **Judge LLM** | **hosted** frontier model (GPT/Claude), wrapped for RAGAS | the "grader" — reads outputs and makes judgments (Part 4). |
| **Embeddings** | local Ollama, wrapped for RAGAS | turns text into vectors for *semantic similarity* metrics. |
| **Metric set** | `evals/metrics.py` | which axes we score (faithfulness, relevancy, …). |
| **Langfuse Dataset / Dataset Run** | the Langfuse backend | where the fixed set is mirrored + where each run's scores land for side-by-side comparison. |
| **Results file** | `evals/results/<ts>_<version>.json` | a version-controlled local record, independent of Langfuse. |

Three things worth saying out loud:

- **The RAG is a black box reached only over HTTP.** The harness POSTs a question and reads
  back `answer` + `retrieved_contexts` (the `contract/`). It never imports RAG code — that's
  what makes the RAG *swappable* and keeps evaluation honest (you grade what the real API
  actually returns, warts and all).
- **There are two LLMs in play, and they're different roles.** The **generator** is the RAG's
  own *local Ollama* model (produces the answer). The **judge** is a *separate, hosted* frontier
  model (GPT/Claude) RAGAS uses to *grade* that answer — grading is harder than answering, so we
  pay for a stronger grader (ARCHITECTURE §7.4). Keep them distinct. (Embeddings, used by some
  metrics, stay local Ollama.)
- **You author inputs, not outputs.** You write the `question` and the `reference` answer. The
  `answer` and `retrieved_contexts` are produced *fresh by the RAG every run* — that's the
  whole point (same inputs, changed system → comparable outputs).

---

## Part 3 — one run, step by step

```
 evals/run_eval.py  (offline, non-streaming, on demand)
 ─────────────────────────────────────────────────────────────────────────────
  1. load golden_set.jsonl  ──►  [ {id, question, ground_truth, type}, ... ]
                                          │  for each item:
                                          ▼
  2. POST question to RAG API (stream=false)  ──►  { answer, retrieved_contexts }
                                          │
                                          ▼
  3. build a RAGAS sample:
        user_input          = question            (you authored)
        reference           = ground_truth        (you authored)
        response            = answer              (RAG produced, this run)
        retrieved_contexts  = retrieved_contexts  (RAG produced, this run)
                                          │  collect all samples → EvaluationDataset
                                          ▼
  4. evaluate(dataset, metrics=[...], llm=judge, embeddings=emb)
        → judge LLM + embeddings score each sample on each metric (Part 4)
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
  5. push a Langfuse DATASET RUN            6. write evals/results/<ts>_<ver>.json
     (run_name = <git_sha>-<desc>;             (local, version-controlled record;
      one score per metric per item)            also feeds the optional dashboard)
                          │
                          ▼
     compare runs in Langfuse's Dataset run-comparison view
     ("faithfulness 0.81 → 0.89" across versions, side by side)
```

### The field-mapping table (memorize this — it's the join between the two repos)

| Golden-set field | RAGAS sample field | Supplied by | When |
|---|---|---|---|
| `question` | `user_input` | **you** (author) | fixed |
| `ground_truth` | `reference` | **you** (author) | fixed |
| — | `response` | the **RAG** (`answer`) | produced each run |
| — | `retrieved_contexts` | the **RAG** (`retrieved_contexts`) | produced each run |
| `type` | *(not sent to RAGAS)* | you | selects coverage + the custom refusal check |

(Names match the `evals/run_eval.py` stub and `contract/README.md`. The harness is pinned to
`stream=false` so timing is clean and answers are identical to streaming — CLAUDE.md §0 #9.)

---

## Part 4 — how RAGAS actually works (the mechanism)

### The core idea: LLM-as-judge

A RAG answer is free-form text. There is no `expected == actual` you can run — "Paris is the
capital" and "The capital is Paris, France" are both right but not string-equal. So RAGAS
**doesn't string-match**. Instead it uses two tools to make *graded* judgments:

1. a **judge LLM**, prompted to make small structured decisions ("is this claim supported by
   this context? yes/no"), and
2. **embeddings**, to measure *semantic* similarity (cosine distance between meaning-vectors).

That's the whole trick — and also why **every score is probabilistic and jitters** run to run:
you're asking a (local, smallish) model to make many sub-judgments, and it won't answer
identically every time. Hold that thought for "reading results" below.

### The data RAGAS wants: a sample

RAGAS evaluates a list of **samples** (one per question). Conceptually a single-turn sample
carries the four fields from Part 3 — `user_input`, `response`, `retrieved_contexts`,
`reference` — and you bundle them into an evaluation dataset, then call `evaluate(...)`. (Shape:
`SingleTurnSample` / `EvaluationDataset` / `evaluate` — verify exact names against `0.4.3`.)

### Reference-free vs reference-based (why the golden set needs `ground_truth`)

| Metric needs… | Means | Metrics |
|---|---|---|
| **no reference** | graded only from question + answer + contexts | faithfulness, answer relevancy |
| **a reference** (`ground_truth`) | graded by comparing against *your* answer key | context precision*, context recall, answer correctness |

This is *why* you bother authoring `ground_truth` in the golden set: the reference-based metrics
literally cannot be computed without it. (*context precision can be run reference-free or
reference-based depending on the variant; we use the reference-based form.)

### Each metric, conceptually

Think of each as a little judge-driven algorithm. (Exact `0.4.3` class names — e.g. RAGAS
renamed *answer relevancy* to `ResponseRelevancy` — to confirm in Phase 4.)

- **Faithfulness** *(reference-free; the anti-hallucination metric — watch this most).*
  Decompose the `response` into individual claims → for each claim, ask the judge "is this
  supported by the `retrieved_contexts`?" → score = supported claims ÷ total claims. A confident
  answer built on nothing in the context scores low. This is the number that catches the RAG
  *making things up*.

- **Answer relevancy** *(reference-free).* Ask the judge to generate a handful of questions that
  the `response` would be a good answer to → embed those and the real `user_input` → average
  their cosine similarity. A focused, on-topic answer yields questions close to the original; a
  rambling or evasive answer drifts away → lower score. Measures "did it actually address what
  was asked?", **not** whether it's correct.

- **Context precision** *(uses reference).* Look at the *ranked* `retrieved_contexts`. Using the
  reference to decide which chunks are truly relevant, reward configurations where the relevant
  chunks sit **near the top**. It's a signal-to-noise + *ordering* measure of retrieval — high
  when the good chunks lead, low when they're buried under junk.

- **Context recall** *(needs `ground_truth`).* Break the `reference` answer into its claims →
  check whether each one is **attributable to** the `retrieved_contexts`. Score = covered claims
  ÷ total. Answers "did retrieval surface *everything* needed to produce the reference?" Low
  recall means the generator never even had the material → a *retrieval* problem, not a
  generation one.

- **Answer correctness** *(needs `ground_truth`).* Compare `response` to `reference` as a blend
  of (a) **factual overlap** — the judge extracts statements and tallies true-positives /
  false-positives / false-negatives into an F1-like score — and (b) **semantic similarity** via
  embeddings. The closest thing to a single "is the final answer right?" number. (RAGAS 0.2+ also
  split out a `FactualCorrectness` metric; pick the one matching ARCHITECTURE §7.3 in Phase 4.)

- **Refusal-correct** *(custom — NOT a RAGAS metric).* Only meaningful on `type == "refusal"`
  items. Checks the RAG **abstained** ("not in the knowledge base") instead of inventing an
  answer. Your RAG abstains *free-form* (its system prompt says "let the user know the context
  doesn't cover this" and to cite a source filename on real answers), so the check can't be an
  exact-string match — it's keyword/citation-presence based, decided in `metrics.py` (Phase 4).
  This lives in our code, not RAGAS, because protecting abstention is the highest-value behavior
  for a personal KB and no off-the-shelf metric captures it.

### Reading results responsibly (judge jitter is real)

Because metrics are judge-driven (an LLM grading, not string-matching), scores **wobble** run to run — a hosted frontier judge *reduces* this materially but doesn't eliminate it.
The discipline (ARCHITECTURE §7.6):

- Don't act on a single **0.02–0.05** swing on ~25 items — that's inside the noise.
- Look for **consistent movement across the set**, not one item.
- **Re-run borderline evals 2–3×** and average; trust the **trend**, not one number.
- **Never** change the golden set *and* a RAG version in the same step — you won't know which
  moved the needle.

### ⚠️ Environment note (the installed combo is currently broken)

As of writing, `import ragas` **fails** in the eval `.venv`: `ragas 0.4.3` imports
`langchain_community.chat_models.vertexai.ChatVertexAI`, which no longer exists in the installed
`langchain-community 0.4.2` (it shipped with `langchain 1.3.10`). The `requirements.txt` pins are
loose (`ragas>=0.2`, `langchain>=0.3`), so pip resolved an untested matrix. **This must be fixed
before Phase 4 coding** — pin a mutually compatible RAGAS × LangChain set. It's purely a setup
issue and doesn't change any concept above.

---

## Part 5 — how this connects back to observability

The two subsystems aren't really separate at runtime: because the harness calls the **real RAG
API**, every eval question *also* flows through the observability hook → each eval question
produces a **trace** (with the retriever/Ollama spans and TTFT/TPOT/TPS scores) *and* a row of
**RAGAS scores**. So one Langfuse backend answers both "how did it behave?" (traces) and "is it
better?" (Dataset Runs), for the same requests. That convergence (ARCHITECTURE §9) is why we put
both code paths into one store.

---

## Where to go next

- **`golden-set.md`** (Phase 3) — author the fixed inputs this whole machine runs on. Should
  read much more naturally now: you know *why* every field exists and what each metric does with
  it.
- **`eval-harness.md`** (Phase 4) — implement `ragas_setup.py` / `metrics.py` / `run_eval.py` /
  `langfuse_sync.py`, verifying the RAGAS `0.4.3` signatures against this mental model (after the
  environment note above is resolved).

## References
- ARCHITECTURE §7 (the eval design), §7.3 (metric set), §7.6 (reading results), §9 (convergence).
- `contract/README.md` — the HTTP contract the harness calls.
- RAGAS docs — verify metric class names / `evaluate` signature against the installed `0.4.3`.
- Companion primer: `observability-concepts.md`.
