"""Getting a finished report out of the building, on a schedule.

`mailer` sends and reports what happened; `monthly` is the job the beat
schedule runs — generate, store as an edition, then send, in that order, so a
failed send never costs the file.

`preflight` is the ten-second answer to "will the report actually go out",
asked on any day rather than discovered on the first of the month. It is
deliberately not imported here: it is a script run with `-m`, and importing it
into the package makes Python execute the module twice and say so.
"""

from lnd.delivery import mailer, monthly

__all__ = ["mailer", "monthly"]
