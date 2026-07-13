---
name: backend-makefile-api-tests
description: Apply the user's backend Makefile and api_tests conventions. Use when creating or editing Makefile targets, make commands, API black-box tests, pytest api_tests structure, test clients, auth fixtures, or backend project run/test workflows.
---

# Backend Makefile And API Tests

## Makefile Conventions

Use the user's anonymized backend Makefile style.

- Start with `SHELL := /bin/bash`.
- Add `.PHONY` with every public target.
- Prefer short target names: `install`, `run`, `worker`, `migrate`, `migration`, `seed`, `db_local`, `redis_local`, `run_local_project`, `logs_local_project`, `down_local_project`, `build_image`, `redis_bash`, `redis_clean`, `lint`, `test`, `api_tests`.
- Group targets with short Russian section comments.
- For `uv` projects, use `uv sync` for install and `uv run ...` for Python commands.
- For app run targets, prefer:
  ```make
  run:
  	uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
  ```
- For tests, keep both `test` and `api_tests` when the project has an `api_tests/` folder:
  ```make
  test:
  	uv run pytest api_tests -v

  api_tests:
  	uv run pytest api_tests -v
  ```
- For lint, include app code, scripts, and API tests:
  ```make
  lint:
  	uv run ruff check src/ scripts/ api_tests/
  ```
- For Docker infra targets, run Compose from `infra/` and pass env files explicitly:
  ```make
  db_local:
  	cd infra && docker compose --env-file ../.env -f docker-compose.yml up postgresql
  ```
- Do not invent complex shell wrappers when a direct Make target is enough.

## api_tests Structure

Use this layout for black-box API tests:

```text
api_tests/
├── .env.example
├── conftest.py
├── pytest.ini
├── requirements.txt
├── test_results.log        # generated, ignore in git
├── tests/
│   ├── test_auth.py
│   ├── test_admin.py
│   └── test_documents.py
└── utils/
    ├── api_client.py
    ├── auth.py
    ├── helpers.py
    └── logger.py
```

Core rules:

- `pytest.ini` sets `testpaths = tests` and `pythonpath = .`.
- `conftest.py` owns env loading and fixtures.
- Load `api_tests/.env` first, otherwise `.env.example`.
- If useful, derive local compose defaults from `infra/.env`, but never hardcode real secrets.
- Provide fixtures for `base_url`, `public_client`, `admin_client`, `employee_client`, and role-specific clients such as `other_employee_client`.
- Keep API tests black-box: use HTTP through an `APIClient`, not direct DB/session calls.
- Put reusable HTTP helpers in `utils/`, not in test files.
- Use `created_documents` or similar cleanup fixtures for resources created during tests.
- Mark tests that require Redis/TaskIQ/background workers:
  ```python
  @pytest.mark.requires_redis
  ```
- Assertions should include response text for readable failures:
  ```python
  assert response.status_code == 200, response.text
  ```

## APIClient Pattern

Use a small synchronous `httpx` client:

- Store `base_url`, optional Bearer token, and timeout.
- Implement `get`, `post`, `delete`, `post_form`, and `post_multipart`.
- Log requests and responses.
- Redact sensitive keys before logging: `access_token`, `authorization`, `email`, `full_name`, `password`, `token`, `username`.
- Mask email-like strings in logs.

## Auth And Env

- Auth helper gets JWT through `POST /auth/login`.
- Use env names like `API_BASE_URL`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `EMPLOYEE_EMAIL`, `EMPLOYEE_PASSWORD`, `OTHER_EMPLOYEE_EMAIL`, `OTHER_EMPLOYEE_PASSWORD`.
- Real `.env`, tokens, passwords, test logs, uploaded/generated files, caches, and virtualenvs must be ignored and not committed.

## Test Style

- Use class-based test grouping by API area: `TestAuth`, `TestAdmin`, `TestDocuments`.
- Name tests by behavior: `test_login_success_returns_access_token`, `test_employee_admin_me_returns_403`.
- Keep docstrings short and behavior-focused.
- Use helper factories for fake files, e.g. minimal DOCX/PDF magic bytes.
- For owner isolation, prefer 404 for foreign resources when the API intentionally hides existence.
