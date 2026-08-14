# استقرار Production

1. دامنه و TLS را آماده کنید.
2. `.env` را از `.env.example` بسازید؛ `APP_ENV=production`، `DEBUG=false`، `TELEGRAM_MODE=webhook`، `OPENAPI_ENABLED=false`.
3. `PUBLIC_BASE_URL=https://YOUR_DOMAIN`
4. کلید Fernet و `APP_SECRET_KEY` را یک‌بار بسازید و در secret store نگه دارید.
5. گواهی: مسیرهای `TLS_CERT_PATH` و `TLS_KEY_PATH`
6. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
7. `docker compose exec api python -m app.cli.main create-super-admin --telegram-id ID --password '...'`
8. Health: `/health/live` و `/health/ready`
9. Backup دوره‌ای: `python scripts/backup_encrypted.py` یا `pg_dump`
10. Rollback مهاجرت: `docker compose exec api alembic downgrade -1`

Worker و Beat باید همیشه جدا از API اجرا شوند (در compose آمده است).
