#!/bin/sh
set -eu

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "ÙØ§ÛŒÙ„ .env Ù†ÛŒØ³Øª. Ø¨Ø³Ø§Ø²ÛŒØ¯: cp .env.example .env"
  echo "Ø¨Ø¹Ø¯ BOT_TOKEN Ùˆ BOT_USERNAME Ùˆ BOOTSTRAP_SUPERADMIN_TELEGRAM_ID Ø±Ø§ Ù¾Ø± Ú©Ù†ÛŒØ¯."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Ù†ØµØ¨ Ù†ÛŒØ³Øª:"
  echo "  curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker \$USER"
  echo "Ø¨Ø¹Ø¯ ÛŒÚ©â€ŒØ¨Ø§Ø± logout/login Ùˆ Ø¯ÙˆØ¨Ø§Ø±Ù‡ ./deploy.sh"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Ù¾Ù„Ø§Ú¯ÛŒÙ† compose Ù„Ø§Ø²Ù… Ø§Ø³Øª: docker compose"
  exit 1
fi

py() {
  python3 -c "$1" 2>/dev/null || python -c "$1"
}

rand() {
  py "import secrets; print(secrets.token_urlsafe($1))"
}

fernet_key() {
  py "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
}

set_kv() {
  key="$1"
  val="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

get_kv() {
  grep -E "^${1}=" .env | cut -d= -f2- | tr -d '\r' || true
}

empty_or_placeholder() {
  v=$(get_kv "$1")
  case "$v" in
    ""|"change-me-to-a-64-char-random-string"|"change-me-webhook-secret"|"change-me-db-password"|"your_bot_username") return 0 ;;
    *) return 1 ;;
  esac
}

if empty_or_placeholder APP_SECRET_KEY; then
  set_kv APP_SECRET_KEY "$(rand 48)"
  echo "APP_SECRET_KEY Ø³Ø§Ø®ØªÙ‡ Ø´Ø¯."
fi
if empty_or_placeholder WEBHOOK_SECRET; then
  set_kv WEBHOOK_SECRET "$(rand 24)"
  echo "WEBHOOK_SECRET Ø³Ø§Ø®ØªÙ‡ Ø´Ø¯."
fi
if empty_or_placeholder POSTGRES_PASSWORD; then
  PW=$(rand 18)
  set_kv POSTGRES_PASSWORD "$PW"
  sed -i "s|change-me-db-password|${PW}|g" .env
  echo "POSTGRES_PASSWORD Ø³Ø§Ø®ØªÙ‡ Ø´Ø¯."
fi
if empty_or_placeholder ROOM_CREDENTIALS_KEY; then
  set_kv ROOM_CREDENTIALS_KEY "$(fernet_key)"
  echo "ROOM_CREDENTIALS_KEY Ø³Ø§Ø®ØªÙ‡ Ø´Ø¯."
fi
if empty_or_placeholder BOOTSTRAP_SUPERADMIN_PASSWORD; then
  AP=$(rand 12)
  set_kv BOOTSTRAP_SUPERADMIN_PASSWORD "$AP"
  echo
  echo "Ø±Ù…Ø² Ù¾Ù†Ù„ Super Admin (ÛŒÚ©â€ŒØ¨Ø§Ø± Ù†Ù…Ø§ÛŒØ´): ${AP}"
  echo "Ø§ÛŒÙ† Ø±Ø§ Ø°Ø®ÛŒØ±Ù‡ Ú©Ù†ÛŒØ¯."
  echo
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
if [ -n "$IP" ] && grep -q "localhost:8080" .env; then
  set_kv PUBLIC_BASE_URL "http://${IP}"
  set_kv API_BASE_URL "http://${IP}/api"
  set_kv FRONTEND_BASE_URL "http://${IP}"
  set_kv ALLOWED_ORIGINS "http://${IP},http://${IP}:80,http://${IP}:8080"
  echo "Ø¢Ø¯Ø±Ø³ Ø¹Ù…ÙˆÙ…ÛŒ Ø±ÙˆÛŒ IP Ø³Ø±ÙˆØ± ØªÙ†Ø¸ÛŒÙ… Ø´Ø¯: http://${IP}"
fi

TOKEN=$(get_kv BOT_TOKEN)
TG=$(get_kv BOOTSTRAP_SUPERADMIN_TELEGRAM_ID)
USERN=$(get_kv BOT_USERNAME)

if [ -z "$TOKEN" ]; then
  echo "BOT_TOKEN Ø®Ø§Ù„ÛŒ Ø§Ø³Øª. Ø¯Ø± .env Ø¨Ú¯Ø°Ø§Ø±ÛŒØ¯ Ùˆ Ø¯ÙˆØ¨Ø§Ø±Ù‡ ./deploy.sh"
  exit 1
fi
if [ -z "$TG" ]; then
  echo "BOOTSTRAP_SUPERADMIN_TELEGRAM_ID Ø®Ø§Ù„ÛŒ Ø§Ø³Øª. Ø´Ù†Ø§Ø³Ù‡ Ø¹Ø¯Ø¯ÛŒ ØªÙ„Ú¯Ø±Ø§Ù… Ø®ÙˆØ¯ Ø±Ø§ Ø¯Ø± .env Ø¨Ú¯Ø°Ø§Ø±ÛŒØ¯."
  exit 1
fi
if empty_or_placeholder BOT_USERNAME; then
  echo "BOT_USERNAME Ø±Ø§ Ø¨Ø¯ÙˆÙ† @ Ø¯Ø± .env Ø¨Ú¯Ø°Ø§Ø±ÛŒØ¯."
  exit 1
fi

COMPOSE_FILES="-f docker-compose.yml"
if grep -qE '^APP_ENV=production' .env 2>/dev/null; then
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.prod.yml"
fi
if [ "${DEPLOY_MINIMAL:-1}" = "1" ]; then
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.minimal.yml"
  UP_SERVICES="postgres redis migrate bot"
  LOG_SERVICE="bot"
  echo "حالت سبک: postgres + redis + bot (بدون api/panel)"
else
  UP_SERVICES=""
  LOG_SERVICE="api"
  COMPOSE_PROFILES="--profile panel"
  echo "حالت کامل: postgres + redis + api + frontend + nginx"
fi

echo "در حال build و اجرا..."
if ! docker compose $COMPOSE_FILES ${COMPOSE_PROFILES:-} up -d --build --remove-orphans $UP_SERVICES; then
  echo
  echo "deploy failed — migrate logs:"
  docker compose $COMPOSE_FILES logs migrate --tail 200 || true
  echo
  echo "api logs:"
  docker compose $COMPOSE_FILES logs api --tail 80 || true
  echo
  echo "bot logs:"
  docker compose $COMPOSE_FILES logs bot --tail 80 || true
  echo
  echo "container status:"
  docker compose $COMPOSE_FILES ps -a || true
  exit 1
fi

echo
echo "ØªÙ…Ø§Ù…. Ù¾Ù†Ù„: http://${IP:-SERVER}"
echo "ÙˆØ±ÙˆØ¯ Ù¾Ù†Ù„ Ø¨Ø§ Telegram ID = ${TG} Ùˆ Ø±Ù…Ø² BOOTSTRAP_SUPERADMIN_PASSWORD Ø¯Ø§Ø®Ù„ .env"
echo "Ø±Ø¨Ø§Øª Ø±Ø§ ÛŒÚ©â€ŒØ¨Ø§Ø± /start Ú©Ù†ÛŒØ¯ Ùˆ Ø±Ø¨Ø§Øª Ø±Ø§ Ø¯Ø± Ú©Ø§Ù†Ø§Ù„ Ø§ØµÙ„ÛŒ Ø§Ø¯Ù…ÛŒÙ† Ú©Ù†ÛŒØ¯."
echo "لاگ: docker compose $COMPOSE_FILES logs -f ${LOG_SERVICE:-api}"
echo "پنل کامل: DEPLOY_MINIMAL=0 ./deploy.sh"
