"""ragas_setup.py — local Ollama judge + embeddings for RAGAS.

STUB (guide-then-review, CLAUDE.md §4). Implement after the build-guide; do not fill in
the bodies yet.

Purpose (ARCHITECTURE §7.4): keep evaluation fully self-hosted by wrapping local Ollama
models as the RAGAS judge LLM and embeddings. Judge quality drives metric quality — a
small local model is noisier, so prefer a *larger* Ollama model for the judge than the
generator, and be consistent within a comparison.

SHAPE to verify against the installed RAGAS version (do not trust signatures blindly,
ARCHITECTURE §12):
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from langchain_ollama import ChatOllama, OllamaEmbeddings
"""

from __future__ import annotations


def get_judge():
    """Return the RAGAS judge LLM (LangchainLLMWrapper around a capable local ChatOllama).

    TODO(guide): wrap ChatOllama(model="<capable-local-model>") in LangchainLLMWrapper.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")


def get_embeddings():
    """Return the RAGAS embeddings (LangchainEmbeddingsWrapper around Ollama embeddings).

    TODO(guide): wrap your existing Ollama embeddings in LangchainEmbeddingsWrapper.
    """
    raise NotImplementedError("guide-then-review: implement after the build-guide")
