"""Scoring: rescaling, and the NPS bands that make P-03 unavailable."""

from __future__ import annotations

import pytest

from lnd.models.core import NpsBand
from lnd.transform.programs import band_for, rescale


class TestRescale:
    def test_a_five_of_five_is_a_ten_of_ten(self) -> None:
        assert rescale(5, source=(1, 5), target=(0, 10)) == 10

    def test_a_one_of_five_is_a_zero_of_ten(self) -> None:
        assert rescale(1, source=(1, 5), target=(0, 10)) == 0

    def test_the_midpoint_maps_to_the_midpoint(self) -> None:
        assert rescale(3, source=(1, 5), target=(0, 10)) == 5

    def test_an_identity_rescale_changes_nothing(self) -> None:
        for score in range(1, 6):
            assert rescale(score, source=(1, 5), target=(1, 5)) == score

    def test_rounding_is_half_up(self) -> None:
        """A stated choice, so a one-respondent reconciliation difference has a
        documented cause rather than being tracked down twice."""
        assert rescale(2, source=(0, 3), target=(1, 5)) == 4  # 3.667 -> 4

    def test_a_degenerate_scale_is_rejected(self) -> None:
        """A mapping claiming min == max is a mis-authored row, not a divisor."""
        with pytest.raises(ValueError, match="degenerate"):
            rescale(3, source=(5, 5), target=(0, 10))


class TestNpsBands:
    @pytest.mark.parametrize("score", [9, 10])
    def test_promoters(self, score: int) -> None:
        assert band_for(score) is NpsBand.PROMOTER

    @pytest.mark.parametrize("score", [7, 8])
    def test_passives(self, score: int) -> None:
        assert band_for(score) is NpsBand.PASSIVE

    @pytest.mark.parametrize("score", [0, 3, 6])
    def test_detractors(self, score: int) -> None:
        assert band_for(score) is NpsBand.DETRACTOR

    def test_only_a_top_score_promotes_on_a_five_point_scale(self) -> None:
        """The consequence of rescaling, stated out loud.

        A 4 out of 5 rescales to 7.5 -> 8, which is a passive. That is the
        conventional treatment and it is why NPS from a 1-5 survey reads lower
        than people expect — a fact that belongs in the week-4 reconciliation
        rather than in a surprised email after launch.
        """
        assert band_for(rescale(5, source=(1, 5), target=(0, 10))) is NpsBand.PROMOTER
        assert band_for(rescale(4, source=(1, 5), target=(0, 10))) is NpsBand.PASSIVE
        assert band_for(rescale(3, source=(1, 5), target=(0, 10))) is NpsBand.DETRACTOR
