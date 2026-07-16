.DEFAULT_GOAL := help
.PHONY: help install dev run test lint fmt typecheck compile clean \
	build_image up down restart prod logs logs_tail ps api_ok

help: ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install: ## Синхронизировать окружение (с dev-зависимостями)
	uv sync --extra dev

dev: ## Поднять LangGraph Studio (graph customer_agent из langgraph.json)
	uv run langgraph dev --no-reload

run: dev ## Синоним dev

test: ## Прогнать тесты
	uv run pytest -q

lint: ## Проверить стиль (ruff)
	uv run ruff check src tests

fmt: ## Автоисправления ruff
	uv run ruff check --fix src tests

typecheck: ## Проверка типов (mypy)
	uv run mypy src

compile: ## Быстрая проверка, что граф собирается
	uv run python -c "from src.graph.graph import graph; print('graph OK:', len(graph.get_graph().nodes), 'nodes')"

clean: ## Удалить кеши и артефакты
	rm -rf output/doc_cache .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# ─── Прод: LangGraph API + Postgres + Redis (docker compose) ─────────────────
build_image: ## Собрать Docker image LangGraph API (langgraph build)
	uv run langgraph build -t accelerator_agent

up: ## Поднять Redis/Postgres/API через docker compose
	docker compose up -d

down: ## Остановить и удалить контейнеры (без удаления volumes)
	docker compose down

restart: build_image ## Пересобрать image и пересоздать только langgraph-api
	docker compose up -d --force-recreate langgraph-api

prod: build_image up ## Собрать image и поднять весь прод-стек одной командой
	@echo "Прод-стек поднят: http://localhost:8123 (health: make api_ok)"

logs: ## Логи langgraph-api
	docker compose logs -f langgraph-api

logs_tail: ## Последние 200 строк логов langgraph-api
	docker compose logs --tail=200 langgraph-api

ps: ## Статус контейнеров прод-стека
	docker compose ps

api_ok: ## Проверить health эндпоинт API (http://localhost:8123/ok)
	curl -fsS http://localhost:8123/ok
	@echo