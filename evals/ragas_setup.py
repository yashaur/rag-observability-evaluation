"""ragas_setup.py — hosted judge + local Ollama embeddings for RAGAS.

STUB (guide-then-review, CLAUDE.md §4). Implement after the build-guide; do not fill in
the bodies yet.

Purpose (ARCHITECTURE §7.4): the JUDGE is a hosted frontier model (GPT or Claude), pinned
to a dated snapshot — judge quality drives metric quality, and a frontier model is stabler
(less jitter) and follows RAGAS's structured-output format reliably. EMBEDDINGS stay local
Ollama (no jitter problem, free). Pick ONE judge provider and keep it fixed within a
comparison. A larger local Ollama judge remains a free fallback.

SHAPE to verify against the installed RAGAS version (do not trust signatures blindly,
ARCHITECTURE §12):
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_openai import ChatOpenAI         # judge (or langchain_anthropic.ChatAnthropic)
    from langchain_ollama import OllamaEmbeddings    # embeddings stay local
"""

from __future__ import annotations


def get_judge():
    """Return the RAGAS judge LLM (LangchainLLMWrapper around a hosted frontier model).

    TODO(guide): wrap ChatOpenAI / ChatAnthropic(model="<dated-snapshot>", temperature=0)
    in LangchainLLMWrapper. Pin a dated snapshot; keep the provider/model fixed per
    comparison. (A larger local ChatOllama remains a free fallback.)
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def get_embeddings():
    """Return the RAGAS embeddings (LangchainEmbeddingsWrapper around Ollama embeddings).

    TODO(guide): wrap your existing Ollama embeddings in LangchainEmbeddingsWrapper.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")
