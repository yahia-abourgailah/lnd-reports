"""`received = counted + quarantined`, before every commit."""

from __future__ import annotations

import pytest

from lnd.ingest.models import Entity, Source
from lnd.transform.accounting import RunTally, TransformInvariantError


def _tally() -> RunTally:
    return RunTally(source=Source.CRM, entity=Entity.EMPLOYEE)


class TestTally:
    def test_a_balanced_run_passes_silently(self) -> None:
        tally = _tally()
        tally.received = 10
        tally.count(7)
        tally.quarantine("invalid_value", 3)

        tally.assert_balanced()

    def test_unchanged_rows_still_count(self) -> None:
        """The second run of the day changes nothing, and nothing is missing.
        An unchanged row is accounted for, so it must not read as a dropped one.
        """
        tally = _tally()
        tally.received = 5
        tally.keep_unchanged(5)

        tally.assert_balanced()
        assert tally.counted == 5
        assert tally.unchanged == 5

    def test_a_dropped_row_fails_the_run(self) -> None:
        """The failure this whole mechanism exists for. Without it the report is
        simply short — no error, no log line, nothing on screen distinguishing
        "42 attendances" from "42 of the 47 we were given"."""
        tally = _tally()
        tally.received = 10
        tally.count(9)

        with pytest.raises(TransformInvariantError, match=r"1 row\(s\) were dropped"):
            tally.assert_balanced()

    def test_counting_a_row_twice_also_fails(self) -> None:
        """Double-counting is as wrong as dropping, and much harder to see: the
        total goes up, which looks like the pipeline working."""
        tally = _tally()
        tally.received = 10
        tally.count(11)

        with pytest.raises(TransformInvariantError, match=r"1 row\(s\) were counted twice"):
            tally.assert_balanced()

    def test_the_message_names_the_entity_and_both_terms(self) -> None:
        """It is read at 3am from a log line, not from a debugger."""
        tally = _tally()
        tally.received = 100
        tally.count(80)
        tally.quarantine("unknown_employee", 5)

        with pytest.raises(TransformInvariantError) as raised:
            tally.assert_balanced()

        message = str(raised.value)
        assert "crm/employee" in message
        assert "counted 80" in message
        assert "quarantined 5" in message

    def test_reasons_are_broken_out_for_the_log(self) -> None:
        tally = _tally()
        tally.received = 4
        tally.count(1)
        tally.quarantine("invalid_value", 2)
        tally.quarantine("duplicate")

        fields = tally.as_log_fields()
        assert fields["quarantined_invalid_value"] == 2
        assert fields["quarantined_duplicate"] == 1
        assert fields["received"] == 4
