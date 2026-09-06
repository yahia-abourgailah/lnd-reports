"""Source-system clients.

One source feeds the platform today: the CRM. Survey and assessment feedback
arrives inside the programs payload, so there is no Microsoft Forms integration,
and `get_users` returns the employee roster with company, department, sector,
position and job level, so there is no HRIS integration either. Both remain
valid values on `Source` — a LinkedIn Learning export is still out of v1 scope
for want of a feed.

Every client is read-only — there is no write client anywhere in this package,
and the credentials are issued read-only, so write-back is unavailable rather
than merely forbidden.
"""
