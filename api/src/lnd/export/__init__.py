"""Files that leave the building.

Until now every figure has lived on a screen next to its definition and its
freshness badge. An export is the same figure with none of that context — unless
the context is written into the file, which is what `provenance` is for.

One rule holds throughout: every number is computed through
`lnd.metrics.registry`. An exporter with its own arithmetic would agree with the
dashboard on the day it was written, and the first correction to a population
rule would leave the wrong number in the attachment rather than on the screen,
where somebody might have caught it.
"""

from lnd.export import monthly, pdf, provenance, retention, tables, writers

__all__ = ["monthly", "pdf", "provenance", "retention", "tables", "writers"]
