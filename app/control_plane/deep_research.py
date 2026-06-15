"""M11 deep-research — a constrained plan -> retrieve -> synthesize agent.

A fixed-shape specialization of the agent loop (app/control_plane/agent_loop.py)
that answers a research question over the TENANT'S OWN retrieval corpus (M10):

  1. plan      — the model decomposes the question into a few focused search
                 queries;
  2. retrieve  — each query is embedded and run against the tenant-scoped
                 retrieval store (M10 isolation, no cross-tenant bypass);
  3. synthesize— the model writes a cited answer using only the retrieved
                 passages.

See docs/m11-followups/02-deep-research.md and NOTICE ("M11 deep-research
adoption decision"): original code, nothing vendored. Carried constraints:
deny-by-default per-tenant allow-list ("deep_research" capability), the operator
kill-switch, clean refusal when inference is cold, server-enforced budgets, and
shape-only audit/telemetry — never the question, passages, or answer.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from app.control_plane.agent_loop import ChatClient, _content, _extract_json_object
from app.control_plane.agent_tools import RateLimiter, _audit, is_allowed
from app.control_plane.embeddings import EmbeddingClient, embed_measured
from app.control_plane.inference import ChatCompletionRequest, ChatMessage
from app.control_plane.notifications import (
    ALLOWED_EVENT_CLASSES,
    NotificationEvent,
    NotificationStore,
    _extract_tenant_id,
    _now_utc,
    _verify_and_extract,
)
from app.control_plane.retrieval import RetrievalStore
from app.control_plane.token_verifier import TokenVerifier
from http import HTTPStatus

logger = logging.getLogger(__name__)

_CAPABILITY = "deep_research"
_MAX_QUESTION_BYTES = 4000
_MAX_PASSAGES = 12

_EVENT_PROGRESS = "agent_task_progress"
_EVENT_COMPLETED = "agent_task_completed"
_EVENT_FAILED = "agent_task_failed"


@dataclass(frozen=True)
class DeepResearchBudgets:
    max_subqueries: int = 4
    top_k: int = 5
    wall_clock_seconds: float = 90.0
    max_tokens: int = 512
    model: str = "default"


@dataclass(frozen=True)
class DeepResearchOutcome:
    status: str                       # completed | failed
    answer: str | None = None
    sources: list[str] = field(default_factory=list)   # cited document_ids
    subqueries: int = 0
    detail: str | None = None


# ── Phases (each takes the model/store; all untrusted output parsed defensively) ─


def _plan(question: str, *, inference: ChatClient, budgets: DeepResearchBudgets) -> list[str]:
    system = (
        "You decompose a research question into focused search queries. Reply "
        "with EXACTLY one JSON object and nothing else: "
        '{"queries": ["...", "..."]}. Use at most '
        f"{budgets.max_subqueries} queries."
    )
    resp = inference.chat_completions(ChatCompletionRequest.build(
        model=budgets.model,
        messages=[ChatMessage("system", system), ChatMessage("user", question)],
        max_tokens=budgets.max_tokens,
    ))
    obj = _extract_json_object(_content(resp)) or {}
    raw = obj.get("queries")
    queries = (
        [q.strip() for q in raw if isinstance(q, str) and q.strip()][: budgets.max_subqueries]
        if isinstance(raw, list)
        else []
    )
    return queries or [question]   # fall back to the question itself


def _retrieve(
    queries: list[str],
    *,
    tenant_id: str,
    store: RetrievalStore,
    embedding_client: EmbeddingClient,
    budgets: DeepResearchBudgets,
) -> list:
    seen: set = set()
    passages: list = []
    for q in queries:
        embedding = embed_measured(embedding_client, [q])[0]
        for p in store.query(tenant_id=tenant_id, embedding=embedding, top_k=budgets.top_k):
            if p.chunk_id in seen:
                continue
            seen.add(p.chunk_id)
            passages.append(p)
            if len(passages) >= _MAX_PASSAGES:
                return passages
    return passages


def _synthesize(
    question: str, passages: list, *, inference: ChatClient, budgets: DeepResearchBudgets
) -> str:
    if passages:
        context = "\n".join(
            f"[{i + 1}] (doc:{p.document_id}) {p.content}" for i, p in enumerate(passages)
        )
    else:
        context = "(no relevant documents were found)"
    system = (
        "Answer the question using ONLY the numbered sources provided. Cite "
        "sources inline as [n]. If the sources do not contain the answer, say "
        "so plainly; do not invent facts."
    )
    user = f"Question: {question}\n\nSources:\n{context}"
    resp = inference.chat_completions(ChatCompletionRequest.build(
        model=budgets.model,
        messages=[ChatMessage("system", system), ChatMessage("user", user)],
        max_tokens=budgets.max_tokens,
    ))
    return _content(resp)


def _emit(store, *, tenant_id, user_id, event_class, run_id) -> None:
    if store is None or event_class not in ALLOWED_EVENT_CLASSES:
        return
    try:
        store.publish(NotificationEvent(
            id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
            event_class=event_class, resource_id=run_id, created_at=_now_utc(),
        ))
    except Exception:  # pragma: no cover - best-effort
        pass


def run_deep_research(
    *,
    question: str,
    tenant_id: str,
    user_id: str,
    store: RetrievalStore,
    embedding_client: EmbeddingClient,
    inference_client: ChatClient,
    budgets: DeepResearchBudgets,
    notification_store: NotificationStore | None = None,
) -> DeepResearchOutcome:
    """Run plan -> retrieve -> synthesize. Pure orchestration; retrieval is the
    tenant's own corpus and all model output is treated as untrusted."""
    run_id = str(uuid.uuid4())
    _emit(notification_store, tenant_id=tenant_id, user_id=user_id,
          event_class=_EVENT_PROGRESS, run_id=run_id)
    started = time.monotonic()

    def _over_budget() -> bool:
        return time.monotonic() - started > budgets.wall_clock_seconds

    def _fail(detail: str, subqueries: int = 0) -> DeepResearchOutcome:
        _emit(notification_store, tenant_id=tenant_id, user_id=user_id,
              event_class=_EVENT_FAILED, run_id=run_id)
        return DeepResearchOutcome(status="failed", subqueries=subqueries, detail=detail)

    try:
        queries = _plan(question, inference=inference_client, budgets=budgets)
        if _over_budget():
            return _fail("wall_clock", len(queries))
        passages = _retrieve(queries, tenant_id=tenant_id, store=store,
                             embedding_client=embedding_client, budgets=budgets)
        if _over_budget():
            return _fail("wall_clock", len(queries))
        answer = _synthesize(question, passages, inference=inference_client, budgets=budgets)
    except Exception as exc:  # noqa: BLE001 - inference/embedding failure → clean fail
        logger.warning("Deep-research failure: %s", type(exc).__name__)
        return _fail("inference_unavailable")

    sources: list[str] = []
    for p in passages:
        if p.document_id not in sources:
            sources.append(p.document_id)

    # Shape-only audit: records the run, never the question/passages/answer.
    _audit(tenant=tenant_id, user=user_id, tool="deep_research",
           arguments={"question": question}, decision="allowed", result_class="success")
    _emit(notification_store, tenant_id=tenant_id, user_id=user_id,
          event_class=_EVENT_COMPLETED, run_id=run_id)
    return DeepResearchOutcome(
        status="completed", answer=answer, sources=sources, subqueries=len(queries))


# ── HTTP handler ──────────────────────────────────────────────────────────────


def build_deep_research_response(
    *,
    authorization: str | None,
    body: bytes,
    token_verifier: TokenVerifier | None,
    enabled: bool,
    allowlist: dict[str, frozenset[str]],
    store: RetrievalStore,
    embedding_client: EmbeddingClient,
    inference_client: ChatClient | None,
    budgets: DeepResearchBudgets,
    rate_limiter: RateLimiter,
    notification_store: NotificationStore | None = None,
) -> tuple[int, dict]:
    """Handle POST /v1/agent/research. Body: {"question": "<text>"}."""
    import json

    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return err
    tenant_id = _extract_tenant_id(claims)  # type: ignore[arg-type]
    user_id = claims.subject  # type: ignore[union-attr]

    if not enabled:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "tools_disabled",
            "detail": "Agent execution is disabled on this instance.",
            "status": "degraded",
        }
    if inference_client is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "research_unavailable",
            "detail": "The inference backend is not configured; deep research is unavailable.",
            "status": "degraded",
        }

    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "Body is not valid JSON."}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'question' is required."}
    if len(question.encode("utf-8")) > _MAX_QUESTION_BYTES:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
            "error": "payload_too_large",
            "detail": f"question exceeds {_MAX_QUESTION_BYTES} bytes.",
        }

    # Deny by default: the tenant must be allow-listed for the deep_research
    # capability. Audited, like any other denied tool.
    if not is_allowed(allowlist, tenant_id, _CAPABILITY):
        _audit(tenant=tenant_id, user=user_id, tool=_CAPABILITY,
               arguments={"question": question}, decision="denied")
        return HTTPStatus.FORBIDDEN, {
            "error": "not_allowed",
            "detail": "Deep research is not permitted for your organisation.",
        }

    now = int(time.time())
    if not rate_limiter.try_acquire(tenant_id, now=now):
        return HTTPStatus.TOO_MANY_REQUESTS, {
            "error": "rate_limited",
            "detail": "Deep-research rate or concurrency limit exceeded; try again shortly.",
        }
    try:
        outcome = run_deep_research(
            question=question, tenant_id=tenant_id, user_id=user_id, store=store,
            embedding_client=embedding_client, inference_client=inference_client,
            budgets=budgets, notification_store=notification_store,
        )
    finally:
        rate_limiter.release()

    if outcome.status == "failed":
        return HTTPStatus.BAD_GATEWAY, {
            "status": "failed",
            "error": "research_failed",
            "detail": "The deep-research run failed before completing.",
        }
    return HTTPStatus.OK, {
        "status": outcome.status,
        "answer": outcome.answer,
        "sources": outcome.sources,
        "subqueries": outcome.subqueries,
    }
