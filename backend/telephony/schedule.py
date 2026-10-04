"""One reviewed calling window, never a recurring job or an automatic retry."""

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .numbers import calling_window


class CallSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["at", "next_window"]
    at: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def rules(self):
        if (self.mode == "at") != (self.at is not None):
            raise ValueError("A specific call time is required only for 'at'.")
        return self

    def resolve(self, number, now):
        if self.mode == "next_window":
            at = calling_window(number, now)["next_at"]
            if at is None:
                raise ValueError("No shared calling window could be determined.")
        else:
            instant = datetime.fromisoformat(self.at.replace("Z", "+00:00"))
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("Provide an ISO call time with an explicit timezone offset.")
            at = instant.timestamp()
            if not now <= at <= now + 7 * 86400:
                raise ValueError("Schedule a call within the next seven days.")
        window = calling_window(number, at)
        if not window["allowed"]:
            raise ValueError("The selected time is outside the destination's calling window.")
        # Do not slide to another day, retry, or spill past any possible callee's 20:00 cutoff.
        closes = [
            datetime.fromtimestamp(at, ZoneInfo(zone))
            .replace(hour=20, minute=0, second=0, microsecond=0)
            .timestamp()
            for zone in window["timezones"]
        ]
        return {
            "mode": self.mode,
            "not_before": at,
            "latest_start": min(at + 15 * 60, *closes),
            "timezones": window["timezones"],
            "at_utc": datetime.fromtimestamp(at, UTC).isoformat(),
        }
