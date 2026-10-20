import pytest
from test_cloud_execution import Clock
from test_cloud_storage import MemoryBucket


def allowance(bucket=None, clock=None):
    from reconciliation.model_allowance import ModelAllowance

    return ModelAllowance(bucket or MemoryBucket(), clock=clock or Clock())


def test_reservations_are_durable_and_known_unused_calls_are_released():
    bucket = MemoryBucket()
    clock = Clock()
    first = allowance(bucket, clock)
    reservation = first.reserve("operation-one", 10)
    assert reservation["remaining"] == 50 and reservation["granted"] is True
    repeated = allowance(bucket, clock).reserve("operation-one", 10)
    assert repeated["granted"] is False
    first.finish(reservation, 4)
    clock.now += 5
    assert allowance(bucket, clock).reserve("operation-two", 4)["remaining"] == 52


def test_unknown_crash_retains_reserved_calls_and_daily_limit():
    clock = Clock()
    first = allowance(clock=clock)
    for n in range(6):
        first.reserve(f"operation-{n:02d}", 10)
        clock.now += 5
    with pytest.raises(ValueError, match="daily"):
        first.reserve("operation-extra", 4)


def test_midnight_finishes_original_day_without_touching_new_day():
    clock = Clock()
    clock.now = 86399
    first = allowance(clock=clock)
    previous = first.reserve("operation-one", 10)
    clock.now = 86410
    current = first.reserve("operation-two", 4)
    assert current["remaining"] == 56
    first.finish(previous, 2)
    clock.now += 5
    assert first.reserve("operation-three", 4)["remaining"] == 52


def test_throttle_and_bad_counts_fail_closed():
    first = allowance()
    reservation = first.reserve("operation-one", 4)
    with pytest.raises(ValueError, match="five seconds"):
        first.reserve("operation-two", 4)
    with pytest.raises(ValueError):
        first.finish(reservation, 5)
    with pytest.raises(ValueError):
        first.reserve("../outside", 4)
    with pytest.raises(ValueError):
        first.reserve("operation-zero", 0)
    with pytest.raises(ValueError):
        first.reserve("operation-false", False)


def test_concurrent_reservations_cannot_bypass_the_workspace_throttle():
    from concurrent.futures import ThreadPoolExecutor

    bucket = MemoryBucket()
    clock = Clock()

    def attempt(n):
        try:
            return allowance(bucket, clock).reserve(f"parallel-operation-{n}", 10)["granted"]
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(True) == 1


def test_daily_rollover_does_not_reset_the_shared_five_second_throttle():
    clock = Clock()
    clock.now = 86399
    first = allowance(clock=clock)
    first.reserve("midnight-operation-one", 4)
    clock.now = 86401
    with pytest.raises(ValueError, match="five seconds"):
        first.reserve("midnight-operation-two", 4)
    clock.now = 86405
    assert first.reserve("midnight-operation-two", 4)["remaining"] == 56
