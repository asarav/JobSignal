"""Per-plugin isolation: timeouts, exception capture, and circuit breakers.

The shim wraps every plugin call. It is the *only* place timeouts and circuit
breakers live, so plugins cannot bypass them.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.domain.models.common import PortError

if TYPE_CHECKING:
    from app.domain.ports.logging import StructuredLogger


class CircuitState:
    """Stable circuit-breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-plugin circuit breaker.

    Opens after ``consecutive_failures`` consecutive failures or when the
    failure rate over the last ``window_calls`` calls reaches
    ``failure_rate_threshold``.  Transitions to half-open after ``cooldown``;
    a successful probe closes it again.
    """

    def __init__(
        self,
        *,
        consecutive_failures: int = 5,
        window_calls: int = 20,
        failure_rate_threshold: float = 0.5,
        cooldown: timedelta | None = None,
        max_cooldown: timedelta | None = None,
    ) -> None:
        self._consecutive_failures = consecutive_failures
        self._window_calls = window_calls
        self._failure_rate_threshold = failure_rate_threshold
        self._cooldown = cooldown if cooldown is not None else timedelta(minutes=10)
        self._max_cooldown = max_cooldown or timedelta(hours=6)
        self._state = CircuitState.CLOSED
        self._consecutive = 0
        self._window: deque[bool] = deque(maxlen=window_calls)
        self._opened_at: datetime | None = None
        self._current_cooldown = self._cooldown

    @property
    def state(self) -> str:
        return self._state

    @property
    def opened_at(self) -> datetime | None:
        return self._opened_at

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def record_success(self) -> None:
        self._consecutive = 0
        self._window.append(True)
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._opened_at = None
            self._current_cooldown = self._cooldown

    def record_failure(self) -> None:
        self._consecutive += 1
        self._window.append(False)
        if self._consecutive >= self._consecutive_failures:
            self._open()
            return
        if len(self._window) >= self._window_calls:
            rate = sum(1 for ok in self._window if not ok) / len(self._window)
            if rate >= self._failure_rate_threshold:
                self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._now()

    def allow(self) -> bool:
        """Return ``True`` when a call may proceed."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if self._opened_at is None:
                self._opened_at = self._now()
            elapsed = self._now() - self._opened_at
            if elapsed >= self._current_cooldown:
                self._state = CircuitState.HALF_OPEN
                self._current_cooldown = min(self._current_cooldown * 2, self._max_cooldown)
                return True
            return False
        # half-open: allow a single probe
        return True

    def to_health(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "consecutive_failures": self._consecutive,
            "window_failures": sum(1 for ok in self._window if not ok),
            "window_size": len(self._window),
            "opened_at": self._opened_at.isoformat() if self._opened_at else None,
            "cooldown_s": self._current_cooldown.total_seconds(),
        }

    @classmethod
    def from_health(cls, health: dict[str, Any]) -> CircuitBreaker:
        breaker = cls()
        state = health.get("state", CircuitState.CLOSED)
        breaker._state = state
        breaker._consecutive = int(health.get("consecutive_failures", 0))
        opened_at = health.get("opened_at")
        if opened_at:
            breaker._opened_at = datetime.fromisoformat(opened_at)
        cooldown_s = health.get("cooldown_s")
        if cooldown_s:
            breaker._current_cooldown = timedelta(seconds=float(cooldown_s))
        return breaker


class IsolationShim:
    """Wraps a plugin callable with timeout, exception capture, and a breaker."""

    def __init__(
        self,
        breaker: CircuitBreaker,
        *,
        timeout_s: float = 30.0,
        logger: StructuredLogger | None = None,
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._breaker = breaker
        self._timeout_s = timeout_s
        self._logger = logger
        self._on_change = on_change

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change(self._breaker.to_health())

    async def call(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        if not self._breaker.allow():
            raise PortError(
                code="plugin.circuit_open",
                message="circuit breaker open for plugin",
                retryable=False,
                details={"state": self._breaker.state},
            )
        try:
            result = await asyncio.wait_for(fn(*args, **kwargs), timeout=self._timeout_s)
        except TimeoutError as exc:
            self._breaker.record_failure()
            self._changed()
            if self._logger is not None:
                self._logger.warning(
                    "plugin.timeout",
                    plugin_id=kwargs.get("plugin_id"),
                    timeout_s=self._timeout_s,
                    error_code="plugin.timeout",
                )
            raise PortError(
                code="plugin.timeout",
                message=f"plugin call timed out after {self._timeout_s}s",
                retryable=True,
                details={"timeout_s": self._timeout_s},
            ) from exc
        except PortError:
            self._breaker.record_failure()
            self._changed()
            raise
        except Exception as exc:  # noqa: BLE001 - isolation shim must capture all
            self._breaker.record_failure()
            self._changed()
            if self._logger is not None:
                self._logger.warning(
                    "plugin.exception",
                    plugin_id=kwargs.get("plugin_id"),
                    error_code="plugin.exception",
                    error=str(exc),
                )
            raise PortError(
                code="plugin.exception",
                message=f"plugin raised {type(exc).__name__}: {exc}",
                retryable=False,
                details={"exception_type": type(exc).__name__},
            ) from exc
        self._breaker.record_success()
        self._changed()
        return result