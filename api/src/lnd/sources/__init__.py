"""Source-system clients.

Three sources feed the platform: the CRM, the HRIS, and a scheduled LinkedIn
Learning export. Survey and assessment feedback arrives inside the CRM payload,
so there is no Microsoft Forms integration.

Every client is read-only — there is no write client anywhere in this package,
and the credentials are issued read-only, so write-back is unavailable rather
than merely forbidden.
"""
