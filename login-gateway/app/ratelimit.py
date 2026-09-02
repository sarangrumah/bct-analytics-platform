"""Authentication rate limiting.

Two independent counters, because they stop different attacks:

* **per (database, login)** — stops password spraying against one account. Keyed on the identity
  being attacked, so rotating source IPs does not help the attacker.
* **per client address** — stops one source enumerating many accounts. Keyed on the source, so
  rotating logins does not help either.

Failures count; successes reset the account counter. In-process state is deliberate at this scale:
the gateway is a single replica behind one port, and reaching for Redis would add a dependency whose
own failure mode is "the whole login path is down". If the gateway is ever replicated this must move
to shared state, and that is called out here rather than discovered later.
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, lockout_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._attempts = {}
        self._locked_until = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list:
        recent = [t for t in self._attempts.get(key, []) if now - t < self.window]
        self._attempts[key] = recent
        return recent

    def is_locked(self, key: str, now: float | None = None) -> float:
        """Return the remaining lockout in seconds, or 0."""
        now = time.time() if now is None else now
        with self._lock:
            until = self._locked_until.get(key, 0)
            if until > now:
                return until - now
            if until:
                del self._locked_until[key]
            return 0.0

    def record_failure(self, key: str, now: float | None = None) -> float:
        now = time.time() if now is None else now
        with self._lock:
            recent = self._prune(key, now)
            recent.append(now)
            self._attempts[key] = recent
            if len(recent) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout
                self._attempts[key] = []
                return float(self.lockout)
            return 0.0

    def record_success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._locked_until.pop(key, None)
