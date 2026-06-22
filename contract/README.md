# API Contract — the swappability interface

> This is the linchpin of the two-repo design (ARCHITECTURE.md §4). The eval harness in
> this repo talks to the RAG system **only over HTTP**. Any RAG system that satisfies the
> contract below can be evaluated by the harness **unchanged** — that is what makes the
> RAG system a replaceable component.

## Transport

- **Protocol:** HTTP (JSON).
- **Default endpoint:** `POST /chat` on the running RAG API (FastAPI, `app.main:app`, port `8000`).
- **Eval mode:** the harness always calls with `stream=false` for comparable, clean
  latency/timing data (CLAUDE.md §0 #9).

## Request

```jsonc
POST /chat
{
  "question":   "string",        // required — the user question
  "stream":     false,           // bool — eval pins this to false
  "session_id": "string | null"  // optional — groups a multi-turn chat into one session
}
```

## Response (non-streaming — what the eval needs)

```jsonc
{
  "answer": "string",                  // required — the generated answer
  "retrieved_contexts": ["string", ]   // required — the ACTUAL chunk texts used,
                                       //   needed for retrieval & faithfulness metrics
}
```

The existing `personal-rag-system` API already returns `retrieved_contexts`, so it
conforms today — **no change is needed in the RAG repo for evaluation** (ARCHITECTURE §1, §4).

## Field-name agreement

The harness reads exactly the field names above. If a future swapped-in system uses
different names, either:

1. rename its fields to match this contract, or
2. adapt the thin HTTP client in `evals/run_eval.py` to map them.

Document any deviation here so the contract stays the single source of truth.

## Honest scope of swappability (ARCHITECTURE §3 / §10)

- **Evaluation is fully RAG-agnostic** — it is black-box over HTTP; only this contract matters.
- **Observability is modular only at the edges** — end-to-end latency, request/response,
  and illustrative cost transfer to any swapped-in system for free, but the internal
  retriever / LLM / Ollama-timing spans come from the in-process hook
  (`app/observability.py`) that lives in the RAG repo. A different RAG system emits those
  internal spans only if it also carries the hook. Don't oversell total modularity.
