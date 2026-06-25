# Build-guide: the observability hook (Phase 1)

> **Mode: guide-then-review.** This is *your* code to write (CLAUDE.md §4). Below are the
> concepts, the shapes for the tricky parts, the gotchas specific to *your* RAG repo, and a
> verification block. I deliberately do **not** give finished implementations — fill the
> bodies yourself, then I review. Code blocks marked `SHAPE` are skeletons to adapt, not
> copy-paste.
>
> **Where this lands:** the `personal-rag-system` repo. This is the *only* code this project
> adds there (ARCHITECTURE §2/§6): one new file `app/observability.py`, a few call-site
> edits, one schema field, and three env vars. Nothing else.
>
> **Verify-as-you-go:** Langfuse (v2→v3) and `langchain-ollama` both move fast. Every
> signature here is the correct *shape* for v3 — confirm against what's installed by printing
> objects and reading the trace in the UI. Don't trust a signature you haven't seen run.

---

## 0. What you're building and why

Two callbacks ride along on each chain call, both writing into the one Langfuse backend you
stood up in Phase 0:

1. **The native Langfuse `CallbackHandler`** — auto-captures the whole run tree as a trace:
   the retriever step, the Ollama LLM call, nested chains, inputs/outputs, end-to-end
   latency. Zero manual span code. This is the "what happened" view.
2. **A custom `OllamaLatencyCallback`** — the perf metrics Langfuse doesn't capture natively
   (TTFT, TPOT, TPS, model-load time). It reads Ollama's timing numbers and writes them as
   **numeric scores** on the trace, so they're queryable/aggregatable in dashboards.

Plus metadata on every trace: **session id** (groups a multi-turn chat), **user id**,
**RAG version** (git SHA), and a **streaming-mode tag**.

---

## 1. Prerequisites (in the RAG repo)

- **Install the SDK** into the RAG repo's `.venv`: `pip install "langfuse>=3"`. (Your
  instance is `3.195.0`; the v3 OTel SDK needs platform `>=3.63.0` ✓.)
- **Env vars** in `personal-rag-system/.env` — use the **same local keys** you created in
  Phase 0 (from the eval repo's `infra/langfuse/.env`, Group B):
  ```
  LANGFUSE_PUBLIC_KEY=pk-lf-...        # the LOCAL key
  LANGFUSE_SECRET_KEY=sk-lf-...        # the LOCAL key
  LANGFUSE_BASE_URL=http://localhost:3000
  ```
  Note: `LANGFUSE_BASE_URL` is the v3-preferred name (`LANGFUSE_HOST` still works as a
  legacy alias). Make sure your `app/config.py` / pydantic-settings either loads these or you
  read them from the environment directly — the `CallbackHandler()` reads them from the
  process env, so they just need to be present when the app starts.

---

## 2. Where this plugs into YOUR code (the map)

You already have clean call-sites — that's most of the battle:

| File | Thing | Role for us |
|---|---|---|
| `app/llm.py` | `llm`, `condenser_llm` (`ChatOllama`) | the LLM whose Ollama timings we read |
| `app/chains.py` | `answer_question()` → `rag_chain_final.invoke(...)` (~L125) | **non-streaming call-site** |
| `app/chains.py` | `stream_answer_question()` → `stream_generation_chain.stream(prompt)` (~L159) | **streaming call-site** |
| `app/api.py` | `/query` and `/query/stream` endpoints | where session id / version / mode enter |
| `app/schemas.py` | `QueryRequest` | add a `session_id` field here |

Both call-sites need the two callbacks attached. The metadata (session/version/mode) flows
from `api.py` → into `answer_question` / `stream_answer_question` (you'll add params) → into
the `config=`.

---

## 3. Part A — the native callback (`app/observability.py`)

Create `app/observability.py`. Start with a factory/singleton for the handler.

```python
# SHAPE — app/observability.py
from langfuse.langchain import CallbackHandler   # v3 import path (verify it imports)

_handler = None
def get_langfuse_handler() -> CallbackHandler:
    global _handler
    if _handler is None:
        _handler = CallbackHandler()   # reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL from env
    return _handler
```

Then attach it at the **non-streaming** call-site. The handler + metadata go in `config`:

```python
# SHAPE — inside answer_question(), replacing the bare .invoke
result = rag_chain_final.invoke(
    {"question": question, "chat_history": chat_history or []},
    config={
        "callbacks": [get_langfuse_handler()],   # (+ the latency callback from Part C)
        "metadata": {
            "langfuse_session_id": session_id,    # groups a multi-turn chat
            "langfuse_user_id": "me",
            "langfuse_tags": ["live", f"stream:off"],
            # version → see Part B
        },
    },
)
```

The exact metadata keys are `langfuse_session_id`, `langfuse_user_id`, `langfuse_tags`
(verified against v3 docs). Get them wrong and they're silently ignored.

> ### ⚠️ Gotcha #1 — nested `.invoke()` and callback propagation (read this carefully)
> Your chain isn't a single flat pipe. `add_timer()` wraps each sub-chain in a
> `RunnableLambda` whose body calls `original_chain.invoke(chain_input)` **without passing
> config**. Likewise `standalone_question()` calls `timed_condense_chain.invoke(...)`
> internally. In older LangChain, a manual nested `.invoke()` that doesn't forward `config`
> **breaks callback propagation** — the retriever/LLM spans would not appear under your
> trace. Modern `langchain-core` auto-propagates config via context variables for *sync*
> code, so it may "just work." **This is the #1 thing to verify** (step 6): look at the trace
> tree in Langfuse. If retrieval/generation spans are missing or detached, the fix is to
> thread `config` through `add_timer`/`standalone_question` (accept a `config` arg and pass
> it to the inner `.invoke(..., config=config)`).

---

## 4. Part B — session grouping & version tagging (the missing piece)

Your API has **no `session_id` today** — multi-turn state is carried as `chat_history` only.
For Langfuse to group a conversation into one session, you need a stable id per chat.

- **Add a field** to `QueryRequest` in `app/schemas.py`: `session_id: str | None = None`.
- **Generate it on the frontend** (one uuid per chat window) and send it with each turn —
  minimal change, and the frontend already owns the conversation. (Alternative: mint one
  server-side and return it; more plumbing. Recommend the frontend approach.)
- Thread it: `api.py` reads `query_request.session_id` → passes to `answer_question(...,
  session_id=...)` → into the `metadata`.

**RAG version (`release`).** ARCHITECTURE wants every trace tagged with the git SHA. Cleanest:
set it once at startup and let the SDK attach it.

```python
# SHAPE — compute once (e.g. in observability.py or config)
import subprocess
def rag_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"
```
Set `LANGFUSE_RELEASE=<sha>` in the environment before the app starts (the SDK reads it
automatically as the trace `release`), **or** put it in `metadata`. Pick one and verify it
shows on the trace. (Env var is less per-call clutter.)

**Streaming-mode tag.** Streaming is endpoint-based in your app, not a runtime flag — so just
tag by which path you're in: `stream:off` for `/query`, `stream:on` for `/query/stream`,
added to `langfuse_tags`.

---

## 5. Part C — the custom `OllamaLatencyCallback` (the perf metrics)

This is the learning-rich part. It's a `langchain_core.callbacks.BaseCallbackHandler`.

```python
# SHAPE — app/observability.py
from langchain_core.callbacks import BaseCallbackHandler
from time import perf_counter
from langfuse import get_client   # v3: the singleton client; verify import

class OllamaLatencyCallback(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        self._t_start = perf_counter()
        self._first_token_t = None

    def on_llm_new_token(self, token, **kwargs):
        # streaming path only: stamp the FIRST token = true TTFT
        if self._first_token_t is None:
            self._first_token_t = perf_counter()

    def on_llm_end(self, response, **kwargs):
        # 1) pull Ollama's timing fields (see gotcha #2 for WHERE they live)
        md = ...  # response_metadata dict from the generation
        load_ns       = md.get("load_duration", 0)
        prompt_n      = md.get("prompt_eval_count", 0)
        prompt_ns     = md.get("prompt_eval_duration", 0)
        eval_n        = md.get("eval_count", 0)
        eval_ns       = md.get("eval_duration", 0)

        # 2) derive metrics — MIND THE UNITS (gotcha #3)
        ttft = (self._first_token_t - self._t_start) if self._first_token_t \
               else (load_ns + prompt_ns) / 1e9          # true vs proxy, in seconds
        tpot = (eval_ns / eval_n) / 1e9 if eval_n else 0  # seconds per output token
        tps  = eval_n / (eval_ns / 1e9) if eval_ns else 0 # output tokens per second

        # 3) write them as numeric scores on the current trace (gotcha #4)
        client = get_client()
        client.score_current_trace(name="ttft_s", value=ttft, data_type="NUMERIC")
        client.score_current_trace(name="tpot_s", value=tpot, data_type="NUMERIC")
        client.score_current_trace(name="tps",    value=tps,  data_type="NUMERIC")
        # also: model_load_s, input_tokens (prompt_n), output_tokens (eval_n)
```

> ### ⚠️ Gotcha #2 — *where* Ollama's timing fields live
> With `langchain-ollama`, the ns timing fields (`load_duration`, `prompt_eval_count`,
> `prompt_eval_duration`, `eval_count`, `eval_duration`, `total_duration`) come back in the
> message's `response_metadata` (and/or `LLMResult.llm_output`). The exact path inside
> `on_llm_end`'s `response` (an `LLMResult`) needs confirming — likely
> `response.generations[0][0].message.response_metadata`. **Print `response` once** and read
> it before trusting any path. Streaming may surface these only on the final chunk.

> ### ⚠️ Gotcha #3 — Ollama durations are NANOSECONDS
> Every `*_duration` is in ns. Convert (`/ 1e9`) before computing or reporting seconds, or
> your TPS/TPOT will be off by a billion. This is the single most common mistake here.

> ### ⚠️ Gotcha #4 — does `score_current_trace()` find the trace from inside a callback?
> `score_current_trace()` attaches to the *active* Langfuse trace context. Both callbacks run
> during the same `chain.invoke`, and the native handler creates that trace — so in the
> common sync path the context should be active and the score lands on the right trace.
> **Verify this** (step 6): confirm the scores appear on the *same* trace as the spans, not
> on a stray/empty trace. If they don't, the fallback is to capture the trace id explicitly
> (e.g. `get_client().get_current_trace_id()` at `on_llm_end`, or read it off the native
> handler) and use `create_score(trace_id=..., name=..., value=..., data_type="NUMERIC")`.

> ### ⚠️ Gotcha #5 — the condenser will also fire this callback
> `condenser_llm` is also `ChatOllama`. If the latency callback is attached broadly, the
> condense step emits its own `on_llm_end` and would overwrite your generation scores. Decide
> scope: attach `OllamaLatencyCallback` so it only measures the **main answer generation**
> (e.g. instantiate per-call and guard, or attach only on the generation sub-chain), not the
> condenser. Simplest: only score on the longest/last LLM call, or tag scores distinctly.

---

## 6. Part D — wire both callbacks at both call-sites

- **Non-streaming** (`answer_question` → `/query`): attach `[get_langfuse_handler(),
  OllamaLatencyCallback()]` in `config`. TTFT uses the **proxy** (no token stream).
- **Streaming** (`stream_answer_question` → `/query/stream`): attach the same callbacks to
  the `.stream(...)` call (`stream_generation_chain.stream(prompt, config={...})`). Here
  `on_llm_new_token` fires, so you get **true TTFT**. Tag `stream:on`.
- Thread `session_id` (and optionally version) from `api.py` into both functions as new
  params. Keep their current signatures working (default `session_id=None`).

> Instantiate `OllamaLatencyCallback` **per call** (it holds per-request timing state like
> `_t_start`). The Langfuse handler can be the shared singleton.

---

## 7. Verification block (do these in order)

1. **Imports work:** `python -c "from langfuse.langchain import CallbackHandler; from langfuse import get_client; print('ok')"` in the RAG venv.
2. **Env loaded:** start the RAG API; no auth errors in logs. (Wrong/missing keys → 401s to Langfuse.)
3. **One non-streaming call:** POST a question to `/query`. In the Langfuse UI (`localhost:3000`):
   - a **trace** appears, with a **retriever** span and an **Ollama LLM** span nested under it
     (this is the Gotcha #1 check — if spans are missing/detached, thread `config`).
   - the trace carries your **session id**, **version**, and `stream:off` tag.
   - **scores** `ttft_s`, `tpot_s`, `tps` (etc.) are attached **to that same trace** (Gotcha #4).
   - sanity-check magnitudes: TPS in the right ballpark for your model (Gotcha #3 — if it's
     ~1e-9 or ~1e9 off, you missed a ns conversion).
4. **Multi-turn grouping:** send two turns with the **same** `session_id`; confirm both land
   in **one session** in the Sessions view.
5. **Streaming call:** POST to `/query/stream`; confirm a trace with `stream:on`, and that
   **true TTFT** (first-token timing) is present and plausibly smaller than the proxy.
6. **Condenser scope (Gotcha #5):** in multi-turn, confirm the condense LLM call didn't
   clobber your generation scores.

When all six pass, Phase 1 is done: every real chat produces a full trace with retrieval +
Ollama spans **and** the TTFT/TPOT/TPS scores (ARCHITECTURE §11 Phase 1 "done when").

---

## 8. Scope guardrail

Touch only: `app/observability.py` (new), the two call-sites in `app/chains.py`, the
`session_id` field in `app/schemas.py`, the param threading in `app/api.py`, and the three
env vars. **No eval code, no golden set, no infra** in the RAG repo — that all lives in this
repo (CLAUDE.md §6).

---

## References (verified for v3, June 2026)

- Langfuse LangChain integration (v3 import, metadata keys): https://langfuse.com/docs/integrations/langchain/tracing
- Custom scores (`score_current_trace`): https://langfuse.com/docs/scores/custom
- Env var names (`LANGFUSE_BASE_URL` preferred; `LANGFUSE_HOST` legacy alias): https://langfuse.com/self-hosting/configuration
