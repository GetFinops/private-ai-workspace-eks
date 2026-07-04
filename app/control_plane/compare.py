"""Compare — blind side-by-side of one prompt across N models, + optional synthesis.

A thin chat feature over the existing inference client: it issues one chat
completion per model and returns them side-by-side (the UI blinds model identity
until the user reveals it). Gated like chat — authenticated, inference-available,
rate-limited — with NO agent-tools allow-list (it is not tool execution). Content
policy: audit is shape-only (model count), never the prompt or completions.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http import HTTPStatus

from app.control_plane.agent_loop import _content
from app.control_plane.agent_tools import RateLimiter, _audit
from app.control_plane.inference import ChatCompletionRequest, ChatMessage
from app.control_plane.notifications import _extract_tenant_id, _verify_and_extract
from app.control_plane.routing import InferenceUnavailableError
from app.control_plane.token_verifier import TokenVerifier

_MAX_PROMPT_CHARS = 8000
_MAX_MODELS = 4
_MAX_TOKENS = 512
_LABELS = "ABCDEFGH"


@dataclass(frozen=True)
class CompareResult:
    label: str          # blind label A/B/C…
    model: str
    content: str | None
    error: str | None = None


def _one(inference, model, messages, max_tokens):
    try:
        resp = inference.chat_completions(ChatCompletionRequest.build(
            model=model, messages=messages, max_tokens=max_tokens))
        return _content(resp), None
    except InferenceUnavailableError:
        return None, "unavailable"
    except Exception:  # noqa: BLE001 - one model failing must not sink the compare
        return None, "error"


def run_compare(prompt, models, *, inference_client, max_tokens=_MAX_TOKENS, synthesize=False):
    """Run the prompt against each model; optionally synthesise the successes.
    Pure orchestration; all model output is treated as untrusted."""
    messages = [ChatMessage("user", prompt)]
    results: list[CompareResult] = []
    for i, model in enumerate(models):
        content, err = _one(inference_client, model, messages, max_tokens)
        results.append(CompareResult(label=_LABELS[i], model=model, content=content, error=err))
    synthesis = None
    if synthesize:
        good = [r for r in results if r.content]
        if len(good) >= 2:
            joined = "\n\n".join(f"[{r.label}] {r.content}" for r in good)
            system = (
                "You are given two or more candidate answers to the same prompt, "
                "labelled [A], [B], … Synthesise the best combined answer and note "
                "where they agree or differ. Do not invent facts."
            )
            user = f"Prompt: {prompt}\n\nCandidates:\n{joined}"
            synthesis, _ = _one(
                inference_client, good[0].model,
                [ChatMessage("system", system), ChatMessage("user", user)], max_tokens,
            )
    return results, synthesis


def build_compare_response(
    *, authorization, body, token_verifier: TokenVerifier | None, enabled: bool,
    inference_client, rate_limiter: RateLimiter,
    max_prompt_chars: int = _MAX_PROMPT_CHARS, default_models=None,
) -> tuple[int, dict]:
    """Handle POST /v1/compare. Body: {"prompt", "models": ["a","b"], "synthesize"?}."""
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]
    if not enabled or inference_client is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "compare_unavailable",
            "detail": "The inference backend is not configured; compare is unavailable.",
            "status": "degraded",
        }
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    prompt = data.get("prompt")
    models = data.get("models") or list(default_models or [])
    synthesize = bool(data.get("synthesize"))
    if not isinstance(prompt, str) or not prompt.strip():
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'prompt' is required."}
    if len(prompt) > max_prompt_chars:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "prompt_too_long"}
    if not isinstance(models, list) or not (2 <= len(models) <= _MAX_MODELS):
        return HTTPStatus.BAD_REQUEST, {
            "error": "bad_request",
            "detail": f"'models' must list 2–{_MAX_MODELS} model names.",
        }
    models = [str(m) for m in models]
    if not rate_limiter.try_acquire(tenant_id, now=int(time.time())):
        return HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"}
    started = time.monotonic()
    try:
        results, synthesis = run_compare(
            prompt, models, inference_client=inference_client, synthesize=synthesize)
    finally:
        rate_limiter.release()
    latency_ms = int((time.monotonic() - started) * 1000)
    # Shape-only audit: model count, never the prompt or completions.
    _audit(tenant=tenant_id, user=user_id, tool="compare", arguments={"models": models},
           decision="allowed", result_class="success", latency_ms=latency_ms)
    return HTTPStatus.OK, {
        "results": [
            {"label": r.label, "model": r.model, "content": r.content, "error": r.error}
            for r in results
        ],
        "synthesis": synthesis,
    }
