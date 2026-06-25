# CLAUDE.md — RAG Eval & Observability

> The "constitution" for this project: what we're building, how we work together, and the rules of engagement. Concrete technical specifics live in **ARCHITECTURE.md** — this file is the *why* and *how we collaborate*; that file is the *what to build*.
>
> **Working name for this repo:** `rag-eval-observability` (rename freely). **The RAG system it observes** lives in a separate repo: `personal-rag-system`.

---

## 0. Confirmed decisions (settled — do not re-litigate)

Everything below was discussed and explicitly decided. A future session should treat these as fixed facts, not open questions.

| # | Decision | Confirmed value |
|---|---|---|
| 1 | RAG framework | **LangChain** (so: native Langfuse LangChain callback for tracing). |
| 2 | LLM | **Local Ollama.** Real cost = \$0. |
| 3 | Cost metric | Tracked for **demonstration only**, via Langfuse's model-pricing config using *illustrative* input/output prices modeled on a real model. Not hardcoded in app. |
| 4 | Other metrics | Track tokens, end-to-end/retrieval/generation latency, **TTFT, TPOT, TPS**, model-load time. (Details in ARCHITECTURE.md §6.) |
| 5 | Repo structure | **Two separate repos.** This one is a standalone CV project; the RAG system is treated as an external service. |
| 6 | Eval ↔ RAG interface | **HTTP only.** The eval harness calls the RAG app's API; it never imports its code. This is what keeps the RAG system swappable. |
| 7 | RAG API already returns retrieved contexts | **Yes** → the RAG repo needs **zero** changes for evaluation. The only code that lands in the RAG repo is the observability hook. |
| 8 | Instrumentation path | **Native Langfuse callback** (not OpenTelemetry). OTel is a deliberate future learning exercise, not part of this build. |
| 9 | Streaming | RAG app has a **streaming on/off toggle.** Live traffic uses whatever it's set to; latency capture adapts (true TTFT when streaming, Ollama-derived proxy when not). The **eval harness is pinned to non-streaming** for comparable runs. |
| 10 | Observability backend | **Self-hosted Langfuse v3, run locally** (MIT; stays open-source post the Jan 2026 ClickHouse acquisition), owned by *this* repo, shared with the RAG app via env vars. *Settled 2026-06: v3-local chosen over Cloud-v3 and over v2 — the goal is hands-on familiarity with the v3 architecture (web+worker+Postgres+ClickHouse+Redis+MinIO); Cloud hides it, v2 is the legacy line. 16 GB RAM trade accepted.* |
| 11 | Eval library | **RAGAS.** |
| 12 | RAGAS judge | **Local Ollama** by default (free, fully self-hosted), accepting some judge jitter. Hosted judge is an option if stabler numbers are ever wanted. |

## 1. The project idea

A standalone **evaluation + observability** project for a Retrieval-Augmented Generation system. Two distinct concerns, deliberately separate code paths, converged into a **single self-hosted Langfuse backend** so there's one place to look.

- **Observability (online):** every real conversation with the RAG app — each retrieval, each Ollama call, each full request, with detailed latency/token metrics — is automatically traced to Langfuse. The "what's happening / what just went wrong" view, running continuously on live (sparse) personal traffic.
- **Evaluation (offline):** a fixed, version-controlled golden set of questions, re-run through RAGAS after every change to the RAG system, to see — quantitatively — whether the change made it **better or worse**. The "did my change help?" view, run on demand.

The throughline is **eval-driven development**: change the RAG → run the eval → compare against the previous version → keep or revert. Monitoring shows how the current version behaves; evaluation tells you whether the next version is an improvement.

## 2. Two-repo architecture (what lives where)

The defining property: this project talks to the RAG system **only over HTTP**, so the RAG system is a **replaceable component behind an API contract**. Any system honoring that contract (request → answer + retrieved contexts) can be swapped in and evaluated by the same harness, unchanged.

- **`personal-rag-system` (existing RAG repo)** — gets exactly **one** small, self-contained addition: the observability hook (`app/observability.py` + the call-site wiring). Nothing else. No eval code, no golden set, no infra. (Eval needs no changes here because the API already returns retrieved contexts.)
- **`rag-eval-observability` (this repo)** — owns everything substantial: the Langfuse self-hosting infra, the golden set, the RAGAS harness, the optional dashboard, the docs, and the API-contract definition.

> **Honest scope note on "swappable":** *Evaluation* is fully RAG-agnostic — it's black-box, all inputs/outputs flow over HTTP. *Observability* is modular at the **edges** (end-to-end latency, request/response, cost transfer to any swapped-in system for free) but **per-implementation on the inside** — the retriever/LLM/Ollama-timing spans come from the in-process hook, so a different RAG system only emits those internal spans if it also carries the hook. This is the nature of the two layers, not a defect; state it plainly, don't oversell total modularity.

See ARCHITECTURE.md §3–§4 for the full design and the contract.

## 3. Goals and non-goals

**Goals**
- Log 100% of real usage to self-hosted Langfuse, multi-turn chats grouped into sessions, every trace tagged with the RAG version and the streaming mode.
- Detailed performance metrics: tokens, TTFT, TPOT, TPS, latencies, plus an illustrative cost figure.
- A repeatable fixed-input eval producing comparable metrics across versions, with regressions surfaced clearly.
- Everything **free, open-source, self-hosted** (Langfuse MIT; RAGAS open; judge can be local Ollama).
- A clean, **portfolio-quality** project — good README, coherent architecture story ("a RAG-agnostic eval & observability harness").
- Treat this as a **learning project**: understand the mechanisms, not just wire up tools.

**Non-goals**
- No production-scale concerns: no sampling, no HA, no horizontal scaling, no premature tuning. Personal, sparse use → **simplicity beats scalability everywhere.**
- Cost is **not** a real metric (Ollama is local). It exists for demonstration only.
- Not replacing Langfuse's built-in UI with a custom dashboard — the dashboard is optional and only earns its place where it adds something the UI doesn't (ARCHITECTURE.md §8).
- No automated CI merge gates (yet). Evaluation is a human-in-the-loop decision aid.

## 4. How we work together (the workflow)

Carried over from the RAG build, applied across the two repos.

**Guide-then-review for the parts I'm learning.** For substantive, learning-relevant code: **I write a detailed markdown build-guide to `guides/<filename>.md`** (concepts + sketches for the tricky parts + gotchas + a verification block) → **I implement it myself** → **you review and I apply fixes** before moving on. You do **not** hand me finished implementations of these parts.

| Component | Repo | Mode | Why |
|---|---|---|---|
| Observability hook (`app/observability.py` + call-site wiring + custom latency callback) | RAG repo | **Guide-then-review** | Touches `app/`; same rule as the RAG backend, and understanding callbacks is learning-relevant. Small but mine to write. |
| `evals/` harness (golden set, RAGAS runner, metrics, Langfuse sync) | this repo | **Guide-then-review** | The core *learning material* — RAGAS, metrics, golden-set design. |
| Self-host infra (`docker-compose.yml`, `.env`) | this repo | **You write directly** | Pure ops/plumbing. |
| Custom Streamlit dashboard (`dashboard/`) | this repo | **You write directly** | I already know Streamlit and asked for frontend to be written for me. |

> This split is your call. If you'd rather I just write the `evals/` harness for you to read and run (or want a guide for the infra), say so and we adjust. The default optimizes for learning the eval methodology hands-on.

## 5. Communication preferences

- **Explain trade-offs in prose, in depth**, with concrete examples and — where it helps — from different angles (e.g. "for the eval's reliability" vs. "for operational simplicity"). I make the call myself in conversation.
- **Do not use multiple-choice / poll prompts** for design forks. Lay out options in prose; I decide. (If I want a quick poll, I'll ask.)
- Prefer fewer, deeper explanations over many shallow ones.

## 6. Do's and Don'ts

**Do**
- **Keep eval and monitoring as separate code paths** but write both into the **one** Langfuse backend — single pane of glass.
- **Talk to the RAG system only over HTTP.** Never import its code. This is what makes it swappable and keeps the two repos decoupled.
- **Keep the golden set fixed and version-controlled.** Changing it resets historical comparisons.
- **Tag every eval run with the RAG version** (git SHA + short description), and **tag live traces with version + streaming mode**.
- **Account for LLM-judge jitter.** RAGAS scores wobble run-to-run; look for consistent movement across the set, re-run borderline evals 2–3×, trust trends over single numbers.
- **Pin the eval harness to non-streaming** (answers are identical to streaming, so RAGAS scores are mode-invariant; non-streaming gives the cleanest latency/timing data).
- **Lean on Langfuse's built-in UI first** for monitoring and eval-run comparison; build custom only where it adds value.
- **Keep the cost assumption as Langfuse config**, labeled illustrative, modeled on a real model's input/output prices.

**Don't**
- **Don't over-engineer for scale.** No sampling, no sharding, no premature ClickHouse tuning. If simpler is enough, simpler wins.
- **Don't compare arbitrary live traces as if they were an eval.** Different questions each time → not comparable. Only the fixed golden set gives clean before/after numbers.
- **Don't fabricate exact library API signatures.** Langfuse (v2→v3) and RAGAS (≤0.1→0.2+) both had breaking rewrites. Give the correct *shape*, then verify against the installed version's docs.
- **Don't add code to the RAG repo beyond the observability hook.** Everything else belongs here.
- **Don't treat cost as a real metric.** Local Ollama = \$0; cost is demonstration only.

## 7. Build order (high level)

Detailed phasing and specifics in **ARCHITECTURE.md §11**. The shape:

0. **Stand up Langfuse** (this repo) — self-host via Docker, create project, get API keys.
1. **Wire the observability hook** (RAG repo) — Langfuse callback + custom Ollama-latency callback; tag version/session/streaming-mode. Confirm a real chat produces a full trace with the latency metrics.
2. **Register illustrative pricing** (Langfuse config) — so cost panels populate. *(No backend change needed for eval — the API already returns contexts.)*
3. **Build the golden set** (this repo) — ~25 representative questions incl. refusal/"not in the KB" cases, with ground truth where feasible.
4. **Build the eval harness** (this repo) — `evals/run_eval.py`: call the RAG via HTTP → RAGAS scores → push to Langfuse as a Dataset Run tagged by version → save a local results file.
5. **Run the regression loop** — change RAG → run eval → compare runs in Langfuse → decide.
6. **(Optional) Custom dashboard** — only if Langfuse's UI leaves a real gap.

---

*See **ARCHITECTURE.md** for the full technical blueprint: the API contract, the Langfuse v3-vs-v2 choice, the instrumentation + metric-capture details, the RAGAS metric set, the data-flow diagram, both repo layouts, and the few revisitable defaults.*
