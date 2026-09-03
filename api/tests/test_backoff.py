"""Retrying a call that might just be having a bad moment.

No database and no waiting: `sleep` is injected, so the delays are asserted
rather than endured.
"""

from __future__ import annotations

import pytest

from lnd.sync.backoff import RetryPolicy, honours_retryable_flag, retry


class Transient(Exception):
    """Worth another go — a timeout, a 502."""


class Permanent(Exception):
    """Not worth another go — a 401, a 404."""


class Flaky:
    """Fails `fail_times` times, then succeeds. Counts its calls."""

    def __init__(self, fail_times: int, exception: type[Exception] = Transient) -> None:
        self.fail_times = fail_times
        self.exception = exception
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exception(f"attempt {self.calls}")
        return "ok"


class TestDelays:
    def test_delays_grow_geometrically(self) -> None:
        policy = RetryPolicy(base_delay=1, multiplier=2, max_delay=100, jitter=0)

        assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [1, 2, 4, 8]

    def test_delays_are_capped(self) -> None:
        """A slow source costs a bounded slice of the sync window."""
        policy = RetryPolicy(base_delay=1, multiplier=10, max_delay=5, jitter=0)

        assert [policy.delay_for(n) for n in (1, 2, 3)] == [1, 5, 5]

    def test_jitter_spreads_the_delay_around_the_nominal_value(self) -> None:
        """Five entities that failed in the same beat tick must not retry in
        lockstep and arrive at a recovering source as one spike."""
        policy = RetryPolicy(base_delay=10, multiplier=1, max_delay=100, jitter=0.25)

        delays = {policy.delay_for(1) for _ in range(50)}

        assert len(delays) > 1, "jitter produced identical delays"
        assert all(7.5 <= delay <= 12.5 for delay in delays)

    def test_jitter_can_be_switched_off(self) -> None:
        policy = RetryPolicy(base_delay=3, multiplier=1, jitter=0)

        assert {policy.delay_for(1) for _ in range(10)} == {3}

    def test_attempts_are_one_based(self) -> None:
        with pytest.raises(ValueError, match="1-based"):
            RetryPolicy().delay_for(0)


class TestRetrying:
    def test_a_call_that_works_first_time_is_made_once(self) -> None:
        operation = Flaky(fail_times=0)

        assert retry(operation, retry_on=(Transient,), sleep=lambda _: None) == "ok"
        assert operation.calls == 1

    def test_it_recovers_within_its_budget(self) -> None:
        operation = Flaky(fail_times=2)
        policy = RetryPolicy(attempts=3, jitter=0)

        result = retry(operation, retry_on=(Transient,), policy=policy, sleep=lambda _: None)

        assert result == "ok"
        assert operation.calls == 3

    def test_it_sleeps_between_attempts_but_not_after_the_last(self) -> None:
        """Sleeping after the final failure would delay the honest error for
        nothing."""
        slept: list[float] = []
        policy = RetryPolicy(attempts=3, base_delay=1, multiplier=2, jitter=0)

        with pytest.raises(Transient):
            retry(Flaky(fail_times=99), retry_on=(Transient,), policy=policy, sleep=slept.append)

        assert slept == [1, 2]

    def test_the_original_exception_is_re_raised_not_wrapped(self) -> None:
        """`record_sync_run` stamps `error_type` from this, so it has to be the
        source's own exception and not a retry wrapper."""
        policy = RetryPolicy(attempts=2, jitter=0)

        with pytest.raises(Transient, match="attempt 2"):
            retry(
                Flaky(fail_times=99),
                retry_on=(Transient,),
                policy=policy,
                sleep=lambda _: None,
            )

    def test_an_unlisted_exception_is_not_retried(self) -> None:
        """The point of `retry_on` having no default.

        A 401 will not fix itself. Retrying it triples the failed logins, delays
        the honest error by the whole backoff budget, and can lock the account.
        """
        operation = Flaky(fail_times=99, exception=Permanent)
        slept: list[float] = []

        with pytest.raises(Permanent):
            retry(operation, retry_on=(Transient,), sleep=slept.append)

        assert operation.calls == 1
        assert slept == []

    def test_a_single_attempt_policy_never_sleeps(self) -> None:
        slept: list[float] = []

        with pytest.raises(Transient):
            retry(
                Flaky(fail_times=99),
                retry_on=(Transient,),
                policy=RetryPolicy(attempts=1),
                sleep=slept.append,
            )

        assert slept == []

    def test_the_policy_defaults_to_settings(self) -> None:
        policy = RetryPolicy.from_settings()

        assert policy.attempts == 3
        assert policy.base_delay == 1.0
        assert policy.max_delay == 30.0


class Classified(Exception):
    """One error class covering both transient and permanent faults, the shape
    `CrmError` has."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class TestPredicate:
    """Type alone is not always enough to decide."""

    def test_a_self_declared_transient_failure_is_retried(self) -> None:
        calls = {"n": 0}

        def operation() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise Classified("503", retryable=True)
            return "ok"

        result = retry(
            operation,
            retry_on=(Classified,),
            retry_if=honours_retryable_flag,
            policy=RetryPolicy(attempts=3, jitter=0),
            sleep=lambda _: None,
        )

        assert result == "ok"
        assert calls["n"] == 3

    def test_a_self_declared_permanent_failure_is_not_retried(self) -> None:
        """The case the predicate exists for.

        `retry_on=(CrmError,)` alone would repeat a 401 three times — tripling
        the failed logins, delaying the honest error by the whole backoff
        budget, and risking a locked account.
        """
        calls = {"n": 0}
        slept: list[float] = []

        def refusing() -> str:
            calls["n"] += 1
            raise Classified("401", retryable=False)

        with pytest.raises(Classified, match="401"):
            retry(
                refusing,
                retry_on=(Classified,),
                retry_if=honours_retryable_flag,
                sleep=slept.append,
            )

        assert calls["n"] == 1
        assert slept == []

    def test_an_exception_without_the_flag_is_still_retried(self) -> None:
        """Listing a type in `retry_on` is itself the claim that it is worth
        repeating, so a plain exception is not silently downgraded."""
        operation = Flaky(fail_times=1)

        result = retry(
            operation,
            retry_on=(Transient,),
            retry_if=honours_retryable_flag,
            policy=RetryPolicy(attempts=2, jitter=0),
            sleep=lambda _: None,
        )

        assert result == "ok"
        assert operation.calls == 2

    def test_it_works_against_the_real_crm_error(self) -> None:
        """Wired to Person A's client rather than a look-alike, so a change to
        how it classifies failures breaks here rather than in production."""
        from lnd.sources.crm.client import CrmError

        assert honours_retryable_flag(CrmError("500", retryable=True)) is True
        assert honours_retryable_flag(CrmError("401", retryable=False)) is False
