"""The PDF pack.

A layout module is mostly untestable by assertion — whether a heading sits well
above its table is a thing you look at. Two properties are not, and they are the
ones that would actually hurt:

  * every figure in a pack comes from the registry, so a pack cannot hold a
    number the dashboard would not show; and
  * a metric with no value renders as words rather than as zero. `Blended
    Learner Hours` reads 0.0 in a printed report and somebody concludes the
    blended figure is zero, when what happened is that LinkedIn was never
    ingested.

The rest of what is here is a smoke test with teeth: each pack renders, for
every entity it can be asked about, and produces a structurally valid PDF. That
catches the failure this module is actually prone to — a text run that cannot be
encoded, a table with mismatched column widths — which raises rather than
producing a wrong-looking page.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.orm import Session

from lnd.analysis import scorecards
from lnd.export import pdf
from lnd.metrics import registry
from lnd.metrics.base import MetricValue, Provenance, Unit
from lnd.metrics.filters import MetricFilters
from lnd.metrics.population import Grain, Population

pytestmark = pytest.mark.usefixtures("core_db")

FEBRUARY = MetricFilters(date_from=dt.date(2026, 2, 1), date_to=dt.date(2026, 2, 28))


def _is_pdf(content: bytes) -> None:
    assert content.startswith(b"%PDF-"), "not a PDF"
    assert content.rstrip().endswith(b"%%EOF"), "PDF is truncated"
    assert len(content) > 5_000, "suspiciously small for a multi-page pack"


class TestAPackIsAValidPdf:
    def test_the_figures_pack_renders(self, loaded: Session) -> None:
        _is_pdf(pdf.kpi_pack(loaded, FEBRUARY))

    def test_every_programme_renders(self, loaded: Session) -> None:
        """Every one, not a chosen one.

        The programme that breaks this is the one with no trainer, no capacity
        or no comments — exactly the rows a hand-picked example avoids.
        """
        for row in scorecards.program_index(loaded):
            _is_pdf(pdf.program_scorecard_pack(loaded, int(row["crm_program_id"])))

    def test_every_trainer_renders(self, loaded: Session) -> None:
        for row in scorecards.trainer_index(loaded):
            _is_pdf(pdf.trainer_scorecard_pack(loaded, int(row["trainer_key"])))

    def test_the_monthly_pack_renders(self, loaded: Session) -> None:
        _is_pdf(pdf.monthly_pack(loaded, 2026, 2, MetricFilters()))

    def test_an_unknown_programme_is_refused(self, loaded: Session) -> None:
        with pytest.raises(scorecards.UnknownProgram):
            pdf.program_scorecard_pack(loaded, 999_999)


class TestNothingHereComputesAFigure:
    def test_the_monthly_pack_holds_the_registrys_numbers(self, loaded: Session) -> None:
        """The pack and the workbook lay out one list, not two.

        `monthly.headline` is that list. If the PDF built its own, a metric
        added to the report would appear in one format and not the other, and
        the two files would be different reports under one name.
        """
        from lnd.export import monthly

        window = monthly.month_window(2026, 2)
        computed = monthly.headline(loaded, window)
        for key, value in computed.items():
            assert value.value == registry.compute(key, loaded, window).value


class TestAnUnmeasuredFigureSaysSo:
    def test_no_value_is_words_not_a_zero(self) -> None:
        """The distinction the whole registry is built to preserve.

        A metric awaiting the LinkedIn export has not been measured. Rendering
        it as 0.0 in a printed report — where there is no tooltip to correct it
        — turns "we do not know" into "it is none", which is the workbook's
        habit rather than this platform's.
        """
        missing = MetricValue(
            key="linkedin_hours",
            title="LinkedIn Hours",
            definition="Hours of LinkedIn Learning content completed.",
            population=Population(
                key="attendances",
                grain=Grain.ATTENDANCE,
                description="one person attending one session",
            ),
            provenance=Provenance.NEW,
            unit=Unit.HOURS,
            value=None,
            sample_size=0,
        )
        assert pdf._sample_line(missing) == "not measured"

    def test_a_ratio_states_both_halves(self, loaded: Session) -> None:
        """A percentage without its denominator is half a claim.

        98% over 297 responses and 98% over 4 are different statements, and a
        printed page is where the sample cannot be hovered for.
        """
        rate = registry.compute("survey_response_rate", loaded, FEBRUARY)
        line = pdf._sample_line(rate)
        assert str(int(rate.numerator or 0)) in line
        assert str(int(rate.denominator or 0)) in line
