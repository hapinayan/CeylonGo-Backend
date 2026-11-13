import os
import threading
import time

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    """
    Thread-safe in-memory sliding window rate limiter.
    Limits the number of requests a client IP can make within a given time window.
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
        error_message: str = "Too many requests. Please try again later.",
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.error_message = error_message
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def get_client_identifier(self, request: Request) -> str:
        """
        Extract client IP address from headers or connection.
        Prioritizes X-Forwarded-For when behind a proxy.
        """
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "127.0.0.1"

    def check(self, request: Request) -> None:
        """
        Record the request and verify if the client has exceeded the rate limit.
        Raises HTTPException(429) if limit is exceeded.
        """
        client_id = self.get_client_identifier(request)
        now = time.time()

        with self._lock:
            # Clean up stale timestamps for this client
            cutoff = now - self.window_seconds
            timestamps = self._requests.get(client_id, [])
            valid_timestamps = [t for t in timestamps if t > cutoff]

            if len(valid_timestamps) >= self.max_requests:
                # Calculate time until the oldest request in the current window expires
                oldest = valid_timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                # Store cleaned up list back
                self._requests[client_id] = valid_timestamps
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=self.error_message,
                    headers={"Retry-After": str(retry_after)},
                )

            valid_timestamps.append(now)
            self._requests[client_id] = valid_timestamps

    def reset(self) -> None:
        """Clear all stored rate limit records (primarily for testing)."""
        with self._lock:
            self._requests.clear()


def get_default_check_email_rate_limiter() -> SlidingWindowRateLimiter:
    max_requests = int(os.getenv("CHECK_EMAIL_RATE_LIMIT", "10"))
    window_seconds = int(os.getenv("CHECK_EMAIL_RATE_WINDOW_SECONDS", "60"))
    return SlidingWindowRateLimiter(
        max_requests=max_requests,
        window_seconds=window_seconds,
        error_message="Too many email check requests. Please try again later.",
    )


def get_default_forgot_password_rate_limiter() -> SlidingWindowRateLimiter:
    max_requests = int(os.getenv("FORGOT_PASSWORD_RATE_LIMIT", "5"))
    window_seconds = int(os.getenv("FORGOT_PASSWORD_RATE_WINDOW_SECONDS", "60"))
    return SlidingWindowRateLimiter(
        max_requests=max_requests,
        window_seconds=window_seconds,
        error_message="Too many password reset requests. Please try again later.",
    )


# Global instances for auth endpoints
check_email_limiter = get_default_check_email_rate_limiter()
forgot_password_limiter = get_default_forgot_password_rate_limiter()


def reset_all_limiters() -> None:
    check_email_limiter.reset()
    forgot_password_limiter.reset()
