"""Getting a finished report out of the building, on a schedule.

`mailer` sends and reports what happened; `monthly` is the job the beat
schedule runs — generate, store as an edition, then send, in that order, so a
failed send never costs the file.
"""

from lnd.delivery import mailer, monthly

__all__ = ["mailer", "monthly"]
