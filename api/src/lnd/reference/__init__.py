"""The frozen reference dataset, and the machinery that keeps it honest.

    anonymise.py   real payload in, same defects out, no real people
    freeze.py      build it from a live `raw` layer; `--check` verifies it

The dataset itself lives at `tests/reference/dataset.json.gz`, beside the
golden suite that reads it. Together they are week 4's gate: a change that
moves a published figure fails the build, and the only way past is to update
the golden file in the same pull request, where a reviewer has to look at the
number that moved and agree with it.
"""

from __future__ import annotations
