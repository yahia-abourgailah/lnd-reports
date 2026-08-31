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
init: ## First-time setup: create .env from the template
	@test -f .env || (cp .env.example .env && \
	  python3 -c "import secrets,pathlib; p=pathlib.Path('.env'); \
p.write_text(p.read_text().replace('generate-a-real-one-with-secrets-token-urlsafe-48', secrets.token_urlsafe(48)))" && \
	  echo "created .env with a generated SESSION_SECRET")
	@echo "Now fill in OIDC_* when the app registration exists (Q-14)."

.PHONY: up
up: ## Start the full stack in dev (http://localhost:8080)
	docker compose $(DEV) up --build -d
	@$(MAKE) --no-print-directory migrate
	@echo "→ http://localhost:8080"

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

.PHONY: downgrade
downgrade: ## Roll back one migration
	docker compose $(DEV) run --rm --no-deps api alembic downgrade -1

# ------------------------------------------------------------------ checks
.PHONY: lint
lint: ## ruff check + format check
	cd api && ruff check . && ruff format --check .

.PHONY: fmt
fmt: ## ruff format
	cd api && ruff format . && ruff check --fix .

.PHONY: types
types: ## mypy
	cd api && mypy src

.PHONY: test
test: ## pytest with coverage
	cd api && pytest

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
