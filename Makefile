.DEFAULT_GOAL := help
SHELL := /bin/bash

DEV     := -f compose.yaml -f compose.dev.yaml
STAGING := -f compose.yaml -f compose.staging.yaml
PROD    := -f compose.yaml -f compose.prod.yaml

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------- local dev
.PHONY: init
init: .env ## First-time setup: create .env from the template
	@echo "  .env is ready. Fill in CRM_BASE_URL, CRM_SERVICE_KEY and OIDC_* as they arrive."

# .env is a real file target, so it is created once and never overwritten —
# your CRM key and generated secrets survive every later `make up`.
.env: .env.example
	@test -f $@ || { \
	  cp $< $@ && \
	  python3 -c "import pathlib, re, secrets; \
p = pathlib.Path('.env'); t = p.read_text(); \
t = t.replace('generate-a-real-one-with-secrets-token-urlsafe-48', secrets.token_urlsafe(48)); \
t = re.sub(r'^(POSTGRES_PASSWORD|APP_DB_PASSWORD)=.*\$$', lambda m: m.group(1) + '=' + secrets.token_urlsafe(24), t, flags=re.M); \
p.write_text(t)" && \
	  echo '  created .env — SESSION_SECRET and both database passwords generated'; \
	}
	@touch $@

# .env is never overwritten once it exists — that is what protects your CRM key
# and the generated passwords — so it drifts from the template every time a
# setting is added. Compose supplies a default for each, so drift is a warning
# and not an error, but it should be visible rather than discovered later.
.PHONY: env-check
env-check: .env ## Report settings the template has and your .env does not
	@missing=$$(comm -13 <(grep -oE '^[A-Z][A-Z0-9_]*=' .env | sort) \
	                     <(grep -oE '^[A-Z][A-Z0-9_]*=' .env.example | sort) | tr -d '='); \
	 if [ -n "$$missing" ]; then \
	   echo "  .env is missing settings the template has — compose defaults apply:"; \
	   echo "$$missing" | sed 's/^/    /'; \
	   echo "  copy them across from .env.example if you need to change them."; \
	 fi

.PHONY: up
up: .env ## Start the full stack in dev (http://localhost:8080)
	@$(MAKE) --no-print-directory env-check
	docker compose $(DEV) up --build -d
	@$(MAKE) --no-print-directory migrate
	@echo "→ http://localhost:8080   ·   pgAdmin http://127.0.0.1:8082   ·   API docs /v1/docs"

.PHONY: down
down: ## Stop the dev stack
	docker compose $(DEV) down

.PHONY: nuke
nuke: ## Stop the dev stack and delete its volumes
	docker compose $(DEV) down -v

.PHONY: logs
logs: ## Tail logs (make logs S=api)
	docker compose $(DEV) logs -f $(S)

.PHONY: ps
ps: ## Show service status
	docker compose $(DEV) ps

.PHONY: shell
shell: ## Shell into the api container
	docker compose $(DEV) exec api /bin/bash

.PHONY: psql
psql: ## psql as the owner role
	docker compose $(DEV) exec db psql -U $$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2) \
	                                   -d $$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)

# ------------------------------------------------------------- migrations
# Always a one-shot container, run before anything else is recreated. If it
# fails the deploy stops and the running version is untouched.
.PHONY: migrate
migrate: ## Run Alembic migrations to head
	docker compose $(DEV) run --rm --no-deps \
	  -e DATABASE_URL="postgresql+psycopg://$$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2):$$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2)@db:5432/$$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)" \
	  api alembic upgrade head

.PHONY: revision
revision: ## New migration (make revision M="add fact_attendance")
	@test -n "$(M)" || (echo 'usage: make revision M="message"'; exit 1)
	docker compose $(DEV) run --rm --no-deps api alembic revision -m "$(M)"

# DDL needs the owning role, not the application one: `lnd_app_rw` has no
# CREATE on the database by design, so this target could never have worked
# without the same override `migrate` uses.
.PHONY: downgrade
downgrade: ## Roll back one migration
	docker compose $(DEV) run --rm --no-deps \
	  -e DATABASE_URL="postgresql+psycopg://$$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2):$$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2)@db:5432/$$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)" \
	  api alembic downgrade -1

# ------------------------------------------------------------------ checks
.PHONY: lint
lint: ## ruff check + format check
	cd api && $(BIN)ruff check . && $(BIN)ruff format --check .

# ruff, mypy and pytest are not in the runtime image by design, so they come
# from a local virtualenv. Prefer ./.venv if it exists — otherwise a machine
# with a system python that merely *has* a `pytest` on PATH runs the tests
# against the wrong interpreter and they fail on a missing dependency rather
# than on anything real. VENV=/some/other/venv overrides.
VENV ?= $(CURDIR)/.venv
BIN  := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/,)

.PHONY: fmt
fmt: ## ruff format
	cd api && $(BIN)ruff format . && $(BIN)ruff check --fix .

.PHONY: types
types: ## mypy
	cd api && $(BIN)mypy src

# Tests get their own database, never the dev one. Several of them assert on
# the whole contents of ops.sync_run — "the newest success wins", "a healthy
# platform raises nothing" — which is only true of an empty schema. Pointed at
# the dev database, every real sync ever run leaks in as fixture data and dozens
# fail for reasons that have nothing to do with the code. Truncating the dev
# database instead would work exactly once, and throw away its history to do it.
#
# Best effort: with no stack running, the URL simply points at nothing and the
# database tests skip, which is what `pytest` on its own already does.
.PHONY: test-db
test-db: ## Create and migrate the throwaway test database
	@u=$$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2); \
	 p=$$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2); \
	 d=$$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)_test; \
	 docker compose $(DEV) exec -T db psql -U $$u -d postgres -tAc \
	   "SELECT 1 FROM pg_database WHERE datname='$$d'" 2>/dev/null | grep -q 1 \
	   || docker compose $(DEV) exec -T db createdb -U $$u $$d 2>/dev/null \
	   || { echo "  test database unavailable — database tests will skip"; exit 0; }; \
	 docker compose $(DEV) run --rm --no-deps \
	   -e DATABASE_URL="postgresql+psycopg://$$u:$$p@db:5432/$$d" \
	   api alembic upgrade head >/dev/null 2>&1 \
	   || echo "  could not migrate $$d — database tests will skip"

.PHONY: test
test: test-db ## pytest with coverage
	cd api && TEST_DATABASE_URL="postgresql+psycopg://$$(grep -E '^POSTGRES_USER=' ../.env | cut -d= -f2):$$(grep -E '^POSTGRES_PASSWORD=' ../.env | cut -d= -f2)@127.0.0.1:5432/$$(grep -E '^POSTGRES_DB=' ../.env | cut -d= -f2)_test" $(BIN)pytest

.PHONY: check
check: lint types test ## Everything CI runs on the API

.PHONY: web-check
web-check: ## Type-check and build the front end
	cd web && npm run typecheck && npm run build

# --------------------------------------------------------------- deployment
.PHONY: deploy-staging
deploy-staging: ## Promote IMAGE_TAG to staging (migrations first)
	@test -n "$(IMAGE_TAG)" || (echo 'usage: make deploy-staging IMAGE_TAG=<sha>'; exit 1)
	docker compose $(STAGING) pull
	docker compose $(STAGING) run --rm --no-deps api alembic upgrade head
	docker compose $(STAGING) up -d
	docker image prune -f

.PHONY: deploy-prod
deploy-prod: ## Promote IMAGE_TAG to production (migrations first)
	@test -n "$(IMAGE_TAG)" || (echo 'usage: make deploy-prod IMAGE_TAG=<sha>'; exit 1)
	docker compose $(PROD) pull
	docker compose $(PROD) run --rm --no-deps api alembic upgrade head
	docker compose $(PROD) up -d
	docker image prune -f

.PHONY: config
config: ## Render the merged dev composition
	docker compose $(DEV) config
