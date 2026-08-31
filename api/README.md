# lnd-analytics (API)

FastAPI service, Celery worker and Celery beat — one package, one image, three
commands. See the repository README for how to run it.

```
src/lnd/
  config.py          settings from environment variables only (NFR-11)
  logging.py         structured JSON logging (NFR-08)
  db.py              SQLAlchemy 2.0 engine and session
  auth/              OIDC login, signed session cookie, request dependencies
  api/v1/            versioned HTTP surface (NFR-14)
  worker/            Celery app and the beat schedule
alembic/             migrations; 0001 is the four-schema baseline
tests/
```
