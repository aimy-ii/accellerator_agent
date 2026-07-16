.DEFAULT_GOAL := help
.PHONY: help install dev run test lint fmt typecheck compile clean

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