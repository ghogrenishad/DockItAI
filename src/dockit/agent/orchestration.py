"""Constrained chat orchestration over typed workflow results only."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib.request import Request, urlopen

import redis


class ConversationUnavailable(RuntimeError):
    """Raised when Redis or the configured model cannot provide a safe response."""


@dataclass(frozen=True)
class ConversationState:
    driver_id: str
    thread_id: str
    shipment_id: str | None = None
    awaiting: str | None = None


class ConversationStore(Protocol):
    def load(self, driver_id: str, thread_id: str) -> ConversationState | None: ...
    def save(self, state: ConversationState) -> None: ...


class RedisConversationStore:
    """TTL-bound state keyed by authenticated driver and thread, never capacity state."""

    def __init__(self, client: redis.Redis, *, ttl_seconds: int = 1_200) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds

    def load(self, driver_id: str, thread_id: str) -> ConversationState | None:
        value = self._client.get(self._key(driver_id, thread_id))
        if value is None:
            return None
        payload = json.loads(value)
        return ConversationState(**payload)

    def save(self, state: ConversationState) -> None:
        self._client.setex(
            self._key(state.driver_id, state.thread_id),
            self._ttl_seconds,
            json.dumps(asdict(state)),
        )

    @staticmethod
    def _key(driver_id: str, thread_id: str) -> str:
        return f"dockit:conversation:{driver_id}:{thread_id}"


class ChatModel(Protocol):
    def respond(
        self, *, system_prompt: str, user_message: str, workflow_result: dict[str, object]
    ) -> str: ...


class OpenRouterChatModel:
    """Small OpenRouter adapter for an AgentCore runtime with secret-injected API key."""

    def __init__(self, *, api_key: str, model: str = "openai/gpt-4o") -> None:
        self._api_key = api_key
        self._model = model

    def respond(
        self, *, system_prompt: str, user_message: str, workflow_result: dict[str, object]
    ) -> str:
        payload = json.dumps(
            {
                "model": self._model,
                "max_tokens": 300,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                    {"role": "tool", "content": json.dumps(workflow_result)},
                ],
            }
        ).encode()
        request = Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:  # noqa: S310
                body = json.load(response)
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, OSError, ValueError) as error:
            raise ConversationUnavailable("chat service is temporarily unavailable") from error


class ConversationOrchestrator:
    """Bind model conversation to typed results; no SQL, scheduler, or booking tools are exposed."""

    system_prompt = (
        "You explain only the supplied typed workflow result. Never invent availability, "
        "claim a booking is confirmed unless result says so, bypass authorization, or output SQL. "
        "Ask one concise clarification when the result says more information is required."
    )

    def __init__(self, store: ConversationStore, model: ChatModel) -> None:
        self._store = store
        self._model = model

    def reply(
        self,
        *,
        driver_id: str,
        thread_id: str,
        message: str,
        workflow_result: dict[str, object],
    ) -> str:
        try:
            state = self._store.load(driver_id, thread_id) or ConversationState(
                driver_id, thread_id
            )
            response = self._model.respond(
                system_prompt=self.system_prompt,
                user_message=message,
                workflow_result=workflow_result,
            )
            self._store.save(state)
            return response
        except (ConversationUnavailable, redis.RedisError) as error:
            raise ConversationUnavailable(
                "chat is temporarily unavailable; please try again"
            ) from error
