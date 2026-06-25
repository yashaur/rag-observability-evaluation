# rag-eval-observability

A standalone **evaluation + observability** harness for a Retrieval-Augmented Generation
system. It treats the RAG system as a **replaceable component behind an HTTP contract** —
any system that returns `answer` + `retrieved_contexts` can be observed and evaluated by
this repo, unchanged.

> The RAG system it observes lives in a separate repo (`personal-rag-system`). This repo
> never imports its code — it talks to it **only over HTTP**. That decoupling is the point.

## Two concerns, one backend

Two deliberately separate code paths converge into a single self-hosted **Langfuse** store,
so there is one place to look:

- **Observability (online):** every real conversation — each retrieval, each Ollama call,
  each full request — is auto-traced to Langfuse with detailed latency/token metrics
  (TTFT, TPOT, TPS, model-load time) and an illustrative cost figure. *"What's happening
  right now?"*
- **Evaluation (offline):** a fixed, version-controlled golden set, re-run through
  **RAGAS** after every change, to quantify whether the change made things **better or
  worse**. *"Did my change help?"*

The throughline is **eval-driven development**: change the RAG → run the eval → compare
against the previous version in Langfuse → keep or revert.

## Repo map

```
infra/langfuse/   self-hosted Langfuse stack (Docker)         [plumbing]
contract/         the HTTP API contract a RAG system must meet
evals/            offline RAGAS harness + fixed golden set     [guide-then-review]
  datasets/       golden_set.jsonl (the single yardstick)
  results/        version-controlled run history (JSON/CSV)
dashboard/        OPTIONAL Streamlit view (Phase 6)
guides/           build-guides for the learning parts
CLAUDE.md         project "constitution" — purpose + collaboration model
ARCHITECTURE.md   technical blueprint — the what-to-build
```

## Build order (ARCHITECTURE §11)

| Phase | Deliverable |
|---|---|
| **0** | Stand up self-hosted Langfuse (`infra/langfuse/`); create project + API keys |
| **1** | Wire the observability hook (in the **RAG repo**): Langfuse + custom Ollama-latency callbacks; tag version / session / stream mode |
| **2** | Register the Ollama model in Langfuse with illustrative input/output pricing (cost panels) |
| **3** | Author the golden set (~25 items incl. refusal / out-of-KB cases) |
| **4** | Build the `evals/` harness: HTTP → RAGAS → Langfuse Dataset Run → local results file |
| **5** | Run the regression loop: change RAG → eval → read the delta → decide |
| **6** | *(Optional)* custom dashboard, only where Langfuse's UI leaves a gap |

## Quickstart (once built)

```bash
# Phase 0 — self-host Langfuse v3 (6 services: web, worker, postgres, clickhouse, redis, minio)
cp infra/langfuse/.env.example infra/langfuse/.env       # then generate secrets (openssl rand -hex 32)
docker compose --env-file infra/langfuse/.env -f infra/langfuse/docker-compose.yml up -d
# wait ~2-3 min for langfuse-web to log "Ready", then open http://localhost:3000,
# create a project, copy the API keys back into infra/langfuse/.env

# Eval deps
pip install -r requirements.txt

# Phase 4+ — run an evaluation against the running RAG API
python evals/run_eval.py --api http://localhost:8000/chat --version "<git_sha>-<desc>"
```

## Status

Skeleton scaffolded per ARCHITECTURE.md §10. **Phase 0 infra is ready**: `infra/langfuse/`
holds an adapted official **Langfuse v3** compose (six services) + `.env.example` — boot it
with the Quickstart above. The `evals/` Python files and the observability hook remain
**stubs** by design — written guide-then-review (see `guides/`) as part of the learning goal.

## Stack

Self-hosted **Langfuse v3** (MIT; full 6-service stack — chosen for hands-on architecture
learning, see ARCHITECTURE §5) · RAGAS · LangChain · local Ollama (LLM + judge, real cost $0;
cost is an illustrative demonstration metric only). All free, open-source, self-hosted.
