# Obsidian MCP Server
# Manages build, deploy, and database operations

IMAGE_NAME := obsidian-mcp
IMAGE_TAG := latest
REGISTRY := localhost:5000
FULL_IMAGE := $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
DATA_DIR ?= ./data
COMPOSE_FILE := docker-compose.yml

GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m

.PHONY: help init build build-cached push image deploy up down restart logs shell db-init db-migrate db-backup db-restore status clean reindex reset-embeddings audit

help:
	@echo "$(GREEN)Obsidian MCP Server$(NC)"
	@echo ""
	@echo "$(YELLOW)Setup:$(NC)"
	@echo "  make init         - Initial setup (directories, .env, database)"
	@echo ""
	@echo "$(YELLOW)Build & Deploy:$(NC)"
	@echo "  make build        - Build Docker image (no cache)"
	@echo "  make build-cached - Build Docker image (with cache)"
	@echo "  make push         - Push image to local registry"
	@echo "  make image        - Build and push"
	@echo "  make deploy       - Full deploy (build, push, backup, recreate)"
	@echo ""
	@echo "$(YELLOW)Container Management:$(NC)"
	@echo "  make up           - Start container"
	@echo "  make down         - Stop container"
	@echo "  make restart      - Restart container"
	@echo "  make logs         - Tail container logs"
	@echo "  make shell        - Shell into container"
	@echo ""
	@echo "$(YELLOW)Database:$(NC)"
	@echo "  make db-init      - Create database, user, and extensions"
	@echo "  make db-migrate   - Run Alembic migrations"
	@echo "  make db-backup    - Backup database"
	@echo "  make db-restore FILE=<path> - Restore from backup"
	@echo ""
	@echo "$(YELLOW)Operations:$(NC)"
	@echo "  make reindex      - Trigger full vault reindex"
	@echo "  make reset-embeddings - Drop & recreate embedding column at configured dim"
	@echo "  make status       - Show container and health status"
	@echo "  make clean        - Remove containers and images"

init:
	@echo "$(GREEN)Setting up Obsidian MCP...$(NC)"
	@sudo mkdir -p $(DATA_DIR)/backups
	@sudo chown -R $(shell id -u):$(shell id -g) $(DATA_DIR)
	@sudo chmod -R 775 $(DATA_DIR)
	@if [ ! -f ".env" ]; then \
		echo "$(GREEN)Creating .env from template...$(NC)"; \
		cp .env.example .env; \
		DB_PASS=$$(openssl rand -hex 16); \
		SECRET=$$(openssl rand -hex 32); \
		sed -i "s/CHANGE_ME/$$DB_PASS/" .env; \
		sed -i "s/SECRET_KEY=.*/SECRET_KEY=$$SECRET/" .env; \
		echo "$(GREEN).env created with random secrets$(NC)"; \
	else \
		echo "$(YELLOW).env already exists$(NC)"; \
	fi
	@echo "$(GREEN)Setup complete. Next: make db-init && make deploy$(NC)"

build:
	@echo "$(GREEN)Building image (no cache)...$(NC)"
	docker build --no-cache --pull -f Dockerfile -t $(IMAGE_NAME):$(IMAGE_TAG) .
	@echo "$(GREEN)Built: $(IMAGE_NAME):$(IMAGE_TAG)$(NC)"

build-cached:
	@echo "$(GREEN)Building image (cached)...$(NC)"
	docker build -f Dockerfile -t $(IMAGE_NAME):$(IMAGE_TAG) .
	@echo "$(GREEN)Built: $(IMAGE_NAME):$(IMAGE_TAG)$(NC)"

push:
	@echo "$(GREEN)Pushing to registry...$(NC)"
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(FULL_IMAGE)
	docker push $(FULL_IMAGE)
	@echo "$(GREEN)Pushed: $(FULL_IMAGE)$(NC)"

image: build push

deploy: image
	@echo "$(GREEN)Deploying Obsidian MCP...$(NC)"
	@$(MAKE) db-backup 2>/dev/null || true
	docker compose -f $(COMPOSE_FILE) up -d --force-recreate
	@HOST=$$(grep -E '^MCP_HOSTNAME=' .env 2>/dev/null | cut -d= -f2); \
	echo "$(GREEN)Deployed! https://$${HOST:-localhost}$(NC)"

up:
	docker compose -f $(COMPOSE_FILE) up -d

down:
	docker compose -f $(COMPOSE_FILE) down

restart:
	docker compose -f $(COMPOSE_FILE) restart obsidian-mcp

logs:
	docker compose -f $(COMPOSE_FILE) logs -f --tail=100 obsidian-mcp

shell:
	docker compose -f $(COMPOSE_FILE) exec obsidian-mcp bash

db-init:
	@echo "$(GREEN)Initializing database...$(NC)"
	@bash docker/db-init.sh
	@echo "$(GREEN)Database ready$(NC)"

db-migrate:
	@echo "$(GREEN)Running migrations...$(NC)"
	docker compose -f $(COMPOSE_FILE) exec obsidian-mcp alembic upgrade head
	@echo "$(GREEN)Migrations complete$(NC)"

db-backup:
	@mkdir -p $(DATA_DIR)/backups 2>/dev/null || true
	@TIMESTAMP=$$(date +%Y%m%d_%H%M%S); \
	BACKUP_FILE="$(DATA_DIR)/backups/backup_$$TIMESTAMP.sql"; \
	docker exec postgres pg_dump -U postgres obsidian_mcp > $$BACKUP_FILE 2>/dev/null || true; \
	gzip $$BACKUP_FILE 2>/dev/null || true; \
	echo "$(GREEN)Backup: $$BACKUP_FILE.gz$(NC)"

db-restore:
	@if [ -z "$(FILE)" ]; then echo "$(RED)Usage: make db-restore FILE=<path>$(NC)"; exit 1; fi
	@echo "$(YELLOW)WARNING: This will replace the obsidian_mcp database!$(NC)"
	@echo "Press Ctrl+C to cancel, waiting 5s..."
	@sleep 5
	@if echo "$(FILE)" | grep -q ".gz$$"; then \
		gunzip -c $(FILE) | docker exec -i postgres psql -U postgres obsidian_mcp; \
	else \
		docker exec -i postgres psql -U postgres obsidian_mcp < $(FILE); \
	fi
	@echo "$(GREEN)Restored from $(FILE)$(NC)"

reindex:
	@echo "$(GREEN)Triggering reindex...$(NC)"
	@curl -s -X POST http://localhost:8000/api/reindex | python3 -m json.tool 2>/dev/null || echo "$(RED)Service not responding$(NC)"

reset-embeddings:
	@echo "$(YELLOW)Resetting embeddings — column will be recreated at EMBEDDING_DIMENSIONS$(NC)"
	@echo "Press Ctrl+C to cancel, waiting 5s..."
	@sleep 5
	docker compose -f $(COMPOSE_FILE) exec obsidian-mcp python -m scripts.reset_embeddings
	@echo "$(GREEN)Done. The next indexer pass will re-embed all notes.$(NC)"

status:
	@echo "$(GREEN)Obsidian MCP Status:$(NC)"
	@docker compose -f $(COMPOSE_FILE) ps
	@echo ""
	@echo "$(GREEN)Health:$(NC)"
	@HOST=$$(grep -E '^MCP_HOSTNAME=' .env 2>/dev/null | cut -d= -f2); \
	URL=$${HOST:+https://$$HOST/health}; \
	URL=$${URL:-http://localhost:8000/health}; \
	curl -s $$URL | python3 -m json.tool 2>/dev/null || echo "$(RED)Not responding$(NC)"

clean: down
	docker rmi $(IMAGE_NAME):$(IMAGE_TAG) $(FULL_IMAGE) 2>/dev/null || true
	@echo "$(GREEN)Cleaned. Data in $(DATA_DIR) preserved.$(NC)"

audit:
	pip-audit -r requirements.txt
