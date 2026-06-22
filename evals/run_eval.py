"""run_eval.py — the offline evaluation runner ("did my change help?").

STUB (guide-then-review, CLAUDE.md §4). Implement after the build-guide; this is the
heart of the eval methodology, so it is yours to write.

Flow (ARCHITECTURE §7.2):
    1. Load evals/datasets/golden_set.jsonl.
    2. For each item, POST the question to the running RAG API with stream=false
       (HTTP only, never import RAG code — contract/README.md). Read back
       `answer` + `retrieved_contexts`.
    3. Assemble a RAGAS dataset (samples: user_input, response, retrieved_contexts, reference).
    4. evaluate(dataset, metrics=get_ragas_metrics(), llm=get_judge(), embeddings=get_embeddings()).
    5. Push to Langfuse as a Dataset Run named for the version (langfuse_sync.push_run).
    6. Write evals/results/<timestamp>_<version>.json (+ optional CSV).

Usage (intended):
    python evals/run_eval.py --api http://localhost:8000/chat --version "a1b2c3d-add-reranker"

Reading results responsibly (§7.6): judge scores wobble. Don't act on a single 0.02–0.05
swing on ~25 items; look for consistent movement; re-run borderline evals 2–3×; never
change the golden set and a RAG version in the same step.
"""

from __future__ import annotations

import argparse
from pathlib import Path

GOLDEN_SET = Path(__file__).parent / "datasets" / "golden_set.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"


def load_golden_set(path: Path = GOLDEN_SET) -> list[dict]:
    """Load and parse the fixed, version-controlled golden set (JSONL).

    TODO(guide): read JSONL → list[dict].
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def query_rag(api_url: str, item: dict) -> dict:
    """POST one question to the RAG API (stream=false) and return answer + contexts.

    TODO(guide): call the API per contract/README.md and parse the response.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def run(api_url: str, version: str) -> None:
    """Orchestrate the full run: query → RAGAS → Langfuse sync → save results.

    TODO(guide): wire steps 1–6 above.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline RAG evaluation.")
    parser.add_argument("--api", default="http://localhost:8000/chat", help="RAG API endpoint")
    parser.add_argument("--version", required=True, help="run label, e.g. <git_sha>-<desc>")
    args = parser.parse_args()
    run(args.api, args.version)


if __name__ == "__main__":
    main()
