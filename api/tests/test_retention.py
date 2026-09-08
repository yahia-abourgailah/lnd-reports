"""Keeping the reports that were published.

The claim this module makes is narrow and worth stating: a monthly report that
was sent can still be produced afterwards, byte for byte, even though
regenerating it would give different numbers. Everything here tests that and its
edges — what gets stored, what deliberately does not, and what the listing is
allowed to load.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lnd.export import retention
from lnd.metrics.base import MetricValue, Provenance, Unit
from lnd.metrics.population import Grain, Population
from lnd.models.ops import ExportEdition, ExportKind, ExportTrigger

pytestmark = pytest.mark.usefixtures("core_db")

POP = Population(key="attendances", grain=Grain.ATTENDANCE, description="one attendance")


def _value(key: str, value: object) -> MetricValue:
    from decimal import Decimal

    return MetricValue(
        key=key,
        title=key,
        definition="",
        population=POP,
        provenance=Provenance.NEW,
        unit=Unit.COUNT,
        value=None if value is None else Decimal(str(value)),
    )


def _keep(
    session: Session,
    *,
    month: int = 8,
    digest: str = "a" * 64,
    content: bytes = b"x" * 100,
    kind: ExportKind = ExportKind.MONTHLY_XLSX,
    by: str | None = "someone@example.com",
) -> ExportEdition:
    return retention.keep(
        session,
        kind=kind,
        trigger=ExportTrigger.MANUAL if by else ExportTrigger.SCHEDULED,
        year=2026,
        month=month,
        filename=f"lnd-monthly-report-2026-{month:02d}.xlsx",
        content_type="application/vnd.ms-excel",
        filters_applied="2026-08-01 to 2026-08-31",
        content=content,
        figures_sha256=digest,
        generated_by=by,
    )


class TestTheDigestIsOfTheFiguresNotTheFile:
    def test_the_same_numbers_give_the_same_digest(self) -> None:
        one = [_value("a", 1), _value("b", 2)]
        other = [_value("b", 2), _value("a", 1)]
        assert retention.figures_digest(one) == retention.figures_digest(other), (
            "the digest must not depend on the order the registry returned them in"
        )

    def test_a_moved_number_changes_it(self) -> None:
        before = retention.figures_digest([_value("a", 1)])
        after = retention.figures_digest([_value("a", 2)])
        assert before != after

    def test_unmeasured_is_not_the_same_as_zero(self) -> None:
        """The distinction the whole metric layer exists to keep.

        If `None` and `0` digested alike, a month could go from "we never
        measured this" to "it was none" and the listing would say nothing
        happened.
        """
        assert retention.figures_digest([_value("a", None)]) != retention.figures_digest(
            [_value("a", 0)]
        )


class TestWhatIsStored:
    def test_an_edition_keeps_its_bytes(self, core_db: Session) -> None:
        edition = _keep(core_db, content=b"the workbook that was sent")
        core_db.flush()
        fetched = retention.fetch(core_db, edition.id)
        assert fetched is not None
        assert fetched.content == b"the workbook that was sent"
        assert fetched.byte_size == len(b"the workbook that was sent")

    def test_a_scheduled_edition_has_no_author(self, core_db: Session) -> None:
        """Null, never a service account.

        An author column naming the platform would make the trail say a person
        did something no person did.
        """
        edition = _keep(core_db, by=None)
        assert edition.generated_by is None
        assert edition.trigger is ExportTrigger.SCHEDULED

    def test_regenerating_unchanged_figures_stores_nothing_new(self, core_db: Session) -> None:
        """Nothing was published that was not already published.

        Without this the listing fills with identical rows and the one edition
        that did change becomes hard to find — which is the only thing the
        listing is for.
        """
        first = _keep(core_db, digest="c" * 64, content=b"one")
        again = _keep(core_db, digest="c" * 64, content=b"two")
        assert again.id == first.id
        assert again.content == b"one", "the stored file must not be replaced"

    def test_moved_figures_do_store_a_new_edition(self, core_db: Session) -> None:
        first = _keep(core_db, digest="c" * 64)
        second = _keep(core_db, digest="d" * 64)
        assert second.id != first.id

    def test_the_two_formats_of_one_month_are_separate_editions(self, core_db: Session) -> None:
        """Same figures, two files. Both were published, so both are kept."""
        workbook = _keep(core_db, kind=ExportKind.MONTHLY_XLSX, digest="e" * 64)
        printed = _keep(core_db, kind=ExportKind.MONTHLY_PDF, digest="e" * 64)
        assert printed.id != workbook.id


class TestTheCap:
    def test_only_the_newest_few_of_one_period_survive(self, core_db: Session) -> None:
        for index in range(6):
            _keep(core_db, digest=f"{index:064d}")
        core_db.flush()
        kept = core_db.scalars(select(ExportEdition).where(ExportEdition.period_month == 8)).all()
        assert len(kept) == retention.RETAIN_PER_PERIOD

    def test_a_different_month_is_untouched(self, core_db: Session) -> None:
        """Every period is kept forever; only repeats within one are capped.

        A cap across the table would delete January the moment a year passed,
        which is the opposite of what retention is for.
        """
        january = _keep(core_db, month=1, digest="f" * 64)
        for index in range(6):
            _keep(core_db, month=8, digest=f"{index:064d}")
        core_db.flush()
        assert retention.fetch(core_db, january.id) is not None


class TestTheListing:
    def test_it_does_not_load_the_files(self, core_db: Session) -> None:
        """One row per report, and the report is the content column.

        Loading it to render a filename would pull every edition in the table
        into memory to answer a question about none of them.
        """
        _keep(core_db, content=b"y" * 5000)
        core_db.flush()
        core_db.expunge_all()

        rows = retention.editions(core_db)
        assert rows
        state = rows[0].__dict__
        assert "content" not in state, "the listing loaded the file it was not asked for"

    def test_the_total_is_reported(self, core_db: Session) -> None:
        _keep(core_db, content=b"z" * 400)
        core_db.flush()
        held, weight = retention.stored_bytes(core_db)
        assert held >= 1
        assert weight >= 400
