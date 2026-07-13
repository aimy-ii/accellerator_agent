# Python Backend API Standard

## How To Use This Standard

This document describes a Python backend API style for AI assistants and engineers. Before writing code, inspect the existing project and follow its structure. If the project does not already define a pattern, use this standard.

The goal is not to invent new architecture for every task. The goal is to produce predictable code with strict layer boundaries, clear names, explicit types, async I/O, and Russian docstrings.

## Core Principles

1. Strict responsibility separation: each layer does only its own job.
2. Business logic never goes into API endpoints.
3. SQL queries and persistence logic never go into services or endpoints.
4. Readability is more important than compactness.
5. Use meaningful names, explicit types, and explicit dependencies.
6. Use `async`/`await` for all I/O: database, Redis/cache, HTTP clients, queues, and filesystem work when async APIs exist.
7. Public modules, classes, service functions, CRUD methods, tasks, and tests use Russian docstrings.

## Project Architecture

Use layered architecture:

```text
app/
├── core/           # configuration, logging, database/cache connections
├── constants/      # constants grouped by domain area, one file per group
├── models/         # SQLAlchemy models only
├── schemas/        # Pydantic schemas: DTO, validation, serialization
├── crud/           # data-access layer, SQL queries only
├── services/       # business logic, orchestration, external APIs
├── api/
│   ├── endpoints/  # FastAPI routers, request/response only
│   └── routers.py  # router registration
├── tasks/          # background tasks
├── taskiq/         # broker and queue configuration
└── utils/          # generic helpers without business logic
```

## Layer Boundaries

| Layer | Knows About | Must Not Know About |
|---|---|---|
| `models` | SQLAlchemy, Python types | Pydantic, FastAPI, business logic |
| `schemas` | Pydantic, Python types | SQLAlchemy sessions, database queries, FastAPI routing |
| `crud` | SQLAlchemy, models, sessions | FastAPI, HTTP, business logic |
| `services` | CRUD, schemas, cache, queues, external APIs | FastAPI `Request`, route decorators, `HTTPException` |
| `api/endpoints` | services, schemas, `Depends` | direct SQL, direct CRUD orchestration |
| `tasks` | services, task dependencies | FastAPI `Request`, route decorators |

## Naming

| Entity | Style | Examples |
|---|---|---|
| Files and directories | `snake_case` | `documents.py`, `validation_service/` |
| Classes | `PascalCase` | `Document`, `DocumentRead`, `DocumentsCRUD` |
| Functions and methods | `snake_case` | `get_by_name`, `validate_document` |
| Private methods | `_snake_case` | `_apply_filters`, `_init_client` |
| Variables and parameters | `snake_case` | `document_id`, `validation_scope` |
| Constants | `UPPER_SNAKE_CASE` | `NAME_LENGTH`, `CACHE_TTL` |
| CRUD singletons | `snake_case` | `documents_crud`, `users_crud` |
| Enum values | `UPPER_SNAKE_CASE` | `StatusType.ACTIVE` |
| Routers | `<name>_router` | `documents_router`, `users_router` |
| Enum classes | `PascalCase` + `str` inheritance | `class StatusType(str, enum.Enum)` |

Names must describe intent. Do not use names like `process_data`, `do_thing`, or `handle`. Prefer names like `validate_uploaded_document`, `send_external_webhook`, or `calculate_weighted_score`.

## Docstrings

Docstrings are in Russian.

Use one-line docstrings for simple functions:

```python
async def get_by_name(self, name: str, session: AsyncSession):
    """Получает объект по полю name. Возвращает None если не найден."""
```

Use multiline docstrings with `Args`, `Returns`, and `Raises` for complex service methods:

```python
async def get_response(self, prompt: str, temperature: float = 0.7) -> str | None:
    """
    Получение ответа от внешнего API.

    Args:
        prompt: Текст запроса
        temperature: Температура генерации (0.0 - 1.0)

    Returns:
        str: Текст ответа или None при ошибке

    Raises:
        Exception: При ошибках подключения
    """
```

Use class docstrings to describe purpose and important behavior:

```python
class CRUDDocument:
    """
    CRUD для документов и истории проверок.
    При конфликте в БД выполняет откат транзакции и возвращает None.
    """
```

Use schema docstrings to describe purpose and restrictions:

```python
class DocumentUpdate(BaseModel):
    """
    Обновление простых полей документа.
    Связанные объекты обновляются через отдельные эндпоинты.
    """
```

Use test docstrings to describe the tested scenario:

```python
async def test_cache_failure_still_returns_data():
    """Ошибки записи в Redis не ломают результат: данные всё равно возвращаются."""
```

## Models

Rules:

- Use SQLAlchemy typed mappings: `Mapped[type] = mapped_column(...)`.
- Add `__tablename__` explicitly when the table name is not obvious.
- Put string length constants in `app/constants/`.
- Use `lazy="selectin"` as the standard relationship loading strategy.
- Use `cascade="all, delete-orphan"` for owned child collections.
- Put `order_by` directly into `relationship(...)` when ordering matters.
- Name foreign keys: `fk_<table>_<field>_<ref_table>`.
- Name unique constraints: `uq_<table>_<fields>`.
- Enum classes inherit from `str`.
- Use `server_default` for boolean fields when the database needs a default.
- Add meaningful `__repr__`.
- Re-export models from `app/models/__init__.py` when the project uses model re-exports.

## Schemas

Use the hierarchy:

```text
Base -> Create / Update -> Read
```

Rules:

- `Read` schemas are for API responses.
- `Create` schemas describe required and optional creation fields.
- `Update` schemas contain optional fields and forbid unexpected fields when supported by the project's Pydantic version.
- Use ORM mode/from-attributes only in read schemas.
- Add examples for Swagger in create/read schemas when the project uses schema examples.
- Version schemas as `DocumentReadV2` only when a stable public contract needs backward compatibility.
- Use field validators for single-field transformation/validation.
- Use root/model validators for cross-field validation.

## CRUD

CRUD is the data-access layer.

Rules:

- CRUD contains SQL and persistence operations only.
- CRUD does not import FastAPI, `Request`, or `HTTPException`.
- All CRUD methods are async.
- Use `CRUDBase` for standard entities when the project has a base class.
- Avoid inheritance for complex entity-specific persistence logic if a base class makes the code harder to read.
- Private helper methods start with `_`.
- The CRUD singleton is created at the end of the module.
- On `IntegrityError`, rollback the session before returning or raising.
- Update methods should update only explicitly provided fields.

Expected CRUD operations:

- `get`
- `get_multi`
- `get_by_name` or another natural unique lookup
- `create`
- `update`
- `update_with_dict` when direct update is useful
- `remove`
- `create_multi` when batch creation is needed

## Services

Services contain business logic and orchestration.

Use a class when the service owns an external client or stateful resource:

```python
class ExternalApiClient:
    """Клиент для работы с внешним API. Ленивая инициализация соединения."""

    def __init__(self) -> None:
        self.client: Optional[object] = None
        self.http_client: Optional[httpx.AsyncClient] = None

    async def _init_client(self) -> None:
        """Инициализация клиента при первом обращении."""
        if self.client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)
            self.client = SomeClient(http_client=self.http_client)

    async def _cleanup(self) -> None:
        """Освобождение ресурсов клиента."""
        if self.http_client:
            await self.http_client.aclose()
        self.client = None
```

Use modules with async functions for pipelines and process-style logic:

```text
app/services/document_validation_service/
├── __init__.py
└── main.py
```

Rules:

- Pass `db_session`, `cache`, and similar dependencies through function parameters, not through `__init__`, when the same service is used by HTTP endpoints and background tasks.
- Services do not import FastAPI `Request` or `HTTPException`.
- Services may call CRUD, cache, queues, external clients, and utility functions.
- Complex services are packages with `__init__.py` and implementation modules.
- Logging in long-running services uses stable context prefixes like `[VALIDATION]`, `[CLEANUP]`, `[WEBHOOK]`, `[AUTH]`, or another project-approved prefix.

## API Endpoints

Endpoints accept request data, call services, and return responses.

Rules:

- No business logic in endpoints.
- No direct SQL queries in endpoints.
- Avoid using CRUD directly from endpoints when a service layer exists.
- Always set `summary` for Swagger when adding endpoints.
- Always set `response_model` when the endpoint returns a body.
- Use `dependencies=[Depends(...)]` for authorization checks that do not need a function parameter.
- Endpoint-local helper functions start with `_`.
- Register routers in `app/api/routers.py`.

Router pattern:

```python
documents_router = APIRouter()


@documents_router.get(
    path="/",
    summary="Получение списка документов",
    response_model=list[DocumentRead],
)
async def get_documents(
    is_active: Optional[bool] = Query(None, description="Фильтр по активности"),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
) -> list[DocumentRead]:
    """Получение списка документов с опциональной фильтрацией."""
    return await documents_service.get_documents(
        session=session,
        user=user,
        is_active=is_active,
    )
```

Router registration pattern:

```python
main_router = APIRouter(prefix="/api")
main_router.include_router(documents_router, prefix="/documents", tags=["Documents"])
```

## Background Tasks

Background tasks call services. They do not duplicate business logic.

Rules:

- Put broker configuration in `app/taskiq/broker.py` or the project equivalent.
- Use result backend when tasks need return values.
- Queue names, task names, retry settings, cron schedules, and timeouts should come from settings when they vary by environment.
- Task functions are async.
- Task functions should be small: resolve dependencies, call a service, log result.
- Use `retry_count` and `retry_delay` only for operations that are safe to retry.
- Prefer explicit kwargs for task payloads.
- Use `labels={"queue_name": "<queue>"}` when sending tasks through a low-level message API and the project uses queue labels.
- Prefer broker-generated task IDs when the project has a broker id generator.

Task name format:

```text
"module_path:function_name"
```

Example task settings:

```python
document_validation_task_name: str = "app.tasks.document_tasks:validate_document_task"
cleanup_task_name: str = "app.tasks.cleanup_tasks:cleanup_old_files_task"
```

Example TaskIQ message:

```python
taskiq_msg = TaskiqMessage(
    task_id=broker.id_generator(),
    task_name=settings.document_validation_task_name,
    labels={"queue_name": settings.default_queue_name},
    args=[],
    kwargs={
        "document_id": document_id,
        "file_path": file_path,
    },
)
broker_message = broker.formatter.dumps(taskiq_msg)
await broker.kick(broker_message)
```

## Queue Configuration

Use a broker module:

```python
result_backend = RedisAsyncResultBackend(settings.redis_dsn)

broker = ListQueueBroker(
    url=settings.redis_dsn,
    queue_name=settings.taskiq_queue_name,
).with_result_backend(result_backend)
```

Use a scheduler when cron tasks are needed:

```python
scheduler = TaskiqScheduler(
    broker=broker,
    sources=[LabelScheduleSource(broker)],
)
```

Use separate queues for work with different resource profiles:

- `default` for normal tasks.
- `heavy_queue` for CPU-heavy or long-running tasks.
- `cleanup_queue` for cleanup tasks.

## Configuration

Use typed settings.

Rules:

- Required secrets have no real default values.
- Environment-specific values live in `.env` or deployment configuration, not in code.
- Build Redis/Postgres DSNs from settings properties instead of hardcoding connection strings.
- Use `extra = "ignore"` or the project equivalent when environment files may contain unrelated values.
- Create a module-level `settings = Settings()` singleton when this is the project convention.
- Pydantic v1 uses `from pydantic import BaseSettings`.
- Pydantic v2 uses `from pydantic_settings import BaseSettings`.

Example:

```python
class Settings(BaseSettings):
    app_title: Optional[str] = "MyApp"
    secret_key: str
    jwt_lifetime: int = 7 * 24 * 3600

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None

    BASE_DIR: Path = Path(__file__).parent.parent

    @property
    def redis_dsn(self) -> str:
        """Формирует DSN для подключения к Redis."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
```

## Logging

Use the standard `logging` module unless the project already standardizes another logger.

Rules:

- Configure logging before creating the FastAPI app.
- Use file rotation plus console output when the app needs persistent logs.
- Simple cases may use f-strings.
- Services and tasks should prefer `%` formatting to avoid eager interpolation and keep stable log templates.
- Use context prefixes for services and tasks: `[VALIDATION]`, `[CLEANUP]`, `[WEBHOOK]`, `[AUTH]`, `[TASK]`, or a project-approved prefix.
- Do not log secrets, tokens, passwords, full authorization headers, or private file contents.

Example:

```python
logging.info("[VALIDATION] Старт проверки document_id=%s", document_id)
logging.error("[VALIDATION] Документ не найден document_id=%s", document_id)
logging.warning("[CACHE] Ошибка записи document_id=%s: %s", document_id, exc)
```

## Caching

Use Redis for shared cache when the project has Redis.

Rules:

- Put cache prefixes and TTL values in constants.
- Use get-or-set for read-heavy data.
- Cache write failures should usually not break the main result when the source of truth is available.
- Do not cache sensitive data unless the project has explicit rules for it.

Pattern:

```python
DOCUMENT_CACHE_PREFIX = "document:data:"
DOCUMENT_CACHE_TTL = 60 * 20

cache_key = f"{DOCUMENT_CACHE_PREFIX}{document_id}"
cached = await cache.get(cache_key)
if cached:
    return json.loads(cached)

data = await documents_crud.get(obj_id=document_id, session=db_session)
try:
    await cache.set(cache_key, json.dumps(data), ex=DOCUMENT_CACHE_TTL)
except Exception as exc:
    logging.warning("[CACHE] Ошибка записи document_id=%s: %s", document_id, exc)

return data
```

## Constants

Constants live in `app/constants/`, one file per semantic group:

```text
app/constants/
├── user.py       # USER_FIRST_NAME_LENGTH = 30
├── document.py   # DOCUMENT_NAME_LENGTH = 200
├── cache.py      # CACHE_TTL_SHORT = 60 * 5
└── validation.py # VALIDATION_TIMEOUT = 120
```

Do not hide magic numbers in models, schemas, or services.

## Tests

Recommended structure:

```text
tests/
├── conftest.py       # shared fixtures: database session, Redis client, seed helpers
├── core/             # configuration and utility tests
├── crud/             # CRUD tests
├── fixtures/         # test data factories
├── schemas/          # Pydantic schema tests
└── services/         # business logic tests: unit and integration
```

Rules:

- Use `pytest.mark.asyncio` for async tests.
- Use `pytest.mark.integration` for tests requiring real external dependencies.
- CRUD tests should cover database behavior.
- Service tests should cover business logic and edge cases.
- Schema tests should cover validation and forbidden fields.
- Add regression tests for fixed bugs.
- Test docstrings are in Russian and describe the scenario.

## Feature Implementation Order

When implementing a new feature, work in this order:

1. Analyze existing related models, schemas, CRUD, services, endpoints, tasks, and tests.
2. Add constants in `app/constants/` if needed.
3. Add or update models in `app/models/<entity>.py`; update `app/models/__init__.py` if the project re-exports models.
4. Create an Alembic migration when the database schema changes.
5. Add schemas in `app/schemas/<entity>.py`: Base / Create / Update / Read.
6. Add CRUD in `app/crud/<entity>.py`; create the module-level singleton at the end.
7. Add service logic in `app/services/<entity>_service/`.
8. Add API endpoints in `app/api/endpoints/<entity>.py`; register the router in `app/api/routers.py`.
9. Add background tasks in `app/tasks/` if asynchronous processing is needed.
10. Add focused tests: service unit tests, CRUD integration tests, schema tests, and endpoint tests when the API contract changes.

Do not skip layers when the feature requires them. Do not change the order unless the existing project structure clearly requires a different sequence.

## Typical Makefile Commands

Use project-provided commands when available:

| Command | Action |
|---|---|
| `make run` | Run FastAPI |
| `make migrate` | Apply Alembic migrations |
| `make worker` | Run background worker |
| `make scheduler` | Run task scheduler |
| `make db_local` | Start local PostgreSQL |
| `make redis_local` | Start local Redis |
| `make test` | Run tests |
| `make flake` | Run style checks |
| `make isort_run` | Sort imports |
| `make build_image` | Build Docker image |
