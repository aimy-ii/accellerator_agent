# Примеры: FastAPI + LangGraph SDK

Полный рабочий пример из статьи (yakvenalex, Habr).

## Структура проекта

```
├── main.py       — FastAPI app + lifespan
├── router.py     — эндпоинты /health, /agent
├── utils.py      — вызов графа через SDK
├── schemas.py    — ChatRequest / ChatResponse
├── deps.py       — FastAPI dependencies
└── config.py     — настройки через pydantic-settings
```

## config.py

```python
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    langgraph_url: str = "http://127.0.0.1:2024"
    access_token: str = "change-me"
    agent_assistant_id: str = "agent"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

## main.py

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langgraph_sdk import get_client
from config import get_settings
from router import router as graph_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.client = get_client(url=settings.langgraph_url)
    yield

def create_app() -> FastAPI:
    app = FastAPI(title="FastAPI + LangGraph SDK", lifespan=lifespan)
    app.include_router(graph_router)
    return app

app = create_app()
```

## utils.py

```python
import secrets
from uuid import uuid4
from fastapi import HTTPException, status

def check_token(req, settings) -> None:
    if not secrets.compare_digest(
        req.access_token.encode(), settings.access_token.encode()
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

async def run_graph(client, assistant_id: str, req) -> dict:
    thread_id = req.thread_id or uuid4()
    input_payload = {"messages": [{"role": "user", "content": req.message}]}
    final_state = None

    try:
        async for chunk in client.runs.stream(
            thread_id=str(thread_id),
            assistant_id=assistant_id,
            input=input_payload,
            stream_mode="values",
            if_not_exists="create",
        ):
            if chunk.event == "values":
                final_state = chunk.data
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LangGraph error: {e}")

    return {"thread_id": thread_id, "output": final_state}
```

## router.py

```python
from fastapi import APIRouter, Request
from deps import ClientDep, SettingsDep
from schemas import ChatRequest, ChatResponse
from utils import check_token, run_graph

router = APIRouter(tags=["graph"])

@router.get("/health")
async def health(client: ClientDep):
    try:
        assistants = await client.assistants.search(limit=20)
        return {"status": "ok", "assistants": [a["graph_id"] for a in assistants]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@router.post("/agent", response_model=ChatResponse)
async def agent(req: ChatRequest, client: ClientDep, settings: SettingsDep):
    check_token(req, settings)
    return await run_graph(client, settings.agent_assistant_id, req)
```

## Тест через curl

```bash
# Проверить связь
curl http://127.0.0.1:8000/health

# Первый запрос — создаёт новый тред
curl -X POST http://127.0.0.1:8000/agent \
  -H 'Content-Type: application/json' \
  -d '{"access_token": "my-token", "message": "Привет"}'

# Продолжить диалог — передать thread_id из предыдущего ответа
curl -X POST http://127.0.0.1:8000/agent \
  -H 'Content-Type: application/json' \
  -d '{"access_token": "my-token", "message": "Что ты помнишь?", "thread_id": "uuid-из-ответа"}'
```
