"""Internal inference-plane contract.

The control plane talks to inference through a narrow interface so model
serving can remain internal-only and independently scalable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Protocol


Role = Literal["system", "user", "assistant", "tool"]


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
    """vLLM client configuration for an internal OpenAI-compatible service."""

    base_url: str

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    def chat_completions(
        self, request: ChatCompletionRequest
    ) -> dict[str, object]:
        """Return the outbound request shape.

        The network call is intentionally left for the next implementation
        slice, after timeout, retry, auth, and observability policy are agreed.
        """

        return {
            "method": "POST",
            "url": self.chat_completions_url,
            "json": request.as_vllm_payload(),
        }
