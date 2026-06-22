"""metrics.py — RAGAS metric selection + custom refusal-correct check.

STUB (guide-then-review, CLAUDE.md §4). Implement after the build-guide.

Metric set (ARCHITECTURE §7.3):
    - Faithfulness        (generation, no ground truth)   ← core anti-hallucination metric
    - Answer relevancy    (generation, no ground truth)
    - Context precision   (retrieval, uses reference)
    - Context recall      (retrieval, needs ground_truth)
    - Answer correctness  (end-to-end, needs ground_truth)
    - Refusal-correct     (custom behavior check on type == "refusal")

Hard IR metrics (Hit Rate@k, MRR) are an optional later add — only if you start tuning
retrieval precisely AND have labelled `ground_truth_contexts`.

SHAPE to verify against the installed RAGAS version (ARCHITECTURE §12):
    from ragas.metrics import (
        faithfulness, answer_relevancy,
        context_precision, context_recall, answer_correctness,
    )
"""

from __future__ import annotations


def get_ragas_metrics() -> list:
    """Return the curated list of RAGAS metric objects to evaluate.

    TODO(guide): assemble the metric list from ARCHITECTURE §7.3.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def refusal_correct(item: dict, answer: str) -> float:
    """Custom check: on out-of-KB ('refusal') questions, did the system abstain?

    Returns 1.0 if it correctly abstained, 0.0 if it hallucinated an answer.
    Only meaningful for items where item["type"] == "refusal".

    TODO(guide): decide the abstention signal (e.g. canonical refusal phrase / empty
    contexts) and score against it.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")
