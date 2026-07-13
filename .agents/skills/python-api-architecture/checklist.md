# Backend API Checklist

Use this checklist before finishing implementation or review.

## Architecture

- [ ] Existing project structure was inspected before adding new files.
- [ ] New code follows existing project patterns first.
- [ ] Business logic is in `services`, not in API endpoints.
- [ ] SQL and persistence logic are in `crud`, not in services or endpoints.
- [ ] Shared helpers without business logic are in `utils`.
- [ ] Constants are in `app/constants/`.

## Models

- [ ] SQLAlchemy fields use `Mapped[type] = mapped_column(...)`.
- [ ] Relationships use `lazy="selectin"` unless the project has a different explicit pattern.
- [ ] Owned child collections use `cascade="all, delete-orphan"` when applicable.
- [ ] Foreign keys and constraints have explicit names.
- [ ] Enum classes inherit from `str`.
- [ ] Boolean database defaults use `server_default` when needed.
- [ ] Model has a meaningful `__repr__`.
- [ ] Model exports are updated if the project uses `app/models/__init__.py`.

## Schemas

- [ ] Schema hierarchy follows `Base -> Create / Update -> Read`.
- [ ] Update schemas reject unexpected fields when supported.
- [ ] ORM mode/from-attributes is only used in read schemas.
- [ ] Swagger examples are present if the project uses schema examples.
- [ ] Validators are in schemas, not endpoints.

## CRUD

- [ ] CRUD imports no FastAPI request/response primitives.
- [ ] CRUD methods are async.
- [ ] CRUD handles session rollback on integrity errors.
- [ ] Update methods only apply explicitly provided fields.
- [ ] Private query helpers start with `_`.
- [ ] CRUD singleton is at the end of the module when that pattern is used.

## Services

- [ ] Services contain orchestration and business rules.
- [ ] Services do not import `Request`, route decorators, or `HTTPException`.
- [ ] `db_session`, cache, and similar dependencies are passed explicitly when practical.
- [ ] Long workflows log with stable context prefixes.
- [ ] External clients are initialized lazily when they keep state.

## API

- [ ] Endpoints only validate/accept input, call services, and return responses.
- [ ] Endpoints have `summary`.
- [ ] Endpoints have `response_model` when returning a body.
- [ ] Authorization-only dependencies use `dependencies=[Depends(...)]` when the user object is not needed.
- [ ] Router is registered in `app/api/routers.py`.

## Tasks

- [ ] Tasks call services instead of duplicating business logic.
- [ ] Task names, queue names, retry settings, schedules, and timeouts come from settings when environment-dependent.
- [ ] Task payloads use explicit kwargs.
- [ ] Retry settings are only used for retry-safe operations.
- [ ] Broker-generated task IDs are used when the project has a broker id generator.

## Configuration And Logging

- [ ] Required secrets have no real default values.
- [ ] DSNs are built from settings properties, not hardcoded as deployment strings.
- [ ] `.env` or deployment config owns environment-specific values.
- [ ] Logging is configured before FastAPI app creation when needed.
- [ ] Services and tasks use stable log prefixes.
- [ ] Logs do not include secrets, tokens, passwords, authorization headers, or private file contents.

## Cache

- [ ] Cache prefixes and TTLs are constants.
- [ ] Cache uses get-or-set for read-heavy data.
- [ ] Cache write failures do not break the main result when the source of truth is available.
- [ ] Sensitive data is not cached without explicit project rules.

## Tests

- [ ] Service business logic has focused tests.
- [ ] CRUD persistence changes have database/integration tests when needed.
- [ ] Schema validation rules are tested.
- [ ] Endpoint tests are added when API contracts change.
- [ ] Async tests use the project async pytest marker.
- [ ] Integration tests use the project integration marker.
- [ ] Test docstrings describe scenarios in Russian.
