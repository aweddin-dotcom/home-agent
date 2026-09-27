"""Email chunks in Qdrant, one point per chunk."""

import uuid

from qdrant_client import models as qm


class EmailIndex:
    def __init__(self, client, collection):
        self.client = client
        self.collection = collection

    def ensure(self, dimensions):
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=qm.VectorParams(size=dimensions, distance=qm.Distance.COSINE),
            )
            return
        existing = self.client.get_collection(self.collection).config.params.vectors.size
        if existing != dimensions:
            raise RuntimeError(
                f"Collection '{self.collection}' holds {existing}-dimension vectors but the embedding "
                f"model produces {dimensions}. The embedding model changed: re-index all email."
            )

    def upsert_email(self, email, chunks, vectors):
        self.ensure(len(vectors[0]))
        points = [
            qm.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"email:{email.id}:{i}")),
                vector=vector,
                payload={
                    "email_id": email.id,
                    "thread_id": email.thread_id,
                    "subject": email.subject,
                    "sender": email.sender,
                    "date": email.date,
                    "chunk_index": i,
                    "text": chunk,
                },
            )
            for i, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        self.client.upsert(self.collection, points=points)

    def search(self, vector, limit):
        if not self.client.collection_exists(self.collection):
            return []
        return self.client.query_points(self.collection, query=vector, limit=limit, with_payload=True).points
