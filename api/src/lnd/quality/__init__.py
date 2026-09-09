"""Data quality: what the platform could not place, and what to do about it.

`catalogue` is the one description of each rule — disposition, meaning, cost and
suggested resolution — read by the transform that raises exceptions, the API
that serves the console, and the tests that assert the two agree.

`completeness` answers "how much of this period is actually in the figures",
which is the question FR-F04 asks and the one a monthly report has to be able to
answer about itself.
"""

from lnd.quality import catalogue, completeness

__all__ = ["catalogue", "completeness"]
