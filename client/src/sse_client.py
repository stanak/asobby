from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx


@dataclass
class SSEEvent:
    event: str
    data: str
    id: Optional[str] = None


class SSEClient:
    def __init__(self, client: httpx.AsyncClient, url: str, headers: Optional[dict[str, str]] = None) -> None:
        self.client = client
        self.url = url
        self.headers = headers or {}

    async def events(
        self,
        stop_event: asyncio.Event,
        *,
        min_backoff: float = 0.5,
        max_backoff: float = 10.0,
    ) -> AsyncIterator[SSEEvent]:
        backoff = min_backoff

        while not stop_event.is_set():
            try:
                async for ev in self._events_once(stop_event):
                    backoff = min_backoff
                    yield ev

            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.TimeoutException):
                if stop_event.is_set():
                    return
                jitter = random.uniform(0.0, backoff * 0.2)
                await asyncio.sleep(min(max_backoff, backoff) + jitter)
                backoff = min(max_backoff, backoff * 1.7)

            except asyncio.CancelledError:
                return

            except Exception:
                if stop_event.is_set():
                    return
                jitter = random.uniform(0.0, backoff * 0.2)
                await asyncio.sleep(min(max_backoff, backoff) + jitter)
                backoff = min(max_backoff, backoff * 1.7)

    async def _events_once(self, stop_event: asyncio.Event) -> AsyncIterator[SSEEvent]:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache", **self.headers}

        async with self.client.stream("GET", self.url, headers=headers, timeout=10.0) as r:
            r.raise_for_status()

            event_name = "message"
            data_lines: list[str] = []
            last_id: Optional[str] = None

            async for line in r.aiter_lines():
                if stop_event.is_set():
                    return

                if line == "":
                    if data_lines:
                        yield SSEEvent(event=event_name, data="\n".join(data_lines), id=last_id)
                    event_name = "message"
                    data_lines = []
                    continue

                if line.startswith(":"):
                    continue

                if line.startswith("event:"):
                    event_name = line[6:].strip()
                    continue

                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue

                if line.startswith("id:"):
                    last_id = line[3:].strip()
                    continue
