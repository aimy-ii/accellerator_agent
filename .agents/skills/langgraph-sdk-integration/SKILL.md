---
name: langgraph-sdk-integration
description: Интеграция LangGraph Server с собственным бэкендом через LangGraph SDK (Python). Используй когда нужно вызвать граф программно, передать файл/URL, работать со state и thread-ами, настроить стриминг или деплой FastAPI + LangGraph. Ключевые слова: LangGraph SDK, get_client, runs.stream, thread_id, state, LangGraph Server, FastAPI + LangGraph.
---

# LangGraph SDK — интеграция с бэкендом

## Стек

```
LangGraph Server (langgraph dev / langgraph up)
        ↕  REST API (порт 2024 / 8123)
LangGraph SDK (langgraph-sdk)
        ↕
Твой FastAPI / скрипт
```

LangGraph Server **не выставляется наружу**. Снаружи смотрит только твой FastAPI. SDK — прослойка между ними.

## Инициализация клиента

```python
from langgraph_sdk import get_client

# В lifespan FastAPI — создать один раз
client = get_client(url="http://127.0.0.1:2024")  # dev
# client = get_client(url="http://127.0.0.1:8123")  # prod (langgraph up)
```

## Вызов графа (стриминг)

```python
from uuid import uuid4

async def run_graph(client, assistant_id: str, input_data: dict, thread_id=None):
    thread_id = thread_id or uuid4()
    final_state = None

    async for chunk in client.runs.stream(
        thread_id=str(thread_id),
        assistant_id=assistant_id,   # имя из langgraph.json
        input=input_data,
        stream_mode="values",        # полный state после каждого шага
        if_not_exists="create",      # создать тред если нет
    ):
        if chunk.event == "values":
            final_state = chunk.data

    return thread_id, final_state
```

`stream_mode` варианты:
- `"values"` — полный state после каждого шага (нужен финальный результат)
- `"updates"` — только дельты
- `"messages"` — токены LLM в реальном времени

## Передача файла

LangGraph Server **не принимает бинарные файлы**. Передаётся только JSON-совместимое значение в `input`.

**Вариант 1 — локальный путь** (файл на машине сервера):
```python
input={"pdf_path": "/abs/path/to/document.pdf"}
```

**Вариант 2 — URL** (файл в S3/MinIO/CDN):
```python
input={"pdf_path": "https://s3.example.com/docs/prikaz.pdf"}
```

В узле графа добавить скачивание:
```python
import httpx
from pathlib import Path

def resolve_path(path_or_url: str) -> Path:
    if path_or_url.startswith("http"):
        tmp = Path("/tmp") / Path(path_or_url).name
        tmp.write_bytes(httpx.get(path_or_url).content)
        return tmp
    return Path(path_or_url)
```

## Управление state и тредами

```python
# Читать текущий state треда
state = await client.threads.get_state(thread_id)

# Вручную обновить state (без запуска графа)
await client.threads.update_state(
    thread_id,
    values={"some_field": "new_value"}
)

# История чекпоинтов — откатиться в любую точку
history = await client.threads.get_history(thread_id)
await client.threads.update_state(thread_id, checkpoint=history[2])
```

## Фоновые и отложенные запуски

```python
# Запустить асинхронно — клиент не ждёт
run = await client.runs.create(
    thread_id=str(thread_id),
    assistant_id="reglament_parser",
    input=input_data,
)
# Позже подключиться к идущему рану
async for chunk in client.runs.join(thread_id, run["run_id"]):
    ...

# Отложенный запуск — через N секунд
await client.runs.create(..., after_seconds=60)
```

## Несколько графов на одном сервере

В `langgraph.json`:
```json
{
  "graphs": {
    "reglament_parser": "./src/graph/graph.py:graph",
    "another_graph":    "./src/another/graph.py:graph"
  }
}
```

Каждый граф — отдельный `assistant_id`. State и чекпоинты изолированы.

## Шаблон FastAPI + SDK

```python
# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph_sdk import get_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.lg = get_client(url="http://127.0.0.1:2024")
    yield

app = FastAPI(lifespan=lifespan)

# router.py
@router.post("/check")
async def check_document(req: CheckRequest, request: Request):
    client = request.app.state.lg
    thread_id, state = await run_graph(
        client,
        assistant_id="reglament_parser",
        input_data={"pdf_path": req.file_url},
    )
    return {"thread_id": str(thread_id), "result": state}
```

## Мультизадачность (конфликт тредов)

```python
# Что делать если по треду уже идёт запуск:
client.runs.stream(..., multitask_strategy="enqueue")   # поставить в очередь
# или: "reject" | "interrupt" | "rollback"
```

## Деплой

| Команда | Режим | Что поднимает |
|---|---|---|
| `langgraph dev` | Dev, без Docker | Сервер + Studio, in-memory чекпоинтер |
| `langgraph up` | Prod, Docker | Сервер + Redis + PostgreSQL (персистентный чекпоинтер) |

Закрыть лишние порты в проде:
```bash
ufw allow 22
ufw allow 8000   # только твой FastAPI наружу
# порты 8123 (LangGraph) и 5433 (Postgres) — только локально
```

## Подробнее
- [examples.md](examples.md) — полный пример FastAPI + SDK с авторизацией
