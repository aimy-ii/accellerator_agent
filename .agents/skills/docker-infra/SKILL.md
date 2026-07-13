---
name: docker-infra
description: Apply the user's Docker Compose infrastructure conventions. Use when creating or editing docker-compose files, nginx gateway configs, Dockerfiles for services, infra directory layout, or Makefile targets for Docker infra (db_local, redis_local, run_local_project, down_local_project, build_image, logs_local_project).
---

# Docker Infrastructure Conventions

## Directory Layout

```
project-root/
  .env                          # единый env-файл для всего стека
  infra/
    docker-compose.yml          # только инфра для разработки: postgresql + redis
    docker-compose.local.yml    # полный стек, образы собираются из исходников (build:)
    docker-compose.production.yml # полный стек, образы из реестра (image:)
    docker-compose.redis.yml    # опционально: только redis
    docker-compose.mcp.yml      # опционально: отдельный сервис
    nginx/                      # API-gateway
      Dockerfile
      nginx.conf
    nginx_audio/                # отдача статичных аудио-файлов
      Dockerfile
      nginx.conf
    nginx_front/                # SPA-фронтенд (если есть)
      Dockerfile
      nginx.conf
  back/                         # исходники бэкенда
  ai_consumer/                  # отдельный AI-воркер (если есть)
  mcp_service/                  # MCP-сервис (если есть)
```

## docker-compose: два файла для одного стека

- `docker-compose.local.yml` — **разработка**: каждый сервис собирается через `build: ../back/` (из исходников).
- `docker-compose.production.yml` — **прод**: каждый сервис использует готовый образ `image: user/project_service:latest`.
- Структура сервисов, volumes, команды — идентичны в обоих файлах; отличаются только `build` vs `image` и иногда биндинг портов.
- `docker-compose.yml` в `infra/` — только инфра (`postgresql`, `redis`), используется при локальной разработке без Docker-бэкенда.

## Общие правила сервисов

- `restart: unless-stopped` — на всех сервисах.
- `env_file: ../.env` — путь относительно файла compose (`.env` лежит в корне проекта).
- Каждый сервис монтирует таймзону:
  ```yaml
  volumes:
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
  ```
- Named volumes объявляются в секции `volumes:` верхнего уровня. Типичные имена: `pg_data`, `redis_data`, `logs`, `calls_audio`.
- Переменные окружения, специфичные для сервиса (не в `.env`), передаются через `environment:` — не дублируй их в `.env`.

## PostgreSQL

```yaml
postgresql:
  image: postgres:15.9-alpine
  container_name: postgresql
  restart: unless-stopped
  volumes:
    - pg_data:/var/lib/postgresql/data
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
  env_file:
    - ../.env
  ports:
    - "5432:5432"          # prod: можно биндить на внутренний IP: "192.168.x.x:5432:5432"
```

## Redis

```yaml
redis:
  image: redis:7-alpine
  container_name: redis
  restart: unless-stopped
  volumes:
    - redis_data:/data
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
  ports:
    - "6379:6379"          # prod: биндить на внутренний IP если нужна изоляция
  command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD:-redispass}
```

## Backend (FastAPI + Alembic)

```yaml
backend:
  # local:
  build: ../back/
  # production:
  # image: user/project_back:latest
  env_file: ../.env
  container_name: backend
  command: >
    sh -c "sleep 10 && alembic upgrade head &&
           uvicorn main:app --host 0.0.0.0 --port 8000"
  depends_on:
    - postgresql
    - redis
  restart: unless-stopped
  volumes:
    - calls_audio:/back/app/calls
    - logs:/back/app/logs
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
```

- `sleep 10` перед миграцией — ждём готовности БД.
- Миграции (`alembic upgrade head`) запускаются внутри команды бэкенда, не отдельным init-контейнером.

## TaskIQ Workers

Каждая очередь — отдельный сервис. Имена: `worker_heavy`, `worker_light`, `worker_cleanup`, `taskiq_scheduler`, `ai_consumer_worker`.

```yaml
worker_heavy:
  build: ../back/    # или image:
  env_file: ../.env
  container_name: worker_heavy
  command: taskiq worker app.taskiq.broker:broker app.tasks.audio_tasks --max-async-tasks 4
  depends_on:
    - postgresql
    - redis
  restart: unless-stopped
  environment:
    - TASKIQ_QUEUE_NAME=heavy_queue
  volumes:
    - calls_audio:/back/app/calls
    - logs:/back/app/logs
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro

taskiq_scheduler:
  build: ../back/    # или image:
  env_file: ../.env
  container_name: taskiq_scheduler
  command: taskiq scheduler app.taskiq.broker:scheduler app.tasks.cleanup_tasks
  depends_on:
    - redis
    - postgresql
  restart: unless-stopped
  environment:
    - TASKIQ_QUEUE_NAME=cleanup_queue
  volumes:
    - logs:/back/app/logs
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
```

- Очередь передаётся через `environment.TASKIQ_QUEUE_NAME`, не через `.env`.
- Воркеры разных очередей — разные контейнеры с одним образом.
- AI-воркер (`ai_consumer_worker`) может собираться из отдельного сервиса (`../ai_consumer/`).

## nginx: шаблонный конфиг с env-подстановкой

Все nginx-образы строятся одинаково:

```dockerfile
FROM nginx:1.22.1
COPY nginx.conf /etc/nginx/templates/default.conf.template
```

Копирование в `templates/` включает автоматическую подстановку `${ENV_VAR}` при старте контейнера.

### gateway (API proxy)

```nginx
server {
  listen 80;
  server_tokens off;

  location /api/ {
    client_max_body_size 100M;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host $host:${HTTPS_PORT};

    resolver 127.0.0.11 valid=10s;
    set $backend backend;
    proxy_pass http://$backend:8000$request_uri;
  }
}
```

- `resolver 127.0.0.11` — Docker DNS; обязателен при использовании переменной (`set $backend`).
- Upstream через переменную (`set $backend backend`) — контейнер не падает при недоступном бэкенде на старте.
- `server_tokens off` — везде.

### audio_gateway (отдача аудио-файлов)

```nginx
server {
  listen 80;
  server_tokens off;

  root /usr/share/nginx/html;
  autoindex off;

  location ~ \.(mp3|wav|m4a|ogg|flac|webm|mp4)$ {
    add_header Content-Disposition inline;
    try_files $uri =404;
  }

  location / {
    return 404;
  }
}
```

- Монтирует volume с файлами как `:ro`.
- Все URL кроме аудио-расширений — 404.

### nginx_front (SPA)

```nginx
server {
  listen 80;
  server_tokens off;

  location / {
    alias /staticfiles/;
    try_files $uri $uri/ /index.html;
  }
}
```

## Compose-сервис gateway

```yaml
gateway:
  build: ./nginx       # или image:
  env_file: ../.env    # нужен для подстановки ${HTTPS_PORT} в шаблон
  container_name: gateway
  depends_on:
    - backend
  ports:
    - 8000:80
  restart: unless-stopped
  volumes:
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro

audio_gateway:
  build: ./nginx_audio   # или image:
  container_name: audio_gateway
  restart: unless-stopped
  ports:
    - "8001:80"          # prod: биндить на внутренний IP
  volumes:
    - calls_audio:/usr/share/nginx/html:ro
    - /etc/timezone:/etc/timezone:ro
    - /etc/localtime:/etc/localtime:ro
```

## Именование образов

Формат: `dockerhub_user/project_service:latest`

Примеры: `andreykobe/fcb_back:latest`, `andreykobe/fcb_nginx:latest`, `andreykobe/fcb_audio_nginx:latest`, `andreykobe/fcb_ai_consumer:latest`.

## Makefile-таргеты для инфры

Согласованы с конвенцией из `backend-makefile-api-tests`:

```make
# === Инфра (Docker) ===

db_local:
	cd infra && docker compose --env-file ../.env -f docker-compose.yml up postgresql

redis_local:
	cd infra && docker compose --env-file ../.env -f docker-compose.yml up redis

run_local_project:
	cd infra && docker compose --env-file ../.env -f docker-compose.local.yml up --build

down_local_project:
	cd infra && docker compose --env-file ../.env -f docker-compose.local.yml down

logs_local_project:
	cd infra && docker compose --env-file ../.env -f docker-compose.local.yml logs -f

build_image:
	docker build -t andreykobe/project_back:latest ./back
```

- Всегда `cd infra &&` перед docker compose — относительные пути в compose работают корректно.
- Передавать `--env-file ../.env` явно — compose не подхватывает `.env` из родительской папки автоматически.
- `down_local_project` не удаляет volumes (без `-v`) — данные сохраняются между перезапусками.

## Prod vs local: различия

| | local | production |
|---|---|---|
| Образы | `build: ../service/` | `image: user/project_service:latest` |
| Порты Redis/PG | открыты на `0.0.0.0` | биндятся на конкретный внутренний IP |
| `env_file` | `../.env` | `../.env` (тот же путь) |
| Volumes | одинаковые | одинаковые |
