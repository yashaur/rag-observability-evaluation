# Build-guide: the OllamaLatencyCallback (Part C)

> **Mode: guide-then-review.** Concepts, shapes, and gotchas — no finished implementation.
> You write it; I review. Lands in `personal-rag-system` as additions to `app/observability.py`
> + the two call-sites in `app/chains.py`.
>
> **Grounded against your actual install** (not generic): `langfuse 4.11.0` (SDK **v4**),
> `langchain-ollama 1.1.0`, `langchain-core 1.4.7`. The field locations and method signatures
> below were read from those packages — but still `print()` once to confirm before trusting.

---

## 1. What this adds and why

The native handler (Part A) already gives you the trace, the spans, and end-to-end latency.
What it does **not** give you is the engine-level performance picture: how long the model took
to *load*, how fast it produced tokens, time-to-first-token. Ollama reports all of that in its
response metadata; this callback reads it and writes it onto the trace as **numeric scores**
(scores are the thing Langfuse can aggregate and chart over time).

**True vs proxy TTFT** — the one conceptual subtlety:
- **Streaming on:** you can stamp the wall-clock moment the *first token* arrives → **true
  TTFT**.
- **Streaming off:** there is no "first token" event, so you approximate it from Ollama's own
  timings: `load_duration + prompt_eval_duration` → **proxy TTFT**. Same metric, lower
  fidelity; the `stream:on/off` tag you already set lets you tell them apart.

---

## 2. The callback skeleton

It's a `langchain_core.callbacks.BaseCallbackHandler`. Three hooks matter:

```python
# SHAPE — app/observability.py
from langchain_core.callbacks import BaseCallbackHandler
from time import perf_counter
from langfuse import get_client

class OllamaLatencyCallback(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        self._t_start = perf_counter()
        self._first_token_t = None          # reset per call

    def on_llm_new_token(self, token, **kwargs):
        if self._first_token_t is None:      # only the FIRST token (streaming path)
            self._first_token_t = perf_counter()

    def on_llm_end(self, response, **kwargs):
        md = ...        # see §3 for the exact path
        # ... compute (§4) and score (§5)
```

> **Per-call state ⇒ per-call instance.** `_t_start`/`_first_token_t` are per-request. Create
> a **new `OllamaLatencyCallback()` for each call** (not a shared singleton). The Langfuse
> handler can stay the singleton; this one cannot.

---

## 3. Where Ollama's timing fields actually live (verified)

In `on_llm_end`, `response` is a `langchain_core.outputs.LLMResult`. The metadata sits on the
message of the first generation. Confirmed shape from `langchain-ollama 1.1.0`:

```python
# response.generations[0][0] is a ChatGeneration
gen = response.generations[0][0]
md  = gen.message.response_metadata          # primary location
# (gen.generation_info carries prompt_eval_count / eval_count too)
```

`md` contains (all durations in **nanoseconds**):
```
load_duration, prompt_eval_count, prompt_eval_duration,
eval_count, eval_duration, total_duration
```
and the message also carries `usage_metadata = {"input_tokens", "output_tokens", ...}`.

> **Do this once:** `print(response)` (or `print(md)`) on your first run and eyeball it. Two
> reasons: (1) confirm the path on your model, (2) streaming may only populate these on the
> **final** chunk — verify they're non-zero on the streaming path before computing TTFT/TPS.
> Guard every read with `md.get(key, 0)` so a missing field can't crash the request.

---

## 4. The maths — and the nanosecond trap

Everything Ollama reports as `*_duration` is **nanoseconds**. Convert with `/ 1e9` before you
report seconds, or your numbers will be ~1e9 off. This is *the* mistake to avoid here.

```
load_s      = load_duration / 1e9
ttft_s      = (first_token_t - t_start)              # streaming: true, already seconds
            = (load_duration + prompt_eval_duration) / 1e9   # non-streaming: proxy
tpot_s      = (eval_duration / eval_count) / 1e9     # seconds per output token
tps         = eval_count / (eval_duration / 1e9)     # output tokens per second
input_tokens  = prompt_eval_count
output_tokens = eval_count
```

Guard the divisions: if `eval_count == 0` or `eval_duration == 0`, skip `tpot`/`tps` rather
than divide by zero (rare, but a refusal/empty generation can do it).

> Note `perf_counter()` returns **seconds** already, so the true-TTFT branch is *not* divided
> by 1e9 — only the Ollama-derived (ns) values are. Mixing those two up is the subtle version
> of the trap.

---

## 5. Writing the scores (v4 SDK)

Verified v4 calls (keyword-only):

```python
client = get_client()
client.score_current_trace(name="ttft_s", value=ttft_s, data_type="NUMERIC")
client.score_current_trace(name="tpot_s", value=tpot_s, data_type="NUMERIC")
client.score_current_trace(name="tps",    value=tps,    data_type="NUMERIC")
# also: load_s, input_tokens, output_tokens
```

**Will `score_current_trace()` find the right trace from inside the callback?** It scores the
*current trace* (the root), and the native handler's root span stays open for the entire
`chain.invoke`, so when your `on_llm_end` fires mid-chain the trace is still active — it should
attach correctly. **Verify in the UI** (step in §10) that the scores land on the *same* trace
as the spans.

If they don't (e.g. an empty/stray trace), use the explicit fallback — capture the id and
score by id:
```python
tid = client.get_current_trace_id()              # -> str | None
if tid:
    client.create_score(trace_id=tid, name="ttft_s", value=ttft_s, data_type="NUMERIC")
```

> **Callback order matters.** Put the latency callback **after** the langfuse handler in the
> list: `[get_langfuse_handler(), OllamaLatencyCallback()]`. You want the langfuse trace
> context established when your scoring runs.

---

## 6. Scope gotcha — the condenser will fire this too

`condenser_llm` is also a `ChatOllama`, so in multi-turn the condense step also triggers
`on_llm_end`. If your callback is attached where it sees both LLM calls, the condenser's
`on_llm_end` runs and **overwrites your generation scores** (same score names on the same
trace). You want these metrics to describe the **main answer generation only**. Options:

- attach `OllamaLatencyCallback()` so it only rides the generation chain, not the condense
  chain (cleanest given your structure), or
- name the scores distinctly per call, or
- ignore calls whose token counts look like a condense (fragile — prefer the first option).

Decide and note your choice; I'll check it in review.

---

## 7. Wiring at both call-sites

- **Non-streaming** (`answer_question` → `/query`): add a fresh `OllamaLatencyCallback()` to
  the `callbacks` list in the `config` you already build. TTFT here is the **proxy**.
- **Streaming** (`stream_answer_question` → `/query/stream`): two things —
  1. you still need to pass `config=` to the actual `stream_generation_chain.stream(prompt, config=...)`
     (from the earlier review it's currently missing) so the generation is traced *and* the
     latency callback receives `on_llm_new_token`;
  2. with tokens flowing, you now get **true TTFT**.

Since `langfuse_config()` builds the dict, the simplest path is to have it append a new
`OllamaLatencyCallback()` each call (remember: per-call instance, §2).

---

## 8. Finish the `rag_version()` wiring (v4 makes this easy)

The v4 `Langfuse(...)` constructor accepts a `release=` kwarg, so skip the env-var dance —
pass it directly where you init the client in `observability.py`:
```python
Langfuse(host=settings.langfuse_base_url,
         public_key=settings.langfuse_public_key,
         secret_key=settings.langfuse_secret_key,
         release=rag_version())
```
Every trace then carries your git short SHA as **release** (filterable in the UI). Caveats
unchanged: it reflects the last commit (not uncommitted edits), and `git rev-parse` resolves
from the process cwd.

---

## 9. Flushing (note for later, not now)

In your long-running FastAPI app the SDK's background exporter flushes on its own — no action
needed. Keep it in mind for **Phase 4**: the eval harness is a short-lived script, so it must
call `get_client().flush()` before exiting or the last traces/scores never ship. Not a Part-C
concern; just don't forget it later.

---

## 10. Verification

1. Send one **non-streaming** `/query`. In Langfuse (`localhost:3000`), open the trace:
   - scores `ttft_s`, `tpot_s`, `tps`, `load_s`, token counts are attached **to the same
     trace** as the retrieval + LLM spans (§5 check).
   - magnitudes are sane for your model (e.g. TPS in the tens, not ~1e9 or ~1e-9 → the ns
     check from §4).
2. Send one **streaming** `/query/stream`: confirm the generation is now traced *and* a
   **true TTFT** appears (plausibly smaller than the proxy), tagged `stream:on`.
3. **Multi-turn** (§6 check): confirm the condense call didn't clobber the generation scores.
4. Confirm the trace's **Release** shows your short SHA (§8).

When these pass, Phase 1 is complete (ARCHITECTURE §11 "done when": a real chat shows a trace
with retrieval + Ollama spans **and** the TTFT/TPOT/TPS scores).
