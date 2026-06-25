# Observability reference: tracing & callback handling, end-to-end

> **What this is.** A complete, reusable reference for how request-level **tracing** works and
> how we wired it into a LangChain + Ollama + Langfuse app — from first-principles vocabulary to
> the exact code, the data flow, every gotcha we hit, and a generalizable template for the next
> project. It is meant to be read top-to-bottom once, then used as a lookup.
>
> **Companion docs** (deeper on one slice each): [observability-concepts.md](observability-concepts.md)
> (the callback-inheritance mechanics), [observability-hook.md](observability-hook.md) (the
> original build checklist), [ollama-latency-callback.md](ollama-latency-callback.md) (the custom
> metrics callback), [unify-traces.md](unify-traces.md) (the one-trace-per-request decision).
> Grounded in `langfuse 4.11.0`, `langchain-core 1.4.7`, `langchain-ollama 1.1.0`, Python 3.14,
> self-hosted Langfuse platform `3.195.0`.

---

## Table of contents

1. [Why observability, and why tracing specifically](#1)
2. [The vocabulary (with this app's concrete mapping)](#2)
3. [How a trace is actually built — the two layers](#3)
4. [The end-to-end data flow](#4)
5. [Instrument #1 — the native Langfuse `CallbackHandler`](#5)
6. [Instrument #2 — the custom `OllamaLatencyCallback`](#6)
7. [The core patterns (the template)](#7)
8. [The gotcha catalogue (decision log)](#8)
9. [A checklist for instrumenting a new project](#9)
10. [Verification & references](#10)

---

<a name="1"></a>
## 1. Why observability, and why tracing specifically

When the app handles a request, a lot happens that you can't see from the outside: condense the
question → retrieve chunks → build a prompt → call the LLM → stream tokens back. When something
is "slow" or "wrong," that tells you nothing about **which step**. Observability is instrumenting
the system so each request leaves behind a **structured, reconstructable record**.

The classic "three pillars":
- **Logs** — timestamped text lines. Good for "what happened at 10:42," bad for "show me this one
  request's whole journey."
- **Metrics** — aggregate numbers over time (avg latency, error rate). Good for dashboards, blind
  to individual requests.
- **Traces** — the detailed, causal story of **one** request as a tree of timed steps. This is
  what answers "where did the 4 seconds go?" and "which retrieval fed this bad answer?"

We build primarily a **tracing** system, and we derive metrics from it (latency scores that
Langfuse aggregates). Two reasons tracing is the right primary tool for a RAG/LLM app:
1. **Causality.** A trace preserves parent→child structure, so you see that *this* retrieval
   produced *these* chunks that went into *that* prompt that yielded *this* answer.
2. **Per-step timing.** LLM latency is multi-dimensional (time-to-first-token, tokens/sec, model
   load). Only a trace with sub-steps lets you attribute time correctly.

---

<a name="2"></a>
## 2. The vocabulary (with this app's concrete mapping)

| Term | Definition | In this app |
|---|---|---|
| **Trace** | the complete record of **one request**, end to end (total duration, overall input/output, status) | one `/query` or `/query/stream` |
| **Observation** | the generic word for any node inside a trace | any step |
| → **Span** | a generic step observation (its own start/end, input/output) | the `condense` run, retrieval |
| → **Generation** | a span specialized for an **LLM call** — also records model, token counts, cost | the Ollama call |
| → **Event** | a zero-duration point marker | (unused here) |
| **Score** | a named measurement attached to a trace/observation — `NUMERIC`, `CATEGORICAL`, or `BOOLEAN` | `answer_ttft`, `answer_tps`, … |
| **Session** | a grouping of related traces under a shared id | one multi-turn chat (`session_id`) |
| **Release / version** | a version label on a trace | the git short SHA |
| **Trace context** | the `{trace_id, parent_span_id}` handle that says "attach to *this* trace" | passed to the `CallbackHandler` |
| **Dataset / Dataset Run** | a fixed input set + one evaluation pass over it | Phase 4 (RAGAS) |

Three distinctions to hold onto — they decide *where each piece of information goes*:

- **Metadata/tags vs. score.** Metadata and tags are *descriptive labels* for **filtering**
  (`stream:on`, a session id, a user). A **score** is a *measurement* Langfuse can **aggregate
  and chart over time** (average TTFT across 100 traces). Latency is stored as scores, not
  metadata, precisely so it's chartable.
- **Generation vs. plain span.** A "generation" is just a span Langfuse treats specially because
  it's an LLM call: it has slots for model name, token usage, and cost. Get an LLM call recorded
  as a generation and the cost panels populate later for free (Phase 2).
- **Trace-level vs. observation-level.** `session_id`, `user_id`, `tags`, `trace_name`, `release`
  describe the **whole request** (the trace). Input/output/duration describe **one step** (an
  observation). The SDK has different setters for each — mixing them up is a common bug.

> A clean trace for this app looks like (two top-level runs under one named trace — see §7 for
> why two, not one):
> ```
> Trace: "stream-request"  (3.8s)   session_id=abc  tags=[live, stream:on]  release=3267c5c
> ├─ Run: "condense"   (0.6s)
> │  ├─ Generation: condenser LLM   ← scores: condense_ttft, condense_tps, …
> │  └─ Span: retrieval
> └─ Run: "answer"     (3.0s)
>    └─ Generation: answer LLM      ← scores: answer_ttft, answer_tpot, answer_tps, …
> ```

---

<a name="3"></a>
## 3. How a trace is actually built — the two layers

You never hand-build the tree. Two layers cooperate: **LangChain's callback system** produces a
stream of lifecycle events, and the **Langfuse SDK** turns those events into a trace. Understand
both and everything else follows.

### 3a. Layer one — LangChain's callback system

As a chain runs, LangChain emits paired **start/end events** for every runnable in the tree:
`on_chain_start/end`, `on_llm_start`/`on_chat_model_start`/`on_llm_end`, `on_retriever_start/end`,
`on_tool_start/end`, plus `on_llm_new_token` during streaming. Each event carries a `run_id` and a
`parent_run_id`, which is **how the tree is reconstructed** — a child's `parent_run_id` is its
parent's `run_id`.

A **callback handler** is any object that implements these hooks. You register handlers two ways:
- **Per call**, via config: `chain.invoke(x, config={"callbacks": [h1, h2]})`.
- They then **propagate** to nested runnables automatically. Modern `langchain-core` propagates
  the active callbacks/config through **contextvars** for sync code, so a nested `.invoke()`
  *inside* a `RunnableLambda` inherits the parent's handlers even if you don't manually thread
  `config`. (This is why our condenser LLM — called inside `standalone_question`'s nested
  `condense_chain.invoke()` without explicit config — still gets traced and scored.)

`BaseCallbackHandler` is the base class. Critical mechanics (full version in
[observability-concepts.md](observability-concepts.md)):
- It defines **no `__init__`** and every hook is an **empty no-op** — they're pure extension
  points. Overriding one replaces "do nothing" with "do my thing"; you clobber nothing.
- Your hook signatures must **accept** the framework's arguments (`serialized`, `prompts`/`messages`,
  `token`, `run_id`, `parent_run_id`, …) even if you ignore them — the callback manager *passes*
  them positionally/by keyword. Always end with `**kwargs` so future args don't break you.
- **Chat models dispatch `on_chat_model_start`, not `on_llm_start`.** `ChatOllama` is a chat
  model. The base `on_chat_model_start` raises `NotImplementedError`, which the manager catches
  and falls back to `on_llm_start`. Cleaner to implement `on_chat_model_start` directly (its 2nd
  positional arg is `messages`). `on_llm_new_token` and `on_llm_end` are shared by both.
- **Errors are swallowed.** With `raise_error=False` (the default), an exception inside your
  callback is logged and dropped — the request keeps working, but your scores silently never
  appear. A broken callback fails *quietly*, which is why "verify in the UI" is a real step.

### 3b. Layer two — the Langfuse v4 SDK (OpenTelemetry under the hood)

The v4 Python SDK is a thin layer over **OpenTelemetry (OTel)**. Three pieces:

1. **The global client.** You configure one `Langfuse(...)` at process start (host, keys,
   `release`). Everything else reaches it via `get_client()`. It owns the HTTP batching/flushing.
   Trace data is sent **asynchronously** — the request doesn't block on network I/O to Langfuse.

2. **The `CallbackHandler`** (`from langfuse.langchain import CallbackHandler`) — the bridge. It's
   a LangChain callback handler that, on each LangChain event, creates the corresponding Langfuse
   observation (span/generation) with the right parent/child links and timings. It is **stateless
   with respect to trace identity** — it doesn't hold the trace; it attaches its observations to
   the trace named by its `trace_context` (or, absent that, the ambient OTel context).

3. **OTel context & contextvars.** OTel tracks the "current span" in a **contextvar**.
   `start_as_current_observation(...)` *attaches* a span as current (returns a token) and
   *detaches* it on exit. This ambient mechanism is how spans nest without you passing ids around
   — **but it only works while execution stays in the same context.** (This is the streaming
   landmine; see §8.)

**`trace_context` and `AS_ROOT` — the single most important SDK behavior to internalize:**
- What makes two observations share a trace is the **`trace_id`**, not the handler *object*. Same
  id → same trace. The handler is a courier of the id.
- Passing `trace_context={"trace_id": ...}` to the handler tells it "your root run **is** the
  root of this trace" — the SDK flags that span `AS_ROOT=True` (`langfuse.internal.as_root`) and
  it **owns the trace's identity, including its name**. The trace name then comes from the
  `langfuse_trace_name` config-metadata key (fallback: the root run's name, e.g.
  `"RunnableSequence"`).
- Therefore: **don't wrap an `AS_ROOT` handler in your own parent span** — the wrapper loses the
  trace name to the handler and just adds a confusing node. Let the handler own the trace, and
  configure it via metadata.

---

<a name="4"></a>
## 4. The end-to-end data flow

One streaming request, start to finish:

```
Browser (Streamlit)
  │  POST /query/stream  { question, mode, chat_history, session_id }
  ▼
app/api.py :: query_stream
  │  builds token_generator() wrapping stream_answer_question(...)
  │  returns StreamingResponse(media_type="application/x-ndjson")
  ▼
app/chains.py :: stream_answer_question  (a GENERATOR)
  │  trace_id = client.create_trace_id()                 # 1. mint a trace id, no span
  │  handler  = get_langfuse_handler(trace_id)           # 2. per-request bridge, carries the id
  │  lf_meta  = { langfuse_trace_name, _session_id, _user_id, _tags }   # 3. trace attrs
  │
  │  (standalone_chain | retrieval_chain).invoke(..., config={callbacks:[handler, latency], metadata, run_name:"condense"})
  │        └─► LangChain fires on_chat_model_start/on_llm_end for the condenser
  │             ├─► CallbackHandler  → creates spans/generation under trace_id   (the "what")
  │             └─► OllamaLatencyCallback → create_score(trace_id=…, condense_*)  (the "how fast")
  │  yield {sources}                                     # UX: sources first
  │
  │  stream_generation_chain.stream(..., config={callbacks:[handler, latency], metadata, run_name:"answer"})
  │        └─► on_chat_model_start → on_llm_new_token (×N, true TTFT) → on_llm_end
  │             ├─► CallbackHandler  → the "answer" generation under the SAME trace_id
  │             └─► OllamaLatencyCallback → create_score(trace_id=…, answer_*)
  │  yield {token} … yield {token}
  ▼
api.py serializes each frame as one ndjson line ({type:sources|token|done}) → client
  │
  ▼  (asynchronously, in the background)
Langfuse SDK batches & flushes spans+scores → Langfuse platform → ClickHouse → UI
```

Non-streaming `/query` → `answer_question` is the same minus the generator/`yield`: it returns a
dict, and both `.invoke()`s carry the shared handler + metadata.

The key property: **the request path and the observability path are decoupled.** The callbacks
ride along on the same LangChain events; if Langfuse is down or slow, the user still gets their
answer (the SDK flushes asynchronously and errors are swallowed).

---

<a name="5"></a>
## 5. Instrument #1 — the native Langfuse `CallbackHandler`

This captures the **whole run tree** as a trace automatically — every chain, retriever, and LLM
call, with inputs/outputs and end-to-end latency. Zero manual span code.

**Configure the global client once** (`app/observability.py`, at import time):
```python
from langfuse import Langfuse, get_client
from langfuse.langchain import CallbackHandler

Langfuse(                                   # the process-wide client
    host       = settings.langfuse_base_url,
    public_key = settings.langfuse_public_key,
    secret_key = settings.langfuse_secret_key,
    release    = rag_version(),             # git short SHA → every trace tagged with the version
)
```
> Note: pydantic-settings loads `.env` into the *settings object*, **not** `os.environ`. The
> `CallbackHandler` reads creds from the global client, so configure the client explicitly from
> settings (as above) rather than assuming env vars are present.

**Make a fresh handler per request, carrying the trace id:**
```python
def get_langfuse_handler(trace_id: str) -> CallbackHandler:
    return CallbackHandler(trace_context = {'trace_id': trace_id})
```
**Attach it (and pass trace attributes via metadata):**
```python
chain.invoke(x, config = {
    'callbacks': [handler, ...],
    'metadata' : {'langfuse_trace_name': 'stream-request',
                  'langfuse_session_id': session_id,
                  'langfuse_user_id'   : 'yashaur',
                  'langfuse_tags'      : ['live', 'stream:on']},
    'run_name' : 'answer',
})
```
The exact metadata keys are `langfuse_session_id`, `langfuse_user_id`, `langfuse_tags`,
`langfuse_trace_name` — get one wrong and it's silently ignored.

---

<a name="6"></a>
## 6. Instrument #2 — the custom `OllamaLatencyCallback`

The native handler records *that* an LLM call happened and its wall-clock duration. It does **not**
compute the LLM-specific perf metrics we want — **TTFT** (time to first token), **TPOT** (time per
output token), **TPS** (tokens/sec), model-load time, token counts. Those live in Ollama's own
response metadata. This second callback rides the *same* event stream and writes them as **numeric
scores**, so they're queryable and chartable.

```python
from langchain_core.callbacks import BaseCallbackHandler
from langfuse import get_client
from time import perf_counter

class OllamaLatencyCallback(BaseCallbackHandler):

    def __init__(self, label: str = 'unknown', trace_id: str = None):
        self.label    = label        # 'condense' | 'answer' — prefixes the score names
        self.trace_id = trace_id     # explicit target trace (see §8 gotcha #2)

    def on_chat_model_start(self, serialized, prompts, **kwargs):
        self._t_start      = perf_counter()   # ChatOllama is a chat model → this hook, not on_llm_start
        self._first_token_t = None

    def on_llm_new_token(self, token, **kwargs):
        if self._first_token_t is None:       # streaming only: first token = TRUE TTFT
            self._first_token_t = perf_counter()

    def on_llm_end(self, response, **kwargs):
        md = response.generations[0][0].message.response_metadata   # WHERE Ollama's numbers live

        load_duration        = md.get('load_duration', 0) / 1e9     # ns → s  (everything in ns!)
        prompt_eval_count    = md.get('prompt_eval_count', 0)
        prompt_eval_duration = md.get('prompt_eval_duration', 0) / 1e9
        eval_count           = md.get('eval_count', 0)
        eval_duration        = md.get('eval_duration', 0) / 1e9
        total_duration       = md.get('total_duration', 0) / 1e9

        # true TTFT if we saw tokens stream; else a proxy from Ollama's own timings
        ttft = (self._first_token_t - self._t_start) if self._first_token_t \
               else (load_duration + prompt_eval_duration)
        tpot = eval_duration / eval_count if eval_count > 0.0 else 0.0   # seconds per output token
        tps  = eval_count / eval_duration if eval_duration > 0.0 else 0.0
        input_tokens, output_tokens = prompt_eval_count, eval_count

        client = get_client()
        if self.trace_id:                                            # explicit target — reliable everywhere
            for name, value in [
                ('ttft', ttft), ('tpot', tpot), ('tps', tps),
                ('load_duration', load_duration), ('total_duration', total_duration),
                ('input_tokens', input_tokens), ('output_tokens', output_tokens),
            ]:
                client.create_score(trace_id=self.trace_id, name=f'{self.label}_{name}',
                                    value=value, data_type='NUMERIC')
        else:                                                        # ambient fallback (non-generator paths)
            ...  # client.score_current_trace(name=f'{self.label}_{name}', value=value, data_type='NUMERIC')
```
(The shipped file spells out the seven `create_score` lines; the loop above is the same thing.)

Why each thing is the way it is:
- **`label` prefixes, doesn't filter.** The condenser is *also* `ChatOllama`, so this callback
  fires for both LLM calls. Rather than suppress the condenser, we **label** each call
  (`condense_*` / `answer_*`) so both sets of metrics are kept and distinguishable. Each call's
  `on_chat_model_start` resets `_t_start`, so every `on_llm_end` measures its own call.
- **True vs proxy TTFT.** Streaming fires `on_llm_new_token`, so `ttft` is the real first-token
  latency. Non-streaming never fires it (`_first_token_t` stays `None`), so we fall back to
  Ollama's `load_duration + prompt_eval_duration` proxy. (This is why the eval harness is pinned
  to non-streaming for *comparable* timing, per CLAUDE.md.)
- **Nanoseconds.** Every Ollama `*_duration` is in **ns** — divide by `1e9`. `perf_counter()` is
  already in seconds. Miss a conversion and TPS is off by a billion.
- **`create_score(trace_id=…)`, not `score_current_trace()`.** See §8 gotcha #2.

Full derivation in [ollama-latency-callback.md](ollama-latency-callback.md).

---

<a name="7"></a>
## 7. The core patterns (the template)

These are the reusable moves — the part to copy into the next project.

### Pattern A — one trace per request (handler-owns-trace)
The spine of the whole design. For any request that makes **more than one** top-level LLM/chain
call and must appear as a single trace:
1. `trace_id = client.create_trace_id()` — mint a stable id **without** creating a span.
2. Build **one per-request** `CallbackHandler(trace_context={'trace_id': trace_id})` and pass that
   **same handler** to every `.invoke()/.stream()` in the request.
3. Pass trace attributes through `config['metadata']` (`langfuse_trace_name`, `langfuse_session_id`,
   `langfuse_user_id`, `langfuse_tags`) and label each run with `config['run_name']`.
4. Write custom scores with `create_score(trace_id=trace_id, ...)`.

Why this shape: the `trace_id` (not the object) unifies the trace; a per-request handler avoids
the frozen-singleton bug; `metadata` is the handler's documented channel for naming/attributes;
explicit `trace_id` on scores sidesteps the ambient-context fragility. **Don't** add a wrapper
span — the handler is `AS_ROOT` and owns the trace. Cost: N top-level calls show as N runs under
the one trace (acceptable and honest). Full reasoning in [unify-traces.md](unify-traces.md).

### Pattern B — name the trace deterministically
Set `langfuse_trace_name` in `metadata`. Don't rely on auto-naming (you'll get
`"RunnableSequence"`). Don't try to set the name on a wrapper span (the `AS_ROOT` handler wins).

### Pattern C — trace attributes vs. measurements
- **Filterable facts** about the request → `metadata`/`tags` (session, user, mode, version).
- **Numbers you'll want to average/chart** → **scores** (`create_score`, `data_type='NUMERIC'`).

### Pattern D — session grouping
Mint a `session_id` once per conversation **on the client** (one UUID per chat window) and send it
with every turn; thread it into `langfuse_session_id`. Same id across turns → Langfuse groups them
in the Sessions view. Single-turn / no session → omit the key (the handler ignores non-`str`).
(See [streamlit-session-id.md](streamlit-session-id.md).)

### Pattern E — versioning
Set `release` once on the global `Langfuse(...)` (we use the git short SHA). Every trace is then
tagged with the code version — essential for the eval-driven loop (compare before/after a change).

### Pattern F — streaming vs non-streaming
- **Non-streaming** has no `yield`, so ambient OTel context *would* work — but we use the same
  explicit-`trace_id` + metadata pattern for **uniformity**.
- **Streaming is a generator** and must use explicit `trace_id` (ambient context dies across the
  `yield`, §8 gotcha #3) and must **avoid OTel context managers** in the generator body. Consume
  the stream by `yield`ing tokens directly; don't switch `.stream()` → `.invoke()` to "simplify"
  (that silently disables streaming and loses true TTFT).

---

<a name="8"></a>
## 8. The gotcha catalogue (decision log)

Each is a real bug we hit; the rule is the takeaway.

**#1 — A cached/singleton handler with a baked-in `trace_context` mis-routes every later request.**
A module-level `_handler` freezes the *first* request's `trace_id`; request #2+ dump their spans
into that dead trace. → **Build the handler per request.** (The handler is stateless re: identity,
so what you're "caching" is worthless and the baked id is actively harmful.)

**#2 — `score_current_trace(trace_id=…)` is a `TypeError`; and ambient scoring is unreliable in
generators.** `score_current_trace()` has **no** `trace_id` parameter (it targets the *current*
context) — passing one raises, and the callback machinery *swallows* it, so scores vanish
silently. And the "current trace" isn't reliably set inside a streaming generator anyway. → **Use
`create_score(trace_id=<explicit id>, name=…, value=…, data_type='NUMERIC')`.**

**#3 — OTel context managers can't straddle a generator `yield`.** `start_as_current_observation`
and `propagate_attributes` `attach`/`detach` a contextvar token that must be released in the same
`Context`. A FastAPI `StreamingResponse` resumes the generator in a *different* context, so detach
fails — `start_as_current_observation` logs **`Failed to detach context`** (raw OTel detach);
`propagate_attributes` swallows it via `_detach_context_token_safely`. → **In streaming, use no
OTel context managers**; pass an explicit `trace_id` and trace attributes via metadata instead.

**#4 — Passing `trace_context` makes the handler `AS_ROOT`, so it owns the trace name.** A manual
wrapper span loses the name to the handler and shows up as a stray node (trace displayed as
`"RunnableSequence"`). → **Don't wrap the handler; name the trace via `langfuse_trace_name`.**

**#5 — Callback exceptions are swallowed (`raise_error=False`).** A wrong metadata key, an
unguarded `None`, a bad attribute path — all fail *silently* and just drop your scores. → **Always
verify scores/spans actually appear in the UI; never assume.**

**#6 — Ollama durations are nanoseconds.** Divide every `*_duration` by `1e9`. → A TPS that's
~1e9 off means a missed conversion.

**#7 — Chat models use `on_chat_model_start`.** `ChatOllama` won't hit your `on_llm_start`
directly (it goes through a `NotImplementedError` fallback). → **Implement `on_chat_model_start`.**

**#8 — Don't attach the trace handler to non-LLM steps you don't want as roots.** With a
`trace_context` handler, a stray top-level `.invoke()` (e.g. prompt templating) becomes another
`AS_ROOT` run. → Only attach the handler where you want observations.

---

<a name="9"></a>
## 9. A checklist for instrumenting a new project

Framework-agnostic-ish; the specifics assume LangChain + Langfuse but the shape generalizes.

1. **Stand up the backend** and configure **one global client** at startup with creds + a
   `release`/version label.
2. **Decide the trace boundary.** Usually "one inbound request = one trace." Name it deterministically.
3. **Auto-capture the tree** with the framework's native callback/instrumentation; attach it at
   the request entry point so it propagates to nested calls.
4. **For multi-call requests, apply Pattern A** (mint id → one per-request handler → same handler
   everywhere → attributes via metadata → explicit-id scores). Don't wrap; don't cache the handler.
5. **Add trace attributes** (session, user, tags, version) as *metadata*, and **derived metrics**
   (latency, tokens) as *scores*. Keep the two straight (filter vs. aggregate).
6. **Handle streaming explicitly**: keep real streaming, use explicit ids, avoid context managers
   across the `yield`, capture true first-token timing.
7. **Decouple the paths**: observability must be async + fail-soft, so the user is never blocked or
   broken by the telemetry backend.
8. **Verify in the UI, every time** — callbacks fail silently. Check: one trace, correct name,
   expected sub-runs, scores attached with sane magnitudes, session grouping, version tag.

---

<a name="10"></a>
## 10. Verification & references

**Verification (this app):**
- Multi-turn `/query/stream`: one trace `stream-request`, runs `condense` + `answer`, `condense_*`
  & `answer_*` scores, `session_id`, `[live, stream:on]`, `release`; **no `Failed to detach
  context`** in logs.
- Multi-turn `/query`: same, `non-stream-request`, `[live, stream:off]`.
- Two turns, same `session_id` → one session in the Sessions view.
- Sanity-check magnitudes: TPS in the right ballpark for the model (catches a missed ns
  conversion); true TTFT (streaming) plausibly smaller than the proxy.
- Single-turn produces no `condense_*` scores (the condenser doesn't run) — expected.

**References (verified June 2026, `langfuse 4.11.0`):**
- LangChain integration & metadata keys: https://langfuse.com/docs/integrations/langchain/tracing
- Custom scores: https://langfuse.com/docs/scores/custom
- Python SDK (OTel-based, v3+): https://langfuse.com/docs/sdk/python/sdk-v3
- Env vars (`LANGFUSE_BASE_URL` preferred; `LANGFUSE_HOST` legacy alias): https://langfuse.com/self-hosting/configuration
- Companion guides in this folder: [observability-concepts.md](observability-concepts.md),
  [ollama-latency-callback.md](ollama-latency-callback.md), [unify-traces.md](unify-traces.md),
  [observability-hook.md](observability-hook.md).
