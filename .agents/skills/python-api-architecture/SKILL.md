---
name: python-api-architecture
description: Apply the user's Python API/backend architecture and coding style. Use when implementing or reviewing Python API services, FastAPI endpoints, SQLAlchemy models, Pydantic schemas, CRUD layers, services, TaskIQ tasks, Redis caching, Alembic migrations, or backend tests.
---

# Python API Architecture

## Instructions

This is the user's Python API/backend development standard. It is a reusable company-style standard, not tied to one codebase.

Before implementing or reviewing Python backend code:

1. Read the included source style guide at `backend-api-style-guide.md`.
2. Read `examples.md` when generating models, schemas, CRUD, services, endpoints, TaskIQ tasks, settings, logging, caching, or tests.
3. Use `checklist.md` before finishing implementation or review.
4. Follow the existing project structure first, then apply the standard where it fits.
5. Keep business logic out of API endpoints.
6. Keep SQL and persistence logic in CRUD/data-access layers.
7. Use explicit types, readable names, async I/O, and Russian docstrings.
8. For new features, work through the layers in the order described by the guide.

## Scope

Use this skill for Python backend/API work involving FastAPI, SQLAlchemy, Pydantic, Alembic, Redis, TaskIQ/Celery-style background tasks, layered architecture, CRUD/service separation, and tests.
