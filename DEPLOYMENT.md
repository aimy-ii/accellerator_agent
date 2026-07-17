# Деплой в прод — как всё устроено на сервере

Этот файл — не про код агента (он в `README.md`), а про то, **где и как агент
крутится на реальном сервере** и **как до него достучаться**. Читай, если
нужно задеплоить новую версию, подключить прод-фронт или разобраться, почему
что-то не отвечает.

---

## 1. Общая идея

Код агента на сервер **не выкладывается**. Вместо этого:

1. Локально собирается Docker-образ (`langgraph build` — генерирует Dockerfile
   сам, на основе `langgraph.json` и графа, поверх `langchain/langgraph-api:3.12`).
2. Образ пушится в Docker Hub.
3. На сервере лежат только `.env` и `docker-compose.yml`, которые тянут этот
   образ (`image:`, без `build:`) и поднимают его.

```bash
# локально
uv run langgraph build -t andreykobe/accelerator_agent:latest
docker push andreykobe/accelerator_agent:latest

# на сервере
cd ~/accelerator_agent
docker compose pull
docker compose up -d
```

---

## 2. Сервер

- SSH: `ssh accelerator` (алиас уже настроен в `~/.ssh/config`).
- IP: `185.246.65.131`.
- На сервере крутится **несколько разных проектов** (не только этот), плюс
  ISPmanager — не удаляй и не трогай чужие контейнеры/конфиги без необходимости.
- Директория агента: `~/accelerator_agent/` (у root, `chmod 700`):
  ```
  ~/accelerator_agent/
    .env               # секреты + настройки (chmod 600)
    docker-compose.yml # только image:, без исходников
  ```
- Бэкенд акселератора (отдельный, самостоятельный деплой) лежит в
  `~/accelerator_back/internship_accelerator_back/` — это **другой** docker
  compose стек, с контейнерами `api_v2` (порт `8000`), `db_v2`, `redis_v2`,
  `worker_v2`. Сети агента и бэкенда **не общие** (два независимых
  `docker compose` проекта).

---

## 3. Как контейнеры агента устроены

`docker-compose.yml` на сервере — три сервиса, свой изолированный стейт:

| Сервис | Образ | Назначение | Порт |
|---|---|---|---|
| `langgraph-api` | `andreykobe/accelerator_agent:latest` | сам агент (LangGraph Server) | `127.0.0.1:18085` → `8000` в контейнере |
| `langgraph-postgres` | `pgvector/pgvector:pg16` | чекпоинты графа, стейт тредов | наружу не торчит |
| `langgraph-redis` | `redis:6` | очереди/pub-sub LangGraph Server | наружу не торчит |

Важно: у агента **своя** Postgres/Redis — не общие с бэкендом акселератора.
Хранится тут только состояние диалогов (треды, чекпоинты графа), не бизнес-данные.

### Как агент ходит в API акселератора

Агент и бэкенд — разные docker-сети → используем `host.docker.internal`:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

```env
API_BASE_URL=http://host.docker.internal:8000
AGENT_INTROSPECT_URL=http://host.docker.internal:8000/api/internal/auth/introspect
```

Бэкенд слушает `0.0.0.0:8000` на хосте (см. `api_v2` в его compose) — агент
достаёт этот порт через хост-гейтвей, как будто ходит на localhost.

### Аутентификация (s2s-токен)

`AGENT_INTROSPECT_TOKEN` в `.env` агента должен **побайтово совпадать** с
`AGENT_INTROSPECT_TOKEN` в `.env` бэкенда
(`~/accelerator_back/internship_accelerator_back/.env`). Это отдельный секрет
для прода, сгенерирован через `openssl rand -hex 32` — **не тот**, что в
локальном `.env.example`/деве (там плейсхолдер `changeme-...`).

Если меняешь токен — обнови **в обоих** местах и перезапусти оба сервиса:

```bash
# бэкенд
ssh accelerator "cd ~/accelerator_back/internship_accelerator_back/infra && \
  docker compose --env-file ../.env -f docker-compose.yml up -d --force-recreate api"

# агент
ssh accelerator "cd ~/accelerator_agent && docker compose up -d --force-recreate langgraph-api"
```

---

## 4. Как достучаться до агента снаружи

### Основной способ: путь на домене (HTTPS)

У акселератора есть реальный домен с уже выпущенным Let's Encrypt-сертификатом
— **`https://internship-accelerator.aimy.expert/`**. Он живёт в
`/etc/nginx/sites-enabled/default` и уже проксирует:

| Путь | Куда |
|---|---|
| `/` | фронтенд, `127.0.0.1:8686` |
| `/api/`, `/docs`, `/openapi.json` | бэкенд акселератора, `127.0.0.1:8000` |
| **`/agent/`** | **агент (этот репозиторий), `127.0.0.1:18085`** |

Добавленный блок (внутри того же `server { server_name internship-accelerator.aimy.expert; ... }`,
с SSL от Certbot):

```nginx
location /agent/ {
    client_max_body_size 25M;
    add_header X-Robots-Tag "noindex, nofollow";

    proxy_pass http://127.0.0.1:18085/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;

    proxy_connect_timeout 30s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;

    # LangGraph стримит ответы через SSE — буферизация/keep-alive-подмена
    # Connection ломают потоковую печать в чате.
    proxy_buffering off;
    proxy_set_header Connection "";
    chunked_transfer_encoding off;
}
```

Важно: `proxy_pass http://127.0.0.1:18085/;` — **со слэшем на конце**, поэтому
nginx срезает префикс `/agent/` (запрос `/agent/threads` долетает до агента
как `/threads`). Так что `apiUrl` для SDK — ровно
`https://internship-accelerator.aimy.expert/agent` (без хвостового слэша),
а дальше SDK сам добавляет `/threads`, `/runs/stream` и т.д.

После правки конфига — не `restart`, а `reload` (чтобы не рвать чужие
соединения на этом же nginx, там висит несколько проектов):

```bash
ssh accelerator "nginx -t && systemctl reload nginx"
```

Это и есть `apiUrl` для `@langchain/langgraph-sdk` / `useStream` на проде —
дальше по контракту как в
[`accellerator_agent_ui/agent-console/INTEGRATION.md`](../accellerator_agent_ui/agent-console/INTEGRATION.md)
(тот же самый агент, просто другой адрес вместо `localhost:8123`). Плюсы
такой схемы по сравнению с голым `IP:порт`: HTTPS "из коробки" (сертификат уже
есть у домена), и фронт с того же домена ходит **same-origin** — никаких
CORS-танцев на стороне браузера.

### Запасной способ: отдельный порт (без домена)

На сервере есть и старая практика — каждый сервис на своей паре портов через
системный nginx, `/etc/nginx/sites-available/*-port.conf`, публичный порт →
внутренний на `127.0.0.1`. Так подняты соседние проекты, и агент по инерции
тоже получил свою пару — это осталось как диагностический/резервный путь
(например, если домену/DNS/сертификату что-то не так), **но не как основной**:

| Проект | Публичный порт | Внутренний порт |
|---|---|---|
| metro-smk-doc-validator | `18080` | `18081` |
| kodik-transcriber | `18082` | `18083` |
| **accelerator_agent** | **`18084`** | **`18085`** |

Конфиг: `/etc/nginx/sites-available/accelerator-agent-port.conf`
(симлинк в `sites-enabled/`) — идентичный `location`-блок, только слушает
`18084` напрямую по HTTP, без домена/TLS:

```
http://185.246.65.131:18084
```

Используй это только для внутренних/тестовых обращений (curl с сервера, отладка) —
не для браузерного фронта (голый HTTP, никакого домена).

---

## 5. Быстрая диагностика

```bash
ssh accelerator

# статус контейнеров агента
docker ps --format '{{.Names}}\t{{.Status}}' | grep accelerator_agent

# логи
docker logs -f --tail 100 accelerator_agent-langgraph-api-1

# health (изнутри сервера)
curl -s http://127.0.0.1:18085/ok

# health снаружи, через домен (основной путь)
curl -s https://internship-accelerator.aimy.expert/agent/ok

# health снаружи, через запасной порт
curl -s http://185.246.65.131:18084/ok
```

Частые причины «не отвечает»:

- `401`/`403` на любой запрос с `Authorization: Bearer <JWT>` — истёк JWT
  заказчика ИЛИ `AGENT_INTROSPECT_TOKEN` разошёлся между `.env` агента и
  бэкенда (см. раздел 3).
- `503` от агента при авторизации — не достучался до бэкенда через
  `host.docker.internal:8000` (бэкенд не поднят / `api_v2` упал — проверяй его
  отдельно, это другой compose-проект).
- Пустой ответ / обрыв стрима через домен или `:18084`, но напрямую на
  `127.0.0.1:18085` всё ок — значит поломался nginx-конфиг
  (`proxy_buffering`/`Connection`) — проверь `nginx -t` и сам конфиг из
  раздела 4 (не забудь, что бэкапы старых версий `sites-enabled/default`
  должны лежать **вне** `sites-enabled/`, иначе nginx их тоже подхватит и
  выдаст `conflicting server name`).

---

## 6. Обновление версии агента

```bash
# 1. локально: собрать и запушить новый образ (тот же тег — latest)
cd accelerator_agent
uv run langgraph build -t andreykobe/accelerator_agent:latest
docker push andreykobe/accelerator_agent:latest

# 2. на сервере: подтянуть и пересоздать только agent-контейнер
ssh accelerator "cd ~/accelerator_agent && docker compose pull langgraph-api && \
  docker compose up -d --force-recreate langgraph-api"

# 3. проверить
ssh accelerator "curl -s http://127.0.0.1:18085/ok"
```

`langgraph-postgres`/`langgraph-redis` трогать не нужно — данные (треды,
чекпоинты) переживают пересоздание `langgraph-api`, т.к. живут в отдельных
контейнерах со своим named volume (`langgraph-data`).
