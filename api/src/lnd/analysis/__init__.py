"""The week-7 analyses: scorecards, coverage, funnel.

Nothing in this package computes a number. Every figure it returns comes from
`lnd.metrics.registry` under narrower filters, because a scorecard that
recomputed No-show Rate would be a second definition of it — one that agrees on
the day it is written and drifts the first time a population rule is corrected
in one place and not the other. Both would keep returning plausible numbers,
and that is exactly how the workbook came to publish six wrong figures.

What these modules do instead is choose *which* metrics answer a question,
narrow the filters to the thing being looked at, and assemble the context the
metrics do not carry: a programme's sessions, a trainer's catalogue, the
free-text comments, the names behind a gap.
"""

from lnd.analysis import coverage, funnel, learners, scorecards

__all__ = ["coverage", "funnel", "learners", "scorecards"]
