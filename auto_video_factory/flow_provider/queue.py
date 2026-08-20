"""
Queue primitives: JobQueue (priority FIFO), RateLimiter (sliding window), and RetryPolicy.
"""
from __future__ import annotations

import collections
import heapq
import random
import threading
import time
from typing import Optional

from .models import FlowFailureClass, FlowGenerationRequest


class JobQueue:
    """
    Thread-safe Priority Queue with FIFO ordering for items of identical priority.
    Higher numerical priority is dequeued first.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counter = 0  # Monotonic counter for FIFO stability
        self._heap: list[tuple[int, int, FlowGenerationRequest]] = []

    def push(self, request: FlowGenerationRequest):
        with self._lock:
            # We negate priority because heapq is a min-heap
            # Order: (-priority, counter, request)
            entry = (-request.priority, self._counter, request)
            self._counter += 1
            heapq.heappush(self._heap, entry)

    def pop(self) -> FlowGenerationRequest:
        with self._lock:
            if not self._heap:
                raise IndexError("Cannot pop from an empty JobQueue")
            _, _, request = heapq.heappop(self._heap)
            return request

    def peek(self) -> Optional[FlowGenerationRequest]:
        with self._lock:
            if not self._heap:
                return None
            return self._heap[0][2]

    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._heap) == 0


class RateLimiter:
    """
    Sliding window rate limiter to prevent flooding the provider.
    """

    def __init__(self, max_operations: int = 10, window_seconds: float = 60.0):
        self.max_operations = max_operations
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._timestamps: collections.deque[float] = collections.deque()

    def acquire(self) -> bool:
        """Attempt to acquire a permit. Returns True if granted, False if rate limited."""
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            # Purge expired timestamps
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) < self.max_operations:
                self._timestamps.append(now)
                return True
            return False

    def wait_and_acquire(self, timeout_s: float = 30.0) -> bool:
        """Wait until a rate limit slot becomes available or timeout expires."""
        start = time.time()
        while time.time() - start < timeout_s:
            if self.acquire():
                return True
            time.sleep(0.05)
        return False


class RetryPolicy:
    """
    Deterministic retry policy with exponential backoff and jitter.
    Strictly forbids blind retry loops on non-retryable failure classes.
    """

    RETRYABLE_CLASSES = frozenset({
        FlowFailureClass.NETWORK,
        FlowFailureClass.RATE_LIMIT,
        FlowFailureClass.TIMEOUT,
    })

    def __init__(
        self,
        max_retries: int = 3,
        base_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
        jitter_ratio: float = 0.2,
    ):
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.jitter_ratio = jitter_ratio

    def is_retryable(self, failure_class: Optional[FlowFailureClass]) -> bool:
        if failure_class is None:
            return False
        return failure_class in self.RETRYABLE_CLASSES

    def should_retry(self, failure_class: Optional[FlowFailureClass], attempt: int) -> bool:
        if attempt >= self.max_retries:
            return False
        return self.is_retryable(failure_class)

    def get_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with bounded jitter."""
        factor = 2 ** max(0, attempt - 1)
        delay = min(self.base_delay_s * factor, self.max_delay_s)
        jitter = delay * self.jitter_ratio * random.uniform(0.0, 1.0)
        return delay + jitter
