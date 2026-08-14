# Free Fire Room

Multi-tenant Telegram platform for prize custom-room events. Python 3.12, aiogram 3, FastAPI, PostgreSQL, Redis, Celery, Next.js, Docker.

Not affiliated with Garena / Free Fire.

## Quick start

```bash
cp .env.example .env
# set BOT_TOKEN, BOT_USERNAME, APP_SECRET_KEY, POSTGRES_PASSWORD
# ROOM_CREDENTIALS_KEY: python -m app.cli.main gen-key
docker compose up --build
docker compose exec api python -m app.cli.main create-super-admin --telegram-id ID --password '...'
```

Panel: `http://localhost:3000`  
API docs (non-prod): `http://localhost:8080/api/docs`

Tests: `docker compose exec api pytest -q`

See the Persian README for the full runbook, security notes, and MVP checklist.
