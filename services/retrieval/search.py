"""Find the emails most relevant to a question.

  python -m services.retrieval.search "when is the plumber coming?"
"""

import sys

from services.common import settings


def search(query, embedder, index, top_k):
    """Best-matching emails, one entry per email (its highest-scoring chunk)."""
    points = index.search(embedder.embed_query(query), limit=top_k * 3)
    best = {}
    for point in points:  # already sorted by score, highest first
        key = (point.payload.get("account"), point.payload["email_id"])
        best.setdefault(key, {**point.payload, "score": point.score})
    return list(best.values())[:top_k]


def build_from_settings():
    from qdrant_client import QdrantClient

    from services.common.ollama import OllamaEmbedder
    from services.embedding.index import EmailIndex

    model = settings.models()
    embedder = OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"])
    index = EmailIndex(QdrantClient(url=settings.QDRANT_URL), settings.retrieval()["search"]["collection"])
    return embedder, index


def main():
    from services.common.containers import delegate_to_container

    delegate_to_container("agent-api", "services.retrieval.search")
    if len(sys.argv) < 2:
        sys.exit('Usage: python -m services.retrieval.search "your question"')
    embedder, index = build_from_settings()
    hits = search(" ".join(sys.argv[1:]), embedder, index, settings.retrieval()["search"]["top_k"])
    if not hits:
        print("No emails indexed yet. Run: python -m services.ingestion.sync")
    for i, hit in enumerate(hits, 1):
        print(f"[{i}] score {hit['score']:.2f}  {hit['date'][:10]}  ({hit.get('account', '')})  {hit['sender']}")
        print(f"    {hit['subject']}")


if __name__ == "__main__":
    main()
