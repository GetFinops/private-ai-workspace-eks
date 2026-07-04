"""Internal inference-plane contract and HTTP client.

The control plane talks to inference through a narrow interface so model
serving can remain internal-only and independently scalable.

The VLLMInferenceClient now performs a real HTTP call using the Python
standard-library urllib.  Timeout, retry, and error-handling policy follows
docs/inference-contract.md and docs/06-cloud-architecture.md.

Endpoint URL resolution is delegated to app.control_plane.routing so that
URL normalisation and scheme validation happen in one place.

Adapted from pewdiepie-archdaemon/odysseus (MIT) for error-type patterns
and request-shaping conventions; no third-party dependencies are introduced.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Literal, Protocol

from app.control_plane.routing import (
    InferenceEndpoint,
    InferenceRoutingError,
    InferenceUnavailableError,
    build_health_url,
)


Role = Literal["system", "user", "assistant", "tool"]

# Default timeout seconds for a single inference HTTP attempt.
_DEFAULT_TIMEOUT = 120

# Number of retry attempts (total tries = 1 + _MAX_RETRIES).
_MAX_RETRIES = 1

# Back-off seconds between retries.
_RETRY_BACKOFF = 2.0

# Timeout for the lightweight GPU-readiness probe. Kept short: /v1/inference/status
# is polled by the UI and must stay responsive even when the GPU is scaled to zero.
_STATUS_PROBE_TIMEOUT = 2.0


def probe_inference_health(
    base_url: str, timeout: float = _STATUS_PROBE_TIMEOUT
) -> dict[str, str]:
    """Probe the inference plane's ``/health`` endpoint and classify GPU readiness.

    Returns ``{"gpu": <state>, "detail": <short reason>}`` where state is one of:
      - ``"warm"``:    /health returned 200 — model loaded, ready to serve.
      - ``"loading"``: pod reachable but not ready (HTTP 5xx, e.g. 503 while the
                       model weights are still loading).
      - ``"cold"``:    endpoint unreachable (connection refused / timeout) — the
                       GPU node is scaled to zero (Karpenter scale-from-zero on
                       first use).
      - ``"unconfigured"``: no inference base URL is configured.
      - ``"unknown"``: any other outcome we can't classify.

    Performs a single short-timeout GET and never raises — callers treat any
    failure as unreachable.  Content policy (M5): only the HTTP status / failure
    class is inspected; no request or response body is read or logged.
    """
    if not base_url:
        return {"gpu": "unconfigured", "detail": "inference URL not configured"}
    try:
        url = build_health_url(base_url)
    except InferenceRoutingError:
        return {"gpu": "unconfigured", "detail": "inference URL not configured"}

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if 200 <= int(status) < 300:
                return {"gpu": "warm", "detail": "model ready"}
            return {"gpu": "loading", "detail": f"health returned {status}"}
    except urllib.error.HTTPError as exc:
        # Pod is answering but not ready (e.g. 503 while loading weights).
        if 500 <= exc.code < 600:
            return {"gpu": "loading", "detail": f"health returned {exc.code}"}
        return {"gpu": "unknown", "detail": f"health returned {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        # Connection refused / DNS failure / timeout → node scaled to zero.
        return {"gpu": "cold", "detail": "inference endpoint unreachable"}
    except Exception:  # pragma: no cover - defensive; probe must never raise
        return {"gpu": "unknown", "detail": "probe failed"}


@dataclass(frozen=True)
class ChatMessage:
    """Message accepted by an OpenAI-compatible chat-completions backend."""

    role: Role
    content: str

    def as_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class ChatCompletionRequest:
    """Control-plane request to the internal inference service."""

    model: str
    messages: tuple[ChatMessage, ...]
    temperature: float = 0.2
    max_tokens: int | None = None

    @classmethod
    def build(
        cls,
        *,
        model: str,
        messages: Iterable[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> "ChatCompletionRequest":
        return cls(
            model=model,
            messages=tuple(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def as_vllm_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [message.as_payload() for message in self.messages],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload


class InferenceClient(Protocol):
    """Interface implemented by internal model-serving clients."""

    def chat_completions(
        self, request: ChatCompletionRequest
    ) -> dict[str, object]:
        """Send a chat-completion request to the inference plane."""


@dataclass(frozen=True)
class VLLMInferenceClient:
    """vLLM client for an internal OpenAI-compatible service.

    Performs a real HTTP POST using urllib.request.  The client:
    - resolves and validates the endpoint URL via InferenceEndpoint
    - enforces a per-request timeout
    - retries once on transient network errors (not on 4xx/5xx)
    - raises InferenceUnavailableError on non-200 responses so callers
      can degrade gracefully without crashing the control plane
    """

    base_url: str
    timeout_seconds: float = _DEFAULT_TIMEOUT

    @property
    def endpoint(self) -> InferenceEndpoint:
        return InferenceEndpoint.from_base_url(self.base_url)

    @property
    def chat_completions_url(self) -> str:
        return self.endpoint.chat_completions_url

    def chat_completions(
        self, request: ChatCompletionRequest
    ) -> dict[str, object]:
        """POST a chat-completion request; return the parsed JSON response.

        Raises:
            InferenceRoutingError: endpoint URL is missing or invalid.
            InferenceUnavailableError: the inference service returned an error.
            TimeoutError: the request exceeded timeout_seconds.
        """
        url = self.chat_completions_url
        payload_bytes = json.dumps(request.as_vllm_payload()).encode("utf-8")

        # Propagate W3C traceparent so the inference span is linked to the
        # control-plane span.  Falls back silently if tracing is not configured.
        outgoing_headers: dict[str, str] = {"Content-Type": "application/json"}
        try:
            from app.control_plane.tracing import inject_trace_headers
            inject_trace_headers(outgoing_headers)
        except Exception:
            pass  # tracing is best-effort — proceed without trace headers

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                time.sleep(_RETRY_BACKOFF)
            try:
                req = urllib.request.Request(
                    url,
                    data=payload_bytes,
                    method="POST",
                    headers=outgoing_headers,
                )
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                raise InferenceUnavailableError(
                    f"Inference returned HTTP {exc.code} for {url}",
                    status_code=exc.code,
                ) from exc
            except urllib.error.URLError as exc:
                last_exc = exc
                continue
            except TimeoutError as exc:
                raise TimeoutError(
                    f"Inference request to {url} timed out after {self.timeout_seconds}s"
                ) from exc

        raise InferenceUnavailableError(
            f"Inference request to {url} failed after {_MAX_RETRIES + 1} "
            f"attempt(s): {last_exc}"
        ) from last_exc

    def open_chat_stream(self, request: ChatCompletionRequest):
        """Open a streaming chat completion; return a file-like SSE response.

        vLLM emits OpenAI-format Server-Sent Events ("data: {...}\\n\\n" …
        "data: [DONE]"). The caller relays those lines verbatim to the client.
        Raises InferenceUnavailableError on connect/HTTP error (so the caller can
        return a clean 503 BEFORE writing any stream bytes); TimeoutError on
        timeout. Not retried — a partial stream cannot be safely replayed.
        """
        url = self.chat_completions_url
        payload = dict(request.as_vllm_payload())
        payload["stream"] = True
        payload_bytes = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        try:
            from app.control_plane.tracing import inject_trace_headers
            inject_trace_headers(headers)
        except Exception:  # pragma: no cover
            pass
        req = urllib.request.Request(url, data=payload_bytes, method="POST", headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as exc:
            raise InferenceUnavailableError(
                f"Inference returned HTTP {exc.code} for {url}", status_code=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise InferenceUnavailableError(f"Inference stream to {url} failed: {exc}") from exc
