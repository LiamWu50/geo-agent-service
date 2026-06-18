from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx


class ChatModelClient(Protocol):
    async def stream_response(
        self,
        *,
        messages: list[dict[str, str]],
        tool_results: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream assistant text chunks."""
        yield ""


class QwenPlusClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model_name: str,
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    async def stream_response(
        self,
        *,
        messages: list[dict[str, str]],
        tool_results: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        if not self.api_key:
            yield "AI model is not configured. Please set QWEN_API_KEY."
            return

        payload_messages = [
            {
                "role": "system",
                "content": (
                    "You are a WebGIS assistant. Use the provided backend tool results as "
                    "ground truth, answer concisely, and mention uncertainty when data is missing."
                ),
            },
            *messages,
        ]
        if tool_results:
            payload_messages.append(
                {
                    "role": "system",
                    "content": f"Backend tool results: {tool_results}",
                }
            )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model_name,
                    "messages": payload_messages,
                    "stream": True,
                    "max_tokens": self.max_output_tokens,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line.removeprefix("data: ").strip()
                    if data == "[DONE]":
                        break
                    chunk = self._content_from_chunk(data)
                    if chunk:
                        yield chunk

    def _content_from_chunk(self, data: str) -> str:
        try:
            payload = httpx.Response(200, content=data).json()
        except ValueError:
            return ""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        delta = choices[0].get("delta")
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        return content if isinstance(content, str) else ""
