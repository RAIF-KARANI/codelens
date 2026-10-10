"""Retrieval over the code graph. Phase 2: vector search; Phase 4 adds full-text,
fusion, graph expansion and reranking (planning.md §6)."""

from core.retrieval.vector import SearchHit, vector_search

__all__ = ["SearchHit", "vector_search"]
