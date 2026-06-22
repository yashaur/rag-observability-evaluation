# ARCHITECTURE.md — RAG Eval & Observability

> The technical blueprint. **CLAUDE.md** covers purpose and collaboration; this file is the concrete *what to build*. Where exact library method signatures matter, the correct **shape** is given and flagged to verify against the installed version — Langfuse (v2→v3) and RAGAS (≤0.1→0.2+) both had breaking rewrites, so signatures here are illustrative, not copy-paste-final.

---

## 1. Confirmed system facts (verified with the owner — not assumptions)

| Fact | Value | Consequence |
|---|---|---|
| RAG framework | **LangChain** | Use the native Langfuse LangChain `CallbackHandler`. |
| LLM | **Local Ollama** | Real cost \$0 → cost is a *demonstration* metric (§6.4). Judge can be local too (§7.4). |
| RAG API | FastAPI, `app.main:app`, port 8000 | The eval harness POSTs to it over HTTP. |
| **API returns retrieved contexts** | **Yes** | **No change needed in the RAG repo for evaluation.** The harness gets answer + contexts from the existing response. |
| Retrieval | Hybrid: BM25 (rebuilt in-memory via `refresh()`, not persisted) + vector | Eval-over-HTTP uses the already-warm BM25 of the running server; a direct import would start cold. |
| Streaming | App has an on/off **toggle** | Live traffic uses the current setting; latency capture adapts (§6.3). Eval is pinned non-streaming. |
| Frontend | Streamlit (`frontend/app.py` + `pages/`) | Not touched by this project. |

## 2. The two subsystems, one backend

Two code paths (online callback vs. offline batch), one Langfuse store. Live traces and eval scores coexist, so trace exploration and run-comparison sit side by side. That is the concrete answer to "two areas, two stacks": two paths, one place to look.

## 3. Two-repo topology + data flow

```
┌──────────────────────── personal-rag-system (RAG repo) ────────────────────────┐
│  Streamlit chat ──► FastAPI ──► LangChain chain                                 │
│   (stream toggle)      │          │  retriever span (BM25 + vector)             │
│                        │          │  Ollama LLM span                            │
│                        │          └──► app/observability.py:                    │
│                        │                 • Langfuse CallbackHandler  ───────────┼──┐
│                        │                 • OllamaLatencyCallback (custom)       │  │
│                        │                   TTFT / TPOT / TPS / load / tokens    │  │
│                        │               metadata: session_id, user_id,          │  │
│                        │                         release=<rag_version>,         │  │
│                        │                         stream=<on|off>                │  │
└────────────────────────┼───────────────────────────────────────────────────────┘  │
                         │  HTTP only (answer + retrieved_contexts)                  │
                         │                                                           ▼
┌──────────────── rag-eval-observability (THIS repo) ────────┐   ┌─────────────────────────────┐
│  infra/langfuse/  ── self-hosts ──────────────────────────►│──►│   SELF-HOSTED LANGFUSE       │
│                                                            │   │   (single source of truth)   │
│  evals/run_eval.py  (offline, on-demand, non-streaming)    │   │                              │
│    1. load fixed golden_set.jsonl                          │   │   TRACES (observations,      │
│    2. POST each Q ──► RAG API ──► answer + contexts ───────┼──►│    grouped into sessions,    │
│    3. RAGAS scores (local Ollama judge + embeddings)       │   │    + custom latency scores)  │
│    4. push DATASET RUN, run_name=<git_sha>-<desc> ─────────┼──►│   SCORES on a Dataset Run    │
│    5. write evals/results/<ts>_<version>.json              │   │    (1 item/Q, 1 score/metric)│
│                                                            │   └──────────────┬──────────────┘
│  dashboard/app.py  (OPTIONAL) ── reads via Langfuse SDK ───┼──────────────────┘
└────────────────────────────────────────────────────────────┘   Primary view: Langfuse built-in UI
```

**Modularity (and its honest limit):** because the harness only needs a URL + the API contract, the RAG system is swappable. *Evaluation* is fully RAG-agnostic (black-box over HTTP). *Observability* is modular at the edges (end-to-end latency, request/response, cost transfer for free) but per-implementation inside (the retriever/LLM/Ollama-timing spans come from the in-process hook). Don't oversell total modularity.

## 4. The API contract (the swappability interface)

This is the linchpin of the two-repo design. Any RAG system that satisfies it can be evaluated unchanged. Keep this documented in the repo (e.g. `contract/README.md` or an OpenAPI snippet).

**Request** (POST, e.g. `/chat`): at minimum `{ "question": str, "stream": bool, "session_id": str|null }`.

**Response** (non-streaming, what the eval needs): at minimum
```
{
  "answer": str,
  "retrieved_contexts": [str, ...]   // the actual chunk texts used — required for retrieval & faithfulness metrics
}
```
Your existing API already returns `retrieved_contexts`, so it conforms today. Document the exact field names you use so a future swapped-in system can match them (or so the harness can adapt via a thin client).

## 5. Component 1 — Self-hosted Langfuse (owned by THIS repo)

**License/status:** MIT, self-hostable via Docker Compose. Acquired by ClickHouse (Jan 16, 2026); stated position is no licensing change and self-hosting continues. Safe to build on. The RAG app does **not** own this — it only points at it via `LANGFUSE_HOST` + keys.

### Default: v3; lighter fallback: v2

| | **v3 (default)** | **v2 (lighter fallback)** |
|---|---|---|
| Services | Web + Worker + Postgres + ClickHouse + Redis + S3/MinIO | Langfuse + Postgres |
| RAM | A few GB for the full stack | Comfortably under ~1 GB |
| Setup | One `docker compose up` (official compose wires it all) | One `docker compose up` |
| Best for | Current/maintained line | Minimal footprint on a small machine |

**Decision:** start on **v3 via the official docker-compose** — current, maintained, batteries-included (so "more services" is still one command). Drop to **v2 Postgres-only** if RAM is tight or you want the fewest moving parts; this project needs nothing v2 lacks.

> **Insulation:** the harness and optional dashboard read through the **Langfuse Python SDK**, never raw ClickHouse/Postgres — so the v2/v3 choice never touches our code, only the compose file.

### Essentials
- Location: `infra/langfuse/docker-compose.yml` + `.env` (you write these — plumbing).
- Secrets to generate: `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY` (`openssl rand -hex 32` each) + DB/Redis/S3 creds for v3.
- Expose UI locally (e.g. `http://localhost:3000`).
- After first boot: create a project → copy **public** + **secret** API keys → these go into the RAG app's env (for the hook) and this repo's env (for the harness/dashboard).

### Reading data back (SDK shapes — verify)
- Spans: `langfuse.api.observations.get_many(...)`
- Traces + scores: `langfuse.api.trace.list(..., fields="core,scores")`
- Aggregates: the metrics endpoint shipped in your version. *Caveat:* the newest Metrics API v2 has been Cloud-only; self-hosted uses its version's endpoints. For a personal dashboard, pulling traces+scores and aggregating in pandas is more than fast enough.

## 6. Component 2 — Observability hook (lives in the RAG repo)

The **only** code this project adds to `personal-rag-system`: `app/observability.py` (a Langfuse callback + a custom Ollama-latency callback) and a few lines at the chain call site. Guide-then-review (CLAUDE.md §4).

### 6.1 Native Langfuse callback (general tracing)
```
# app/observability.py — SHAPE
from langfuse.langchain import CallbackHandler     # v3 import path
def get_langfuse_handler():
    return CallbackHandler()                        # reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from env
```
At the **single chain-invocation site** (`chains.py`/`api.py`):
```
chain.invoke(
    user_input,
    config={
        "callbacks": [langfuse_handler, ollama_latency_callback],
        "metadata": {
            "langfuse_session_id": conversation_id,   # groups a multi-turn chat
            "langfuse_user_id": "me",
            "langfuse_tags": ["live"],
            "release": RAG_VERSION,                    # git short SHA
            "stream": stream_enabled,                  # on/off, from the toggle
        },
    },
)
```
The callback auto-captures the retriever sub-spans (BM25 + vector + ensemble) and the Ollama LLM span beneath the trace — no manual span code.

### 6.2 Custom Ollama-latency callback (the detailed perf metrics)
Token counts arrive natively (if `ChatOllama` surfaces `usage_metadata`), but TTFT/TPOT/TPS and Ollama's timing breakdown are **not** in Langfuse's standard capture — they live in Ollama's `response_metadata`. A small custom `BaseCallbackHandler` reads them and writes them as **numeric scores** on the trace (scores are queryable/aggregatable in Langfuse dashboards). Shape:
```
# OllamaLatencyCallback — SHAPE; verify field names against your langchain-ollama version
on_llm_start:      record t_start
on_llm_new_token:  on FIRST token only → ttft_true = now - t_start     # streaming path
on_llm_end:        md = response_metadata   # ns fields from Ollama:
                   #   load_duration, prompt_eval_count, prompt_eval_duration,
                   #   eval_count, eval_duration, total_duration
                   ttft_proxy = (load_duration + prompt_eval_duration)   # non-streaming path
                   tpot       = eval_duration / eval_count
                   tps        = eval_count / eval_duration
                   in_tokens  = prompt_eval_count
                   out_tokens = eval_count
                   → write these as numeric scores on the current Langfuse trace,
                     tagged with the streaming mode
```

### 6.3 Metrics captured

| Metric | Source | How |
|---|---|---|
| End-to-end latency | native | Langfuse trace duration |
| Retrieval latency | native | retriever span duration |
| Generation latency | native / Ollama | LLM span duration / `eval_duration` |
| Model-load time | custom (Ollama) | `load_duration` (cold-start visibility) |
| **TTFT (true)** | custom | streaming: first `on_llm_new_token` − `on_llm_start` |
| **TTFT (proxy)** | custom (Ollama) | non-streaming: `load_duration + prompt_eval_duration` |
| **TPOT** | custom (Ollama) | `eval_duration / eval_count` |
| **TPS** | custom (Ollama) | `eval_count / eval_duration` |
| Input tokens | native / Ollama | `usage_metadata` / `prompt_eval_count` |
| Output tokens | native / Ollama | `usage_metadata` / `eval_count` |
| Cost (illustrative) | native (model-pricing) | `(in×in_price)+(out×out_price)`, computed by Langfuse (§6.4) |
| Error rate | native | trace status |
| Request volume | native | trace counts |
| Streaming mode | tag | metadata on trace |

> **TTFT only exists for real when streaming.** With the toggle on, we timestamp the first streamed token (true TTFT); with it off, there is no "first token" moment, so we use Ollama's `load_duration + prompt_eval_duration` as the proxy. Either way all four perf metrics are available; only TTFT's fidelity differs. The mode tag lets you tell them apart (and compare streaming vs. non-streaming profiles if you ever want).

### 6.4 Cost as a demonstration metric
Real cost is \$0 (local). To still show a meaningful cost panel: register a **custom model** in Langfuse's model-pricing config with **separate input and output prices** modeled on a real model (output typically 2–5× input), e.g. *illustrative* GPT-4o-mini-style rates ≈ \$0.15 / 1M input, \$0.60 / 1M output. Langfuse then computes and aggregates cost automatically on every trace — no arithmetic in your app. Label it clearly as hypothetical pricing. This demonstrates understanding of the cost model (two-sided, per-token) without claiming real numbers.

## 7. Component 3 — Offline evaluation harness (THIS repo, `evals/`)

The heart of "did my change help?". All of `evals/` is guide-then-review.

### 7.1 The golden set — `evals/datasets/golden_set.jsonl`
Fixed, version-controlled, the single yardstick. Start ~**25 items**; grow deliberately. Per item:
```
{
  "id": "q014",
  "question": "...",
  "ground_truth": "...",            // reference answer — needed for context recall & answer correctness
  "ground_truth_contexts": ["..."], // OPTIONAL: ideal chunk(s) — only if you want hard IR metrics (Hit Rate/MRR)
  "type": "factual | multi_hop | summary | refusal"
}
```
Coverage: **factual** (single-chunk), **multi-hop/synthesis** (several chunks), **summarization** (broad retrieval), and **refusal/"not in the KB" negatives** — questions whose answer isn't in your KB, to verify the system *abstains* instead of hallucinating. For a personal KB, protecting abstention is one of the highest-value behaviors. Keep the file in-repo as source of truth; mirror it into a Langfuse **Dataset** so the run-comparison UI works (§7.5).

### 7.2 The runner — `evals/run_eval.py`
1. Load `golden_set.jsonl`.
2. For each item, **POST the question to the running RAG API with `stream=false`**, read back `answer` + `retrieved_contexts`. (HTTP, non-streaming, real path, warm BM25.)
3. Assemble a RAGAS dataset (current shape: samples with `user_input`, `response`, `retrieved_contexts`, `reference`).
4. `evaluate(dataset, metrics=[...], llm=judge, embeddings=emb)`.
5. Push to Langfuse as a **Dataset Run** named for the version (§7.5).
6. Write `evals/results/<timestamp>_<version>.json` (+ optional CSV) — a version-controlled record independent of Langfuse and the data source for the optional dashboard.

### 7.3 Metric set (RAGAS, curated)

| Metric | Type | Needs ground truth? | Tells you |
|---|---|---|---|
| **Faithfulness** | Generation | No | Are the answer's claims grounded in retrieved context? *The core anti-hallucination metric — watch this most.* |
| **Answer relevancy** | Generation | No | Does the answer address the question? |
| **Context precision** | Retrieval | Uses reference | Are relevant chunks ranked at the top (signal vs. noise)? |
| **Context recall** | Retrieval | Yes (`ground_truth`) | Did retrieval surface everything needed? |
| **Answer correctness** | End-to-end | Yes (`ground_truth`) | Correctness vs. your reference answer. |
| **Refusal-correct** (custom) | Behavior | Uses `type==refusal` | On out-of-KB questions, did it abstain rather than invent? |

Reference-free metrics (faithfulness, answer relevancy) can *also* run on sampled live traces later for online quality tracking — but the golden set is where clean before/after numbers come from. Hard IR metrics (Hit Rate@k, MRR) are an optional later add (custom function or LlamaIndex retrieval evaluators) **only if** you start tuning retrieval precisely and have labelled `ground_truth_contexts`.

### 7.4 Judge LLM + embeddings — `evals/ragas_setup.py`
To stay fully self-hosted, wrap local models:
```
# SHAPE — verify against installed RAGAS version
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
judge = LangchainLLMWrapper(ChatOllama(model="<a-capable-local-model>"))
emb   = LangchainEmbeddingsWrapper(<your existing Ollama embeddings>)
```
**Caveat:** judge quality drives metric quality; a small local model is noisier. Mitigations: use a *larger* Ollama model as judge than the generator, and accept some jitter; or, if you'll tolerate a small external cost for stabler numbers, point only the judge at a hosted model (the RAG app stays fully local). Be consistent within a comparison.

### 7.5 Push to Langfuse + the regression loop — `evals/langfuse_sync.py`
- Upload the golden set once as a Langfuse **Dataset**.
- Each run = a **Dataset Run**, `run_name = f"{git_sha}-{short_desc}"` (e.g. `a1b2c3d-add-reranker`). Each item → a run item linked to its trace; each RAGAS metric → a **score** on that item. Shape: `langfuse.create_score(trace_id=..., name="faithfulness", value=0.91, data_type="NUMERIC")` (verify).
- **Compare in Langfuse's Dataset run-comparison view** — metric deltas across runs, side by side ("faithfulness 0.81 → 0.89"). This is why a custom dashboard is optional.

**Reading results responsibly:** judge scores wobble. Don't act on a single 0.02–0.05 swing on ~25 items; look for consistent movement; re-run borderline evals 2–3× and average; never change the golden set and a RAG version in the same step.

## 8. Component 4 — Monitoring view (Langfuse UI first; dashboard optional)

**What Langfuse's built-in UI already gives you, zero custom code:** live trace explorer (query → retrieved chunks → Ollama call → answer, with per-span latency and your custom perf scores), dashboards over time (volume, latency, tokens, illustrative cost), sessions view, and **Dataset run-comparison** for eval regressions — the thing a dashboard was most wanted for.

So the custom `dashboard/app.py` is **optional**, worth building only for: a single bespoke screen merging live stats + eval history with your own framing; custom RAGAS visualizations (e.g. faithfulness-over-versions trend lines); or the Streamlit exercise itself. If built: Streamlit, reads **only** via the Langfuse SDK (live) and/or local `evals/results/*.json` (eval history) — no new datastore. I write it directly on request (CLAUDE.md §4).

**Recommendation:** ship Phases 0–5 on Langfuse's UI, then decide. You'll likely want at most a thin eval-trend page, not a full monitoring dashboard.

## 9. Convergence model

Two code paths (online callback, offline batch), one Langfuse backend. Live traces (with custom perf scores) and eval Dataset Runs (with RAGAS scores) coexist, so one place answers both "how is it behaving now?" and "is the next version better?". The optional dashboard reads both through the SDK. Eval requests, because they go through the real API, also flow through the observability layer — so running an eval automatically exercises monitoring too.

## 10. Repo layouts

**`personal-rag-system` (existing — additions only):**
```
app/
├─ ...                    # config.py, llm.py, ..., chains.py, api.py, main.py (existing)
└─ observability.py       # NEW (only addition): Langfuse handler + OllamaLatencyCallback
# call-site edit in chains.py / api.py to attach callbacks + metadata
# new env vars only: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
```

**`rag-eval-observability` (this repo):**
```
rag-eval-observability/
├─ infra/
│  └─ langfuse/
│     ├─ docker-compose.yml   # self-hosted Langfuse (v3 default, v2 fallback)   [you write]
│     └─ .env.example
├─ contract/
│  └─ README.md               # the API contract a RAG system must satisfy (§4)
├─ evals/                      # offline evaluation                              [guide-then-review]
│  ├─ datasets/
│  │  └─ golden_set.jsonl
│  ├─ ragas_setup.py          # local Ollama judge + embeddings wrappers
│  ├─ metrics.py              # metric selection + custom refusal-correct check
│  ├─ run_eval.py             # call API (non-streaming) → RAGAS → sync → save
│  ├─ langfuse_sync.py        # dataset upload + run/score push
│  └─ results/                # local JSON/CSV history (version-controlled)
├─ dashboard/                 # OPTIONAL Streamlit view                          [I write on request]
│  └─ app.py
├─ guides/                    # build-guides I write for the learning parts
├─ README.md                  # portfolio front door
├─ CLAUDE.md
└─ ARCHITECTURE.md
```

## 11. Build order (phased)

| Phase | Repo | Deliverable | Mode | Done when |
|---|---|---|---|---|
| **0** | this | `infra/langfuse/` up; project + API keys | You write | UI reachable; keys in hand |
| **1** | RAG | `observability.py` (Langfuse + Ollama-latency callbacks) wired at the chain call site; version/session/stream tags | Guide → you implement → review | A real chat shows a trace with retrieval + Ollama spans **and** TTFT/TPOT/TPS scores |
| **2** | this | Register Ollama model in Langfuse with illustrative input/output pricing | You write (config) | Cost panels populate |
| **3** | this | `golden_set.jsonl` (~25 items incl. refusal cases) | You author; I review coverage | Set covers factual/multi-hop/summary/refusal with ground truth |
| **4** | this | `evals/` harness (`run_eval.py`, `ragas_setup.py`, `metrics.py`, `langfuse_sync.py`) | Guide → you implement → review | A run yields RAGAS scores + a Langfuse Dataset Run + a local results file |
| **5** | this | Regression loop in use | — | Change RAG → run eval → read the delta in Langfuse |
| **6** | this | *(Optional)* `dashboard/app.py` + polish `README.md` | I write on request | Only if Langfuse's UI leaves a gap |

*(There is no "expose retrieved contexts" phase — the API already returns them.)*

## 12. Tech / versions / gotchas

| Item | Note |
|---|---|
| Langfuse | Self-hosted, MIT; v3 default / v2 lighter fallback. Read via SDK only. |
| Langfuse Python SDK | v3.x — note v2→v3 breaking change (handler import path, score creation). Verify signatures. |
| RAGAS | v0.2+ — `EvaluationDataset`/`evaluate()` API differs from ≤0.1. Verify signatures. |
| Integration | Native Langfuse `CallbackHandler` + custom `OllamaLatencyCallback`, both on the chain. |
| Ollama timing fields | `load_duration`, `prompt_eval_count`, `prompt_eval_duration`, `eval_count`, `eval_duration`, `total_duration` (ns). Read via `response_metadata`; verify names against `langchain-ollama` version. |
| TTFT | True (stream on) vs. proxy `load_duration + prompt_eval_duration` (stream off). Tag the mode. |
| Cost | Illustrative only — Langfuse model-pricing config, separate input/output prices, modeled on a real model. |
| Judge LLM | Local Ollama default (some jitter) or hosted (stabler, small cost). Consistent per comparison. |
| Eval transport | HTTP, non-streaming, fixed golden set. No `--mode import` switch (unneeded complexity). |
| BM25 `refresh()` | In-memory, not persisted → eval-over-HTTP uses the running server's warm index. |
| Trace volume | Sparse → log 100%, no sampling. Eval adds ~25 traces/run. Trivial storage. |
| Judge jitter | Don't over-read small deltas; re-run borderline evals; trend over single numbers. |

## 13. Revisitable defaults (sensible now, easy to change later — not blockers)

These are chosen defaults, not open questions; flagged only so you know where the easy dials are.
1. **Langfuse v3 vs v2** (§5) — v3 default; drop to v2 if RAM-constrained.
2. **Judge: local vs hosted** (§7.4) — local Ollama default; switch the judge alone to hosted for stabler metrics if wanted.
3. **Custom dashboard** (§8) — deferred to Phase 6; likely a thin eval-trend page at most.
4. **Golden-set size** (§7.1) — ~25 to start; grow deliberately (and reset comparisons when you do).

---

*Kick-off order: **Phase 0** (self-host Langfuse) → **Phase 1** (the observability hook guide). Everything above is decided; these defaults are the only dials left, and all of them can be turned later without rework.*
