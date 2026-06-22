"""dashboard/app.py — OPTIONAL Streamlit view (Phase 6).

STUB. This is deferred and "I write directly on request" (CLAUDE.md §4) — built only if
Langfuse's built-in UI leaves a real gap (ARCHITECTURE §8).

Langfuse's UI already gives you, with zero custom code: the live trace explorer, dashboards
over time (volume / latency / tokens / illustrative cost), the sessions view, and the
Dataset run-comparison for eval regressions. So this dashboard is worth building only for a
bespoke screen that merges live stats + eval history with your own framing, or a custom
RAGAS trend (e.g. faithfulness-over-versions).

If built: Streamlit, reading ONLY via the Langfuse SDK (live) and/or local
evals/results/*.json (eval history) — no new datastore.

Run (once implemented):  streamlit run dashboard/app.py
"""

# Intentionally not implemented — see ARCHITECTURE §8. Build on request in Phase 6.
