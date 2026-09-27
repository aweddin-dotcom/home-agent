"""OpenAI-compatible chat API, so Open WebUI can offer the assistant as a model.

  uvicorn services.agent.api:app --port 8000

Implements only what Open WebUI needs: GET /v1/models and
POST /v1/chat/completions, streaming or not.
"""

import json
import time
import uuid

from fastapi import Body, FastAPI
from fastapi.responses import StreamingResponse

MODEL_ID = "home-agent"


def assistant_from_settings():
    from qdrant_client import QdrantClient

    from services.common import settings
    from services.common.ollama import OllamaChat, OllamaEmbedder
    from services.embedding.index import EmailIndex
    from services.ingestion.store import Store

    from .assistant import Assistant
    from .status import sync_notes

    model = settings.models()
    search = settings.retrieval()["search"]
    return Assistant(
        chat=OllamaChat(settings.OLLAMA_BASE_URL, model["chat"], num_ctx=model["chat_context_tokens"]),
        embedder=OllamaEmbedder(settings.OLLAMA_BASE_URL, model["embedding"], model["embedding_query_template"]),
        index=EmailIndex(QdrantClient(url=settings.QDRANT_URL), search["collection"]),
        load_events=lambda: Store(settings.STRUCTURED_DB, readonly=True).all_events(),
        open_mail=lambda: Store(settings.STRUCTURED_DB, readonly=True),
        status_notes=lambda: sync_notes(
            Store(settings.STRUCTURED_DB, readonly=True).sync_statuses(),
            list(settings.accounts()),
            stale_after_minutes=settings.retrieval()["sync"]["stale_after_minutes"],
        ),
        tz=settings.TIMEZONE,
        top_k=search["top_k"],
    )


def _chunk(completion_id, created, delta, finish_reason=None):
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _safe(pieces):
    """Turn a failure mid-answer into a readable message instead of a broken stream."""
    try:
        yield from pieces
    except Exception as error:  # noqa: BLE001 - shown to the user, not swallowed
        yield f"\n\n(Something went wrong: {type(error).__name__}: {error})"


def create_app(assistant=None):
    app = FastAPI(title="Home Agent")
    state = {"assistant": assistant}

    def get_assistant():
        if state["assistant"] is None:
            state["assistant"] = assistant_from_settings()
        return state["assistant"]

    @app.get("/v1/models")
    def list_models():
        return {
            "object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "home-agent"}],
        }

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict = Body(...)):
        pieces = _safe(get_assistant().respond(body.get("messages", [])))
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        if body.get("stream"):
            def events():
                yield _chunk(completion_id, created, {"role": "assistant"})
                for piece in pieces:
                    yield _chunk(completion_id, created, {"content": piece})
                yield _chunk(completion_id, created, {}, "stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(events(), media_type="text/event-stream")

        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": MODEL_ID,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": "".join(pieces)}, "finish_reason": "stop"}
            ],
        }

    return app


app = create_app()
