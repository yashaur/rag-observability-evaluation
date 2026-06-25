# Build-guide: one trace per request (the handler-owns-trace pattern)

> **Updated after implementation.** This supersedes the earlier "Approach A / B (wrapper span)"
> draft — that approach *fought* the Langfuse handler and was discarded. What's below is the
> design that actually shipped, grounded in `personal-rag-system/app/chains.py` +
> `app/observability.py` and the installed `langfuse 4.11.0`.
>
> For the full mental model (what a trace/span/score is, how the callback bridge works), read
> [observability-reference.md](observability-reference.md). This file is the *focused* record of
> one problem: getting **exactly one well-named trace per request**.

---

## 1. The goal and the naive problem

Goal: each `/query` (non-stream) and `/query/stream` produces **one** Langfuse trace, correctly
named, containing every step (condense, retrieval, generation) and the latency scores — not two
or three disconnected traces.

A Langfuse trace is created per **top-level run**. A request makes *two* separate top-level
LangChain calls — condense, then generation:

```python
standalone_chain.invoke(...)          # top-level run #1  → its own trace
single_turn_chain.invoke(...)         # top-level run #2  → its own trace
```

Two roots → two traces. They share a `session_id` so they group in the Sessions view, but
they're not one trace. (Streaming is the same: the retrieval `.invoke()` and the generation
`.stream()` are two roots.) We split the calls deliberately — to keep `OllamaLatencyCallback`
labelled per step, and (in streaming) to emit sources before tokens — so the fix can't be "stop
splitting." We need the two roots to land in **one** trace.

---

## 2. The two facts about the v4 handler that decide the design

### Fact 1 — what unifies a trace is the `trace_id`, not the handler object
The v4 `CallbackHandler` is a *stateless bridge*. It doesn't hold the trace; it forwards
LangChain events into Langfuse, attaching each emitted observation to the trace identified by
its `trace_context`. So two observations belong to the same trace because they carry the **same
`trace_id`**, not because the same Python object emitted them. The handler is a courier of the
id; give every call a courier carrying the same id and they all land in one trace.

→ This is why the handler must be **per-request, not a cached singleton**: a module-level
singleton freezes the *first* request's `trace_id` and dumps every later request into that dead
trace. Build a fresh handler each request.

### Fact 2 — passing `trace_context` makes the handler *own* the trace (AS_ROOT)
The handler's docstring: `trace_context` is for *"setting a custom trace id for the root
LangChain run."* Whenever a `trace_context` carries a `trace_id`, the span the handler creates is
flagged `AS_ROOT=True` (`langfuse.internal.as_root`, set in `client.start_observation`). That
flag tells Langfuse "this run **is** the trace root" — so the handler's run drives the trace's
identity (name included).

→ Consequence: **don't wrap the handler in your own parent span.** We tried minting a
`stream-request` span via `start_as_current_observation` / `start_observation` and parenting the
handler under it. Because the handler's run is `AS_ROOT`, *it* won the trace name and the wrapper
showed up as a confusing extra node — the trace displayed as **"RunnableSequence"** with
`stream-request` buried inside. The wrapper fought the handler and lost.

---

## 3. The two real obstacles the wrapper was (badly) trying to solve

### 3a — ambient OTel context dies across a generator `yield`
The reason we can't just rely on `start_as_current_observation` setting the "current" context
and letting the handler pick it up ambiently: streaming is a **generator**. Starlette's
`StreamingResponse` drives it through anyio, resuming the generator in a *different*
`contextvars.Context` than the one the `with` block entered. So the ambient "current span" set
inside the `with` is not active when the generation's spans are created during token streaming →
those spans detach and form their own trace. Worse, the context manager's `__exit__` calls
`otel_context.detach(token)` in the wrong context and logs **`Failed to detach context`**.

So for streaming we **cannot** depend on ambient context; we **must** pass an explicit
`trace_id` to the handler. And we should avoid OTel context managers (`start_as_current_observation`,
`propagate_attributes`) in the streaming path entirely, since their `attach`/`detach` straddles
the `yield`.

### 3b — the trace name comes from `metadata`, not the span name
The handler names the trace from a **LangChain config metadata** key, `langfuse_trace_name`
(read in `_parse_langfuse_trace_attributes`). If you don't pass it, the name falls back to the
root run's name → `"RunnableSequence"`. Setting `langfuse.trace.name` on some *other* span (our
wrapper) doesn't win, because the `AS_ROOT` handler run is the one that defines the trace.

---

## 4. The pattern we use: handler-owns-trace + config metadata

Stop wrapping. Mint a trace id up front, give the handler that id, and feed every trace
attribute through the handler's documented channel — `config["metadata"]`. No wrapper span, no
`propagate_attributes`, so **no context managers straddle the yield** and there's no detach
noise.

**Handler factory** (`app/observability.py`):
```python
def get_langfuse_handler(trace_id: str) -> CallbackHandler:
    return CallbackHandler(trace_context = {'trace_id': trace_id})   # per-request, no caching
```

**Streaming call-site** (`app/chains.py`, condensed):
```python
client   = get_client()
trace_id = client.create_trace_id()                  # mint an id with NO span (staticmethod)
handler  = get_langfuse_handler(trace_id = trace_id)  # one handler, shared by every call below

lf_meta = {
    'langfuse_trace_name': 'stream-request',          # ← names the trace (Fact 2 / §3b)
    'langfuse_user_id'   : 'yashaur',
    'langfuse_tags'      : ['live', 'stream:on'],
}
if session_id:
    lf_meta['langfuse_session_id'] = session_id        # handler ignores it if not a str

# call #1 — condense + retrieve
retrieval_dict = (standalone_chain | retrieval_chain).invoke(
    {'question': question, 'chat_history': chat_history or []},
    config = {'callbacks': [handler, OllamaLatencyCallback(label='condense', trace_id=trace_id)],
              'metadata': lf_meta, 'run_name': 'condense'})

yield {'type': 'sources', 'sources': retrieval_dict['retrieved_chunks']}

prompt = (context_dict | rag_prompt).invoke(retrieval_dict)   # no handler → no extra root run

# call #2 — generation (real streaming)
for chunk in stream_generation_chain.stream(
        prompt,
        config = {'callbacks': [handler, OllamaLatencyCallback(label='answer', trace_id=trace_id)],
                  'metadata': lf_meta, 'run_name': 'answer'}):
    yield {'type': 'token', 'token': chunk}
```

Non-streaming (`answer_question`) is identical in spirit — same `create_trace_id` + handler +
`lf_meta` (with `'langfuse_trace_name': 'non-stream-request'`, `'stream:off'`), applied to
`standalone_chain.invoke` and `single_turn_chain.invoke`.

What each piece does:
- `client.create_trace_id()` — a stable id, minted **without** creating a span (so nothing
  competes with the handler for `AS_ROOT`).
- one **shared** `handler` carrying that id → both calls land in the same trace.
- `metadata` keys (`langfuse_trace_name`, `langfuse_session_id`, `langfuse_user_id`,
  `langfuse_tags`) → the handler sets these as trace attributes on its root run.
- `run_name` → labels each top-level run, so the tree reads `condense` / `answer` instead of
  two `RunnableSequence`s.
- scores stay on `create_score(trace_id=trace_id, ...)` inside `OllamaLatencyCallback` — explicit
  id, so they attach correctly even though we're not inside any "current trace" context.
- the prompt-templating `.invoke()` gets **no** handler (it's not an LLM call; with a
  `trace_context` handler it would spawn a third `AS_ROOT` root).

---

## 5. The honest trade-off

You do **not** get a single synthetic root span. The trace `stream-request` holds **two
top-level observations** — `condense` and `answer` — because each `.invoke()`/`.stream()` is its
own root run and both reuse the same `trace_context` (the handler returns it for every root run).
There's no clean way to nest two separate calls under one named wrapper without re-triggering the
`AS_ROOT` conflict from §2. For two genuinely distinct LLM operations, two clearly-labelled runs
under one named trace is the honest, library-aligned view — and it's what the handler is built to
produce.

---

## 6. Verification

1. **Streaming, multi-turn** `/query/stream` (send `session_id` + `chat_history` so the
   condenser runs): in Langfuse — **one** trace named `stream-request`, with two top-level runs
   `condense` and `answer`; `condense_*` and `answer_*` scores attached; `session_id`, tags
   `[live, stream:on]`, and the `release` SHA present. **No `Failed to detach context`** in the
   server logs.
2. **Non-streaming** `/query` (multi-turn): same shape, trace named `non-stream-request`, tags
   `[live, stream:off]`.
3. **Single-turn**: expect **no** `condense_*` scores — the condenser LLM doesn't run with no
   history, so the `condense` run is empty. (Not a bug.)
4. No `RunnableSequence`-named traces, and no stray prompt-templating root.

When a request shows up as one named trace with both runs and the generation scores attached,
trace unification is done and Phase 1 is complete.
