"""langfuse_sync.py — push the golden set + eval runs/scores to Langfuse.

STUB (guide-then-review, CLAUDE.md §4). Implement after the build-guide.

Purpose (ARCHITECTURE §7.5): mirror the in-repo golden set into a Langfuse Dataset, and
record each evaluation as a Dataset Run so Langfuse's run-comparison view shows metric
deltas across versions side by side (the reason a custom dashboard is optional).

SHAPE to verify against the installed Langfuse v3 SDK (v2→v3 breaking change, §12):
    from langfuse import Langfuse
    langfuse = Langfuse()                       # reads LANGFUSE_* from env
    langfuse.create_score(trace_id=..., name="faithfulness", value=0.91, data_type="NUMERIC")
"""

from __future__ import annotations


def upload_golden_set(dataset_name: str, items: list[dict]) -> None:
    """Upload the fixed golden set once as a Langfuse Dataset.

    TODO(guide): create the dataset + one dataset item per golden-set entry.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def push_run(run_name: str, per_item_scores: list[dict]) -> None:
    """Record one evaluation as a Langfuse Dataset Run.

    run_name = f"{git_sha}-{short_desc}" (e.g. "a1b2c3d-add-reranker").
    Each item → a run item linked to its trace; each RAGAS metric → a NUMERIC score.

    TODO(guide): link run items to traces and create_score per metric.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")
