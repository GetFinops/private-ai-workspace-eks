"""M14 media services — control-plane routing to isolated GPU media backends.

Media services (speech-to-text, image generation, ...) run as their own isolated
GPU Helm releases (vLLM-shape: ClusterIP, ingress restricted to the control
plane, GPU taint/nodeSelector). The control plane is the only caller; it enforces
auth, a deny-by-default per-tenant allow-list, operator + per-tenant kill-
switches, per-tenant rate limits, server-side size / content-policy caps,
per-tenant S3 isolation for artifacts, shape-only audit, and media.task
notifications. The model weights live only in the GPU service, never the
control-plane image.

Backends are reached over plain in-cluster HTTP (like the vLLM / embedding
clients) — they are trusted internal services, NOT the M13 integration egress
path, so they do not go through the outbound URL guard.

Security model mirrors M11/M12/M13:
  - operator kill-switch (MEDIA_ENABLED);
  - per-tenant operator disable (reuses the TenantIntegrationState abstraction);
  - deny-by-default per-tenant allow-list (MEDIA_ALLOWLIST);
  - dedicated per-tenant rate/concurrency limiter;
  - server-side size caps (audio bytes, prompt length) + output caps;
  - audit is shape-only (service, op, input shape, result class, latency) — never
    media content, prompts, or transcripts.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from urllib import request as _urlrequest
from urllib.error import HTTPError, URLError

from app.control_plane.agent_tools import RateLimiter, _audit, is_allowed
from app.control_plane.integrations import TenantIntegrationState
from app.control_plane.notifications import (
    NotificationStore,
    _extract_tenant_id,
    _verify_and_extract,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0          # media is slow (model inference on GPU)
_DEFAULT_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_PROMPT_CHARS = 2000
_MAX_RESULT_BYTES = 16 * 1024 * 1024  # cap an artifact pulled back from a backend

# Media task notification classes (must also be whitelisted in notifications.py).
_EVENT_DONE = "media_task_completed"
_EVENT_FAILED = "media_task_failed"


# ── Service registry ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MediaService:
    """A registered media backend. `kind` is "stt" or "image"."""

    name: str
    kind: str
    base_url: str  # in-cluster ClusterIP base, e.g. http://whisper.inference.svc:8000


# Default registry is EMPTY — deny by default. Real services are registered at
# wiring time from config; each is a separate per-service adoption decision.
MEDIA_SERVICES: dict[str, MediaService] = {}


def parse_media_services(raw: str | None) -> dict[str, MediaService]:
    """Parse MEDIA_SERVICES JSON {"<name>": {"kind","base_url"}} into the registry."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("MEDIA_SERVICES is not valid JSON — no media services registered.")
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, MediaService] = {}
    for name, spec in data.items():
        if not isinstance(spec, dict):
            continue
        kind = spec.get("kind")
        base_url = spec.get("base_url")
        if kind in ("stt", "image") and isinstance(base_url, str) and base_url:
            out[str(name)] = MediaService(name=str(name), kind=kind, base_url=base_url.rstrip("/"))
    return out


def parse_media_allowlist(raw: str | None) -> dict[str, frozenset[str]]:
    """Parse MEDIA_ALLOWLIST JSON: {"<tenant>": ["<service>"]} — deny by default."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("MEDIA_ALLOWLIST is not valid JSON — treating as empty (deny all).")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(tenant): frozenset(str(s) for s in items)
        for tenant, items in data.items()
        if isinstance(items, list)
    }


# ── Executor ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MediaOutcome:
    # success | unknown_service | wrong_kind | backend_error | backend_timeout
    result_class: str
    status: int | None = None
    result: dict | None = None


class MediaExecutor:
    """Forwards a media request to its GPU backend and handles the artifact."""

    def __init__(
        self,
        *,
        services: "dict[str, MediaService] | None" = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        storage_client=None,
        opener=None,
    ) -> None:
        self._services = services if services is not None else MEDIA_SERVICES
        self._timeout = timeout_seconds
        self._storage = storage_client
        # Injectable for tests; defaults to urllib.request.urlopen.
        self._open = opener or _urlrequest.urlopen

    def available(self, tenant_id: str, allowlist: dict) -> list[str]:
        return sorted(n for n in self._services if is_allowed(allowlist, tenant_id, n))

    def _post(self, url: str, *, data: bytes, content_type: str) -> "tuple[int, bytes, str]":
        req = _urlrequest.Request(
            url, data=data, headers={"Content-Type": content_type}, method="POST"
        )
        resp = self._open(req, timeout=self._timeout)
        body = resp.read(_MAX_RESULT_BYTES)
        return resp.status, body, resp.headers.get("Content-Type", "")

    def transcribe(self, service: str, audio: bytes, content_type: str, *, tenant_id: str) -> MediaOutcome:
        spec = self._services.get(service)
        if spec is None:
            return MediaOutcome("unknown_service")
        if spec.kind != "stt":
            return MediaOutcome("wrong_kind")
        try:
            status, body, _ = self._post(
                f"{spec.base_url}/v1/audio/transcriptions", data=audio, content_type=content_type
            )
        except HTTPError as e:
            return MediaOutcome("backend_error", status=e.code)
        except (URLError, TimeoutError):
            return MediaOutcome("backend_timeout")
        except OSError:
            return MediaOutcome("backend_error")
        try:
            data = json.loads(body)
            text = data.get("text", "") if isinstance(data, dict) else ""
        except (ValueError, json.JSONDecodeError):
            text = ""
        # Transcript is the user's own data returned to them; never logged.
        return MediaOutcome("success", status=status, result={"text": text})

    def generate(self, service: str, prompt: str, params: dict, *, tenant_id: str, user_id: str) -> MediaOutcome:
        spec = self._services.get(service)
        if spec is None:
            return MediaOutcome("unknown_service")
        if spec.kind != "image":
            return MediaOutcome("wrong_kind")
        payload = json.dumps({"prompt": prompt, **(params or {})}).encode("utf-8")
        try:
            status, body, ctype = self._post(
                f"{spec.base_url}/v1/images/generations", data=payload, content_type="application/json"
            )
        except HTTPError as e:
            return MediaOutcome("backend_error", status=e.code)
        except (URLError, TimeoutError):
            return MediaOutcome("backend_timeout")
        except OSError:
            return MediaOutcome("backend_error")
        # Persist the generated artifact per-tenant in S3; return a handle, never
        # the bytes inline (and never the prompt).
        artifact_id = str(uuid.uuid4())
        key = f"media/{tenant_id}/{user_id}/{artifact_id}.bin"
        stored = False
        if self._storage is not None:
            try:
                self._storage.put_object(key=key, body=body, content_type=ctype or "application/octet-stream")
                stored = True
            except Exception:  # noqa: BLE001 - storage failure must not leak internals
                return MediaOutcome("backend_error")
        return MediaOutcome(
            "success", status=status,
            result={"artifact_id": artifact_id, "bytes": len(body), "stored": stored, "key": key},
        )


# ── HTTP handlers ─────────────────────────────────────────────────────────────


def _gate(authorization, token_verifier, enabled):
    claims, err = _verify_and_extract(authorization, token_verifier)
    if err is not None:
        return None, None, err
    tenant_id = _extract_tenant_id(claims)
    user_id = claims.subject
    if not enabled:
        return None, None, (HTTPStatus.SERVICE_UNAVAILABLE, {
            "error": "media_disabled",
            "detail": "Media services are disabled on this instance.",
            "status": "degraded",
        })
    return tenant_id, user_id, None


def _authorize(tenant_id, user_id, service, allowlist, tenant_state, label, shape):
    """Shared allow-list + per-tenant disable check; returns an error tuple or None."""
    if not is_allowed(allowlist, tenant_id, service):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=shape, decision="denied")
        return HTTPStatus.FORBIDDEN, {"error": "service_not_allowed"}
    if not tenant_state.is_enabled(tenant_id, service):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=shape, decision="tenant_disabled")
        return HTTPStatus.FORBIDDEN, {"error": "tenant_disabled"}
    return None


def build_media_list_response(
    *, authorization, body, token_verifier, enabled, allowlist, executor,
) -> tuple[int, dict]:
    """POST /v1/media/list — media services allow-listed for the caller's tenant."""
    tenant_id, _user, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    return HTTPStatus.OK, {"services": executor.available(tenant_id, allowlist)}


def build_media_transcribe_response(
    *, authorization, service, body, content_type, token_verifier, enabled, allowlist,
    executor, rate_limiter: RateLimiter, tenant_state: TenantIntegrationState,
    max_audio_bytes: int = _DEFAULT_MAX_AUDIO_BYTES, notification_store: NotificationStore | None = None,
) -> tuple[int, dict]:
    """POST /v1/media/transcribe?service=<name> — binary audio body."""
    tenant_id, user_id, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    if not isinstance(service, str) or not service:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'service' query param is required."}
    if not body:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "audio body is required."}
    if len(body) > max_audio_bytes:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "audio_too_large"}
    label = f"media:{service}/transcribe"
    # Raw args for shape-only audit; _audit records key+type/size, never values.
    args = {"audio": body}
    deny = _authorize(tenant_id, user_id, service, allowlist, tenant_state, label, args)
    if deny is not None:
        return deny
    if not rate_limiter.try_acquire(tenant_id, now=int(time.time())):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=args, decision="rate_limited")
        return HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"}
    started = time.monotonic()
    try:
        outcome = executor.transcribe(service, body, content_type or "application/octet-stream", tenant_id=tenant_id)
    finally:
        rate_limiter.release()
    latency_ms = int((time.monotonic() - started) * 1000)
    _audit(tenant=tenant_id, user=user_id, tool=label, arguments=args,
           decision="allowed", result_class=outcome.result_class, latency_ms=latency_ms)
    _maybe_notify(notification_store, tenant_id, user_id, service, outcome)
    return _to_http(outcome)


def build_media_generate_response(
    *, authorization, body, token_verifier, enabled, allowlist, executor,
    rate_limiter: RateLimiter, tenant_state: TenantIntegrationState,
    max_prompt_chars: int = _DEFAULT_MAX_PROMPT_CHARS, notification_store: NotificationStore | None = None,
) -> tuple[int, dict]:
    """POST /v1/media/generate — body {"service", "prompt", "params"}."""
    tenant_id, user_id, err = _gate(authorization, token_verifier, enabled)
    if err is not None:
        return err
    try:
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    if not isinstance(data, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request"}
    service = data.get("service")
    prompt = data.get("prompt")
    params = data.get("params", {})
    if not isinstance(service, str) or not service:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'service' is required."}
    if not isinstance(prompt, str) or not prompt:
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'prompt' is required."}
    if not isinstance(params, dict):
        return HTTPStatus.BAD_REQUEST, {"error": "bad_request", "detail": "'params' must be an object."}
    if len(prompt) > max_prompt_chars:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "prompt_too_long"}
    label = f"media:{service}/generate"
    # Raw args for shape-only audit; _audit records key+type/size, never the
    # prompt text.
    args = {"prompt": prompt, "params": params}
    deny = _authorize(tenant_id, user_id, service, allowlist, tenant_state, label, args)
    if deny is not None:
        return deny
    if not rate_limiter.try_acquire(tenant_id, now=int(time.time())):
        _audit(tenant=tenant_id, user=user_id, tool=label, arguments=args, decision="rate_limited")
        return HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited"}
    started = time.monotonic()
    try:
        outcome = executor.generate(service, prompt, params, tenant_id=tenant_id, user_id=user_id)
    finally:
        rate_limiter.release()
    latency_ms = int((time.monotonic() - started) * 1000)
    _audit(tenant=tenant_id, user=user_id, tool=label, arguments=args,
           decision="allowed", result_class=outcome.result_class, latency_ms=latency_ms)
    _maybe_notify(notification_store, tenant_id, user_id, service, outcome)
    return _to_http(outcome)


def _to_http(outcome: MediaOutcome) -> tuple[int, dict]:
    rc = outcome.result_class
    if rc == "success":
        return HTTPStatus.OK, {"status": outcome.status, "result": outcome.result}
    if rc == "unknown_service":
        return HTTPStatus.NOT_FOUND, {"error": "unknown_service"}
    if rc == "wrong_kind":
        return HTTPStatus.BAD_REQUEST, {"error": "wrong_service_kind"}
    if rc == "backend_timeout":
        return HTTPStatus.GATEWAY_TIMEOUT, {"error": "backend_timeout", "status": "degraded"}
    return HTTPStatus.BAD_GATEWAY, {"error": "backend_error", "status": "degraded"}


def _maybe_notify(notification_store, tenant_id, user_id, service, outcome) -> None:
    if notification_store is None:
        return
    event = _EVENT_DONE if outcome.result_class == "success" else _EVENT_FAILED
    try:
        from app.control_plane.notifications import (
            ALLOWED_EVENT_CLASSES,
            NotificationEvent,
            _now_utc,
        )
        if event in ALLOWED_EVENT_CLASSES:
            notification_store.publish(NotificationEvent(
                id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id,
                event_class=event, resource_id=f"media:{service}", created_at=_now_utc()))
    except Exception:  # pragma: no cover - best-effort
        pass
