"""Minimal clients for Ollama's embedding and chat APIs."""

import httpx


class OllamaEmbedder:
    def __init__(self, base_url, model, query_template="{query}", batch_size=16, timeout=300):
        self.url = f"{base_url.rstrip('/')}/api/embed"
        self.model = model
        self.query_template = query_template
        self.batch_size = batch_size
        self.timeout = timeout

    def _embed(self, texts):
        response = httpx.post(self.url, json={"model": self.model, "input": texts}, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["embeddings"]

    def embed_documents(self, texts):
        vectors = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self._embed(texts[i : i + self.batch_size]))
        return vectors

    def embed_query(self, query):
        return self._embed([self.query_template.format(query=query)])[0]


class OllamaChat:
    def __init__(self, base_url, model, num_ctx=8192, think=False, timeout=600):
        self.url = f"{base_url.rstrip('/')}/api/chat"
        self.model = model
        self.num_ctx = num_ctx
        self.think = think
        self.timeout = timeout

    def complete(self, system, user, schema=None, temperature=None):
        """Reply to one system + user message. With `schema` (a JSON schema),
        the model must reply with JSON matching it."""
        options = {"num_ctx": self.num_ctx}
        if temperature is not None:
            options["temperature"] = temperature
        body = {
            "model": self.model,
            "stream": False,
            "think": self.think,
            "options": options,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None:
            body["format"] = schema
        response = httpx.post(self.url, json=body, timeout=self.timeout)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()
