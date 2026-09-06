"""The participation-rate denominator, and the two labels it must carry.

P-01 was a hardcoded 192 against an HR roster of 212. P-13 was worse: that 212
covered one company while the 128 attendees spanned several, so the published
rate divided two different populations by each other. This module is the
replacement, and most of what is tested here is not the arithmetic — it is
whether the number arrives with enough context that nobody can repeat either
defect by accident.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lnd.ingest.landing import land
from lnd.ingest.models import Entity, Source
from lnd.models.core import DimEmployee
from lnd.transform.employees import (
    HISTORY_EPOCH,
    as_of,
    headcount_as_of,
    known_entities,
)
from lnd.transform.runner import transform_programs
from tests.fixtures import crm_program

pytestmark = pytest.mark.usefixtures("core_db")

BEFORE_THE_PLATFORM = datetime(2025, 10, 1, tzinfo=UTC)


def land_program(session: Session, payload: dict[str, Any]) -> None:
    land(
        session,
        source=Source.CRM,
        entity=Entity.PROGRAM,
        records=[(str(payload["id"]), payload)],
    )


def moved_sector(sector: str, *, odoo_id: str = "4821") -> dict[str, Any]:
    """The fixture with one person's sector changed, which opens a version."""
    payload = crm_program.program()
    for entry in payload["users"]:
        if entry["user_odoo_id"] == odoo_id:
            entry["user"] = dict(entry["user"], sector=sector)
            moved_user = entry["user"]
            break
    for session_row in payload["sessions"]:
        for row in session_row["attendance"]:
            if row["user_odoo_id"] == odoo_id:
                row["user"] = moved_user
    return payload


# ---------------------------------------------------------------------------
# backdating, and the bug it fixes
# ---------------------------------------------------------------------------
class TestFirstVersionIsBackdated:
    def test_history_resolves_at_all(self, core_db: Session) -> None:
        """The defect `is_estimated` was introduced to make visible.

        With `valid_from` at observation time, every as-of lookup before the
        platform's first sync returned nothing — which is every session the CRM
        holds. A dimension that cannot answer for any date in the data is not a
        slowly changing dimension, it is a snapshot.
        """
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        assert as_of(core_db, odoo_id="4821", moment=BEFORE_THE_PLATFORM) is not None

    def test_the_backdated_version_says_it_is_estimated(self, core_db: Session) -> None:
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        version = as_of(core_db, odoo_id="4821", moment=BEFORE_THE_PLATFORM)
        assert version is not None
        assert version.is_estimated is True
        assert version.valid_from == HISTORY_EPOCH
        assert version.observed_from > HISTORY_EPOCH

    def test_a_later_version_is_not_backdated(self, core_db: Session) -> None:
        """We were there for the change, so nothing is being assumed."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)
        land_program(core_db, moved_sector("Residential"))
        transform_programs(core_db)

        versions = (
            core_db.execute(
                select(DimEmployee)
                .where(DimEmployee.odoo_id == "4821")
                .order_by(DimEmployee.valid_from)
            )
            .scalars()
            .all()
        )
        assert [v.is_estimated for v in versions] == [True, False]
        assert versions[1].valid_from == versions[1].observed_from

    def test_history_reads_the_old_version_not_the_new_one(self, core_db: Session) -> None:
        """The whole point of versioning: a past month must not move when
        somebody transfers today."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)
        land_program(core_db, moved_sector("Residential"))
        transform_programs(core_db)

        past = as_of(core_db, odoo_id="4821", moment=BEFORE_THE_PLATFORM)
        present = as_of(core_db, odoo_id="4821", moment=datetime.now(UTC))

        assert past is not None and past.sector == "Commercial"
        assert present is not None and present.sector == "Residential"

    def test_the_flag_cannot_drift_from_the_backdating(self, core_db: Session) -> None:
        """A total CHECK, so nothing can be backdated without being labelled."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        mislabelled = core_db.scalar(
            select(func.count())
            .select_from(DimEmployee)
            .where(DimEmployee.is_estimated != (DimEmployee.valid_from < DimEmployee.observed_from))
        )
        assert mislabelled == 0


# ---------------------------------------------------------------------------
# the count
# ---------------------------------------------------------------------------
class TestHeadcount:
    def test_it_counts_people_current_at_the_moment(self, core_db: Session) -> None:
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        assert headcount_as_of(core_db, moment=datetime.now(UTC)).count == 2

    def test_a_versioned_person_is_counted_once_not_twice(self, core_db: Session) -> None:
        """Distinct on `odoo_id`, because two rows for one person is exactly
        how a denominator quietly inflates and a rate quietly falls."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)
        land_program(core_db, moved_sector("Residential"))
        transform_programs(core_db)

        assert core_db.scalar(select(func.count()).select_from(DimEmployee)) == 3
        assert headcount_as_of(core_db, moment=datetime.now(UTC)).count == 2

    def test_a_date_before_anyone_existed_counts_nobody(self, core_db: Session) -> None:
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        earlier = HISTORY_EPOCH - timedelta(days=1)
        assert headcount_as_of(core_db, moment=earlier).count == 0

    def test_inactive_people_are_excluded_by_default(self, core_db: Session) -> None:
        """A leaver is not part of the population that could have attended."""
        payload = crm_program.program()
        payload["users"][1]["user"] = dict(payload["users"][1]["user"], status="inactive")
        land_program(core_db, payload)
        transform_programs(core_db)

        now = datetime.now(UTC)
        assert headcount_as_of(core_db, moment=now).count == 1
        assert headcount_as_of(core_db, moment=now, active_only=False).count == 2


# ---------------------------------------------------------------------------
# P-13: the denominator must name its population
# ---------------------------------------------------------------------------
class TestEntitySet:
    def test_an_entity_set_scopes_the_count(self, core_db: Session) -> None:
        """The fix for P-13. The workbook divided attendees drawn from several
        companies by the employees of one."""
        payload = crm_program.program()
        payload["users"][1]["user"] = dict(
            payload["users"][1]["user"],
            company={"id": 2, "odoo_id": "2", "name": "The Address Developments"},
        )
        land_program(core_db, payload)
        transform_programs(core_db)

        now = datetime.now(UTC)
        assert headcount_as_of(core_db, moment=now).count == 2
        assert (
            headcount_as_of(core_db, moment=now, entity_set=["The Address Investments"]).count == 1
        )

    def test_the_result_records_which_population_it_counted(self, core_db: Session) -> None:
        """So an export states the population it divided by, rather than
        leaving a reader to assume it was the whole company."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        scoped = headcount_as_of(
            core_db, moment=datetime.now(UTC), entity_set=["The Address Investments"]
        )
        unscoped = headcount_as_of(core_db, moment=datetime.now(UTC))

        assert scoped.entity_set == ("The Address Investments",)
        assert unscoped.entity_set is None

    def test_known_entities_makes_q15_concrete(self, core_db: Session) -> None:
        """Live data shows attendees spanning five companies. This is the query
        that shows somebody the five."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        assert known_entities(core_db) == ["The Address Investments"]


# ---------------------------------------------------------------------------
# the two labels
# ---------------------------------------------------------------------------
class TestLabels:
    def test_a_historical_date_is_labelled_estimated(self, core_db: Session) -> None:
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        assert headcount_as_of(core_db, moment=BEFORE_THE_PLATFORM).is_estimated is True

    def test_today_is_not_estimated(self, core_db: Session) -> None:
        """Everyone counted has been observed by now, so nothing is assumed."""
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        after = datetime.now(UTC) + timedelta(minutes=1)
        assert headcount_as_of(core_db, moment=after).is_estimated is False

    def test_it_never_claims_to_cover_the_enrollable_population(self, core_db: Session) -> None:
        """The guard against repeating P-01 with better provenance.

        `dim_employee` holds people who touched training and nobody else, so
        dividing participants by this yields very nearly 100% — wrong in the
        flattering direction. It stays false until Q-15 is answered.
        """
        land_program(core_db, crm_program.program())
        transform_programs(core_db)

        assert (
            headcount_as_of(
                core_db, moment=datetime.now(UTC), entity_set=["The Address Investments"]
            ).covers_enrollable_population
            is False
        )
