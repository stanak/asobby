"""A local game ID survives KO corrections, retries and transient scene flicker."""
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class BattleIdentity:
    started_monotonic: float
    client_id: str = field(default_factory=lambda: uuid4().hex)
    match_id: str = ""
    duration_sec: float | None = None

    def result_fields(self, now: float) -> dict:
        if self.duration_sec is None:
            self.duration_sec = max(0.0, now - self.started_monotonic)
        return {"report_version": 2, "client_id": self.client_id,
                "match_id": self.match_id, "duration_sec": self.duration_sec}
