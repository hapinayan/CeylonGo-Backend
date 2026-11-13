import time
from collections import defaultdict
from typing import Dict, List


class SlidingWindowRateLimiter:
    """In-memory sliding window rate limiter for abuse prevention."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 900):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: Dict[str, List[float]] = defaultdict(list)

    def is_rate_limited(self, key: str) -> bool:
        """Check if a key has exceeded request limits and record attempt."""
        now = time.time()
        cutoff = now - self.window_seconds
        # Clean older requests outside the current window
        valid_requests = [t for t in self._history[key] if t > cutoff]
        self._history[key] = valid_requests

        if len(valid_requests) >= self.max_requests:
            return True

        self._history[key].append(now)
        return False

    def clear(self) -> None:
        """Clear all rate limit history (useful for automated testing)."""
        self._history.clear()


# Global limiter for password reset requests: max 5 requests per 15 minutes per key
password_reset_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=900)
