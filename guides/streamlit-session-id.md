# Build-guide: session IDs in the Streamlit chat page

> **Mode: guide-then-review.** Logic and patterns only — no finished implementation. You
> write it; I review. Anchored to your real frontend: `frontend/pages/1_chat.py`,
> `frontend/api_client.py`. Lands in the `personal-rag-system` repo.
>
> **The job:** give every conversation a stable `session_id` so Langfuse groups its turns
> into one session — but start a *new* session when the user switches the single/multi mode
> (or clears the chat). You already added `session_id` passthrough in `api_client.py`; this
> is the last piece: deciding the id's *lifetime* on the page.

---

## 1. Why a naive `uuid4()` fails — the Streamlit execution model

Streamlit isn't event-driven like a normal web app. **The entire page script re-runs, top to
bottom, on every interaction** — every keystroke-submit, every toggle flip, every button
press — and again when you navigate to another page and come back. There are no "handlers"
that run in isolation; the whole file is the handler.

So if you write `session_id = uuid4()` anywhere at the top of `1_chat.py`, that line executes
*on every rerun*. Ask a question → rerun → new id. Flip a toggle → rerun → new id. Each turn
of the same conversation gets a **different** id, and Langfuse sees a pile of one-message
sessions instead of one coherent chat. That's the exact trap you spotted.

The fix has two halves: **(a) persist** the id across reruns, and **(b) deliberately
*invalidate* it** at the moments that mean "this is a new conversation."

---

## 2. The persistence tool: `st.session_state`

`st.session_state` is a dict that **survives reruns** for as long as the browser tab's
session lives (it also survives page switches — that's why your `messages` list isn't wiped
when you visit Documents and return). Anything you stash there persists; plain local
variables do not.

You already rely on this for the chat history:
```python
if "messages" not in st.session_state:
    st.session_state.messages = []
```
That's the **initialize-once idiom**: the body runs only on the *first* rerun (when the key is
absent); every later rerun skips it, so the value persists. The session id wants the same
treatment — initialize it once, then reuse it.

> Mental model: `st.session_state["session_id"]` is the *memory* that outlives a rerun; a new
> `uuid4()` is the *act of starting a new conversation*. You want the memory to persist and
> the act to happen only on purpose.

---

## 3. Half (a): a session id that persists

Apply the initialize-once idiom to the session id: on first load, if there's no
`session_id` in `st.session_state`, mint one (`str(uuid4())` — store the **string**, since the
API/Langfuse want a string, not a `UUID` object) and stash it. On every later rerun the key
already exists, so you reuse the same id. Read it back from `st.session_state` wherever you
make the API call.

That alone gives you a stable id for the tab's lifetime. Now the harder half: making it
change **only** when it should.

---

## 4. Half (b): start a NEW session when the mode toggles — the core idea

The conceptual rule: **single ↔ multi is a different kind of conversation, so it deserves a
fresh session.** (A multi-turn chat carries history; a single-shot Q&A doesn't. Mixing both
under one Langfuse session would be misleading.) Same for the "Clear conversation" button —
that's literally "start over." So those two actions should *invalidate* the id.

The difficulty is that on any given rerun you only see the toggle's **current** value
(`multi_turn` → `mode`). You don't automatically know whether it just *changed* or has been
sitting there for ten reruns. To regenerate "on change," you must **detect the change**, and
there are two clean ways.

### Approach A — remember the previous value (change-detection)

The idea: keep a second piece of memory, `prev_mode`, in `st.session_state`. On each rerun,
**compare** the widget's current `mode` to the stored `prev_mode`:

- if they differ → the user just flipped it → mint a new `session_id` and update `prev_mode`
  to the current value;
- if they're equal → do nothing (persist the existing id).

Logic sketch (not the full code):
```
read mode from the toggle
if "prev_mode" not in session_state:        # first ever run
    session_state.prev_mode = mode
if mode != session_state.prev_mode:         # it changed since last rerun
    session_state.session_id = new id
    session_state.prev_mode = mode
```
Why it works: `prev_mode` only updates *after* you notice the difference, so exactly one rerun
sees `mode != prev_mode` — the one right after the flip. Every other rerun sees them equal and
leaves the id alone. This is the canonical "diff against last-seen value" pattern; understand
it and you understand most Streamlit state puzzles.

### Approach B — an `on_change` callback (more idiomatic)

Streamlit widgets accept a `key=` (which binds the widget's value into `st.session_state`
under that key) and an `on_change=` callback that **fires once, before the rerun body, only
when the value actually changes.** So you can give the Multi-turn toggle a `key` and an
`on_change` function whose whole job is "mint a new session id into session_state." No manual
`prev_mode` bookkeeping — Streamlit does the change-detection for you.

Logic: define a tiny callback `def _new_session(): st.session_state.session_id = <new id>`,
and pass `on_change=_new_session` (plus a `key=`) to the toggle. The same callback can be
reused by the "Clear conversation" button.

> **Recommendation:** Approach B is cleaner and less error-prone — prefer it. But read
> Approach A too: it makes the rerun/diff model explicit, which is the thing you said you
> wanted to *understand*, and you'll reuse that pattern constantly.

### What should and shouldn't reset the id

- **Reset (new session):** the Multi-turn toggle changing; the "Clear conversation" button.
- **Do NOT reset:** the "Stream LLM response" toggle (streaming on/off is the *same*
  conversation, just a different transport — it must keep the same id so both transports land
  in one session). Page navigation (Documents → back) also must not reset it.

This is why you attach the regeneration to *specific* controls, not to "any rerun."

---

## 5. Wiring it into the API call (and a bug to fix while you're here)

Once `st.session_state.session_id` exists, pass it into both API calls. **But look at your
current calls** in `1_chat.py`:
```python
api_client.query_stream(prompt, history, mode)   # line ~75
api_client.query(prompt, history, mode)          # line ~85
```
Your `api_client` signatures are now
`query(question, chat_history=None, session_id=None, mode='single')` — note `session_id`
sits **before** `mode`. So those **positional** calls now bind `mode`'s value ('multi'/
'single') into the `session_id` slot, and `mode` silently falls back to its default. Two bugs
at once: the session id is wrong *and* multi-turn isn't actually being sent.

Fix by switching to **keyword arguments** (and adding the id):
```
api_client.query(prompt, chat_history=history, session_id=st.session_state.session_id, mode=mode)
api_client.query_stream(prompt, chat_history=history, session_id=st.session_state.session_id, mode=mode)
```
Keyword args make you immune to future parameter-order changes — a good habit for exactly
this reason.

---

## 6. Verify the behaviour (before checking Langfuse)

Reason through (or print `st.session_state.session_id` temporarily):

1. Ask two questions in **multi** mode → **same** id both turns.
2. Flip **Multi-turn** → id **changes** once.
3. Flip **Stream** on/off → id **unchanged**.
4. Go to **Documents** and back → id **unchanged**.
5. Click **Clear conversation** → id **changes**.

Then in Langfuse (`localhost:3000`) → **Sessions**: a multi-turn chat shows as one session
with several traces; flipping mode starts a new session. That's the Part-B session grouping
working end to end.

---

## 7. One design note (optional, your call)

Since a mode switch is semantically "a new conversation," you *may* also want to clear
`st.session_state.messages` at the same moment (single mode doesn't use history anyway). Not
required for tracing — but it keeps the UI honest with the new session. Decide based on the UX
you want; the session-id logic above stands either way.
