"""
Tests for Failure Classification, Retryability, and Rate Limiting.
"""
import time
from auto_video_factory.flow_provider.models import FlowFailureClass
from auto_video_factory.flow_provider.queue import RetryPolicy, RateLimiter


def test_retryability_rules():
    policy = RetryPolicy(max_retries=3, base_delay_s=0.01)
    
    # Retryable classes
    assert policy.is_retryable(FlowFailureClass.NETWORK) is True
    assert policy.is_retryable(FlowFailureClass.RATE_LIMIT) is True
    assert policy.is_retryable(FlowFailureClass.TIMEOUT) is True
    
    # Non-retryable classes (must fail fast or stop immediately)
    assert policy.is_retryable(FlowFailureClass.AUTH) is False
    assert policy.is_retryable(FlowFailureClass.SESSION_EXPIRED) is False
    assert policy.is_retryable(FlowFailureClass.USER_INTERACTION_REQUIRED) is False
    assert policy.is_retryable(FlowFailureClass.QUOTA) is False
    assert policy.is_retryable(FlowFailureClass.SELECTOR_CHANGED) is False
    assert policy.is_retryable(FlowFailureClass.UI_CHANGED) is False
    assert policy.is_retryable(FlowFailureClass.MODEL_UNAVAILABLE) is False
    assert policy.is_retryable(FlowFailureClass.DOWNLOAD_FAILED) is False
    assert policy.is_retryable(FlowFailureClass.UNKNOWN) is False


def test_retry_backoff_calculation():
    policy = RetryPolicy(max_retries=4, base_delay_s=1.0, max_delay_s=10.0)
    delay1 = policy.get_delay(attempt=1)
    delay2 = policy.get_delay(attempt=2)
    delay3 = policy.get_delay(attempt=3)
    
    assert 1.0 <= delay1 <= 2.0
    assert 2.0 <= delay2 <= 4.0
    assert 4.0 <= delay3 <= 8.0
    
    # Exceeded max retries
    assert policy.should_retry(FlowFailureClass.NETWORK, attempt=4) is False
    assert policy.should_retry(FlowFailureClass.NETWORK, attempt=3) is True


def test_rate_limiter_permits_and_delays():
    # Allow 2 ops per 0.1 second
    limiter = RateLimiter(max_operations=2, window_seconds=0.1)
    
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    assert limiter.acquire() is False  # rate limited!
    
    # Wait for window to slide
    time.sleep(0.12)
    assert limiter.acquire() is True
