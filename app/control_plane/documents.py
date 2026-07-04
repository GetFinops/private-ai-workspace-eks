"""Documents editor — a stateless AI-edit endpoint over the inference client.

The document itself is persisted as a note of kind="doc" (per-tenant/user, the
same isolation + content policy as notes.py). This module adds only the
writing-first capability: given a document plus an instruction, return an
AI-edited version. Stateless — the client decides whether to apply/save the
result. Gated like chat: authenticated, inference-available (degrades cleanly
when cold), rate-limited; NO agent-tools allow-list. Audit is shape-only — never
the document text or the instruction.
"""
from __future__ import annotations

import json
import time
from http import HTTPStatus

from app.control_plane.agent_loop import _content
from app.control_plane.agent_tools import RateLimiter, _audit
from app.control_plane.inference import ChatCompletionRequest, ChatMessage
from app.control_plane.notifications import _extract_tenant_id, _verify_and_extract
from app.control_plane.routing import InferenceUnavailableError
from app.control_plane.token_verifier import TokenVerifier

_MAX_CONTENT_CHARS = 20000
_MAX_INSTRUCTION_CHARS = 2000
_MAX_TOKENS = 1024

_SYSTEM = (
    "You are a writing assistant. Apply the user's instruction to the document and "
    "return ONLY the revised document text — no preamble, no explanation, no code "
    "fences. Preserve the author's voice and any content the instruction does not "
    "ask you to change."
)


def build_document_edit_response(
    *, authorization, body, token_verifier: TokenVerifier | None, enabled: bool,
    inference_client, rate_limiter: RateLimiter, max_content_chars: int = _MAX_CONTENT_CHARS,
) -> tuple[int, dict]:
    """Handle POST /v1/documents/edit. Body: {"content", "instruction", "model"?}."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    if not enabled or inference_client is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "documents_unavailable",
            "detail": "The inference backend is not configured; AI edit is unavailable.",
            "status": "degraded",
        }
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    content = data.get("content")
    instruction = data.get("instruction")
    model = data.get("model") or "default"
    if not isinstance(content, str) or not content.strip():
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'content' is required."}
    if not isinstance(instruction, str) or not instruction.strip():
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'instruction' is required."}
    if len(content) > max_content_chars:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "content_too_long"}
    if len(instruction) > _MAX_INSTRUCTION_CHARS:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "instruction_too_long"}
    if not rate_limiter.try_acquire(tenant_id, now=int(time.time())):
        return HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"}
    user = f"Instruction: {instruction}\n\nDocument:\n{content}"
    started = time.monotonic()
    outcome = None
    result = None
    try:
        resp = inference_client.chat_completions(ChatCompletionRequest.build(
            model=str(model),
            messages=[ChatMessage("system", _SYSTEM), ChatMessage("user", user)],
            max_tokens=_MAX_TOKENS,
        ))
        result = _content(resp)
    except InferenceUnavailableError:
        outcome = (HTTPStatus.SERVICE_UNAVAILABLE,
                   {"error": "documents_unavailable", "status": "degraded"})
    except Exception:  # noqa: BLE001 - inference failure → clean degrade
        outcome = (HTTPStatus.BAD_GATEWAY, {"error": "edit_failed", "status": "degraded"})
    finally:
        rate_limiter.release()
    if outcome is not None:
        return outcome
    latency_ms = int((time.monotonic() - started) * 1000)
    # Shape-only audit: never the document or the instruction text.
    _audit(tenant=tenant_id, user=user_id, tool="document_edit",
           arguments={"content": content, "instruction": instruction},
           decision="allowed", result_class="success", latency_ms=latency_ms)
    return HTTPStatus.OK, {"result": result}
