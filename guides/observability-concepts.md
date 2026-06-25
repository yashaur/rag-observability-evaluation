# Primer: observability concepts + how LangChain callbacks work

> Reference notes for the mental model behind this project. Part 1 is the observability
> vocabulary (trace/span/score/…); Part 2 is the mechanics of `BaseCallbackHandler` (why our
> `OllamaLatencyCallback` looks the way it does). Grounded in the installed
> `langchain-core 1.4.7` / `langfuse 4.11.0`.

---

## Part 1 — observability fundamentals

### The problem
When the RAG app handles a request, lots happens you can't see from outside: condense →
retrieve → rerank → build prompt → generate. "It was slow" or "the answer was wrong" doesn't
say *which step*. Observability = instrumenting the system so each request leaves a structured
record you can reconstruct afterward. The classic "three pillars" are **logs** (text lines),
**metrics** (aggregate numbers), and **traces** (the detailed story of one request). We build
primarily a **tracing** system.

### Trace and span
- A **trace** is the complete record of **one request**, end to end. One `/query` → one trace.
  It has a total duration, an overall input (question) and output (answer), and a status.
- A **span** is **one step inside** that trace — retrieval, the condense call, the generation.
  Each span has its own start/end (duration), input/output, and status.

Spans **nest into a tree**. The trace is the root; spans branch under it. For this app a
single `/query` ideally looks like:

```
Trace: "rag-request"  (3.8s)
├─ Span: condense question   (0.4s)   ← condenser LLM
├─ Span: retrieval + rerank  (0.9s)   ← BM25 + vector + reranker
└─ Generation: Ollama answer (2.5s)   ← the LLM call
```

That tree is why "merge into one trace" matters: two separate top-level `.invoke()` calls
make **two roots** (two traces); a parent span gives everything **one** root.

### Langfuse's vocabulary

| Term | What it is | In this app |
|---|---|---|
| **Trace** | one request | one `/query` |
| **Observation** | the generic word for any node in the tree | any step |
| → **Span** | a generic step observation | retrieval |
| → **Generation** | a special observation for an LLM call (also records model, tokens, cost) | the Ollama call |
| **Score** | a named measurement attached to a trace/observation (numeric/categorical/boolean) | `answer_ttft`, `answer_tps`, … |
| **Session** | a grouping of related traces | a multi-turn chat (`session_id`) |
| **Release** | a version label on traces | the git short SHA |
| **Dataset / Dataset Run** | a fixed input set + one eval pass over it | Phase 4 (RAGAS) |

Two distinctions worth holding onto:
- **Metadata/tags vs. score.** Metadata are descriptive labels (`stream:on`, session id) — for
  *filtering*. Scores are *measurements* Langfuse can aggregate and chart over time (avg TTFT
  across 100 traces). That's why latency is stored as scores, not metadata.
- **Generation vs. plain span.** A "generation" is just a span Langfuse treats specially
  because it's an LLM call — it has slots for tokens, model, and cost. This is how cost panels
  populate later, for free.

### How a trace is born
You don't hand-build the tree. The Langfuse `CallbackHandler` hooks into LangChain's callback
system: as the chain runs, LangChain fires events ("chain started", "LLM finished"), and the
handler turns them into spans/generations with the right parent/child links and timings. Our
custom `OllamaLatencyCallback` rides the *same* event stream, but instead of building spans it
reads Ollama's numbers and writes **scores**.

---

## Part 2 — how `BaseCallbackHandler` actually works

Our `OllamaLatencyCallback(BaseCallbackHandler)` overrides a few methods. Here's the mechanism
behind every "why is this like this?".

### Why no `super().__init__()`
`BaseCallbackHandler` defines **no `__init__` of its own** (`BaseCallbackHandler.__init__ is
object.__init__` → `True`). There's nothing to initialize, so omitting `super().__init__()` is
fine. If you add your own `__init__` (e.g. a `label`), calling `super().__init__()` is harmless
good practice but does nothing here.

### The parent hooks are no-ops — you're not clobbering anything
The inherited `on_llm_start`, `on_llm_new_token`, `on_llm_end`, etc. are **empty stubs** — their
entire body is a docstring; they return `None` and do nothing. They exist purely as **extension
points**. Overriding them replaces "do nothing" with "do my thing" — you lose no behavior,
because there is none.

### The unused params are the *framework's* contract
`serialized`, `prompts`, `token` aren't there because the parent uses them (it doesn't — its
methods are empty). They're there because **the caller passes them.** LangChain's *callback
manager* invokes your method like:
```python
handler.on_llm_start(serialized, prompts, run_id=..., parent_run_id=..., tags=..., metadata=..., **kwargs)
```
Your signature must **accept** those args or Python raises `TypeError` at call time. You don't
*reference* them because you don't need them — but you must *receive* them. The `**kwargs`
absorbs everything you didn't name (`run_id`, `parent_run_id`, `tags`, `metadata`, …) so the
call never fails. (If you ever wanted the model name at start time, it's sitting in
`serialized`.)

### What the class can do beyond what you coded
`BaseCallbackHandler`'s MRO is `LLMManagerMixin → ChainManagerMixin → ToolManagerMixin →
RetrieverManagerMixin → CallbackManagerMixin → RunManagerMixin → object`. So your object
already has **every** hook (`on_chain_start`, `on_tool_start`, `on_retriever_start`,
`on_llm_error`, …) as inherited no-ops. LangChain can fire any event at it; the ones you didn't
override silently do nothing. You implement only the hooks you care about.

### The chat-model catch (important)
`ChatOllama` is a **chat** model, and LangChain dispatches a *different* start event for chat
models: **`on_chat_model_start`**, not `on_llm_start`. The bridge: the inherited default
`on_chat_model_start` **raises `NotImplementedError`**, and the callback manager catches it and
**falls back to `on_llm_start`** (converting messages → prompt strings). So a handler that only
implements `on_llm_start` still works for chat models, via that fallback.

Cleaner is to **define `on_chat_model_start` directly** (its 2nd positional arg is `messages`,
not `prompts`) and set your start timestamp there — then you don't depend on the fallback.
`on_llm_new_token` and `on_llm_end` are shared by chat and non-chat models, so those fire
directly with no fallback.

### Callback errors are swallowed by default
By default `raise_error` is `False`, so **exceptions inside your callback are logged and
swallowed** — the request keeps working, but your scores silently never get written. That's why
"verify the scores actually appear in the UI" is a real step: a broken callback fails *quietly*
(wrong metadata key, an unguarded `None`, etc.). (`run_inline` controls sync execution;
`ignore_llm`/`ignore_chain`/… let a handler opt out of whole event categories.)
