# Free Fire Room

سامانهٔ چندمستاجری برای مدیریت و اطلاع‌رسانی کاستوم‌های جایزه‌دار Free Fire. شامل ربات تلگرام، API، پنل برگزارکننده، پنل Super Admin، زمان‌بندی ارسال رمز اتاق، و Audit Log.

**این محصول شریک رسمی Garena / Free Fire نیست.** برگزارکننده مسئول قانونی بودن مسابقه و تحویل جایزه است. نسخهٔ اول خرید اجباری، شرط‌بندی یا پرداخت ندارد.

## فرض‌های قابل‌تغییر

اگر هنگام راه‌اندازی مقدار واقعی ندادید، این پیش‌فرض‌ها استفاده می‌شود:

| مورد | فرض |
|---|---|
| دامنه | `http://localhost:8080` (توسعه) |
| حالت تلگرام | `polling` در توسعه، `webhook` در تولید |
| منطقه زمانی پیش‌فرض | `Asia/Tehran` (ذخیرهٔ تاریخ‌ها همیشه UTC) |
| تأیید کاستوم | لازم است مگر در تنظیمات خاموش شود |
| استقرار | Docker Compose + Nginx |
| Super Admin | ساخته می‌شود با CLI، نه با مقدار سخت‌کد |

توکن ربات، کلید رمزنگاری و رمز دیتابیس **فقط** از متغیر محیطی خوانده می‌شوند.

## یک دستور روی VPS

روی سرور (اوبونتو + Docker):

```bash
git clone https://github.com/amydfyrwzabady692-boop/Free-Fire-Room.git
cd Free-Fire-Room
cp .env.example .env
nano .env   # حداقل این سه تا را پر کن
chmod +x deploy.sh
./deploy.sh
```

حداقل فیلدهای `.env`:

```env
BOT_TOKEN=123456:ABC...
BOT_USERNAME=YourBotUsername
BOOTSTRAP_SUPERADMIN_TELEGRAM_ID=123456789
```

`./deploy.sh` بقیهٔ کلیدها را می‌سازد، دیتابیس را migrate می‌کند، Super Admin را می‌سازد و همهٔ کانتینرها را بالا می‌آورد.

پنل: `http://IP-SERVER`  
ورود: همان شناسه تلگرام + رمز که اسکریپت چاپ می‌کند (یا `BOOTSTRAP_SUPERADMIN_PASSWORD` در `.env`).

بعد از اولین اجرا، ربات را در تلگرام `/start` کنید و در کانال اصلی **ادمین** کنید.

## پوش روی GitHub

روی ویندوز، در پوشه پروژه (هرگز `.env` را commit نکن):

```powershell
cd "C:\Users\Atomic\Desktop\FREE FIRE ROOM"
git add .
git status
git commit -m "Initial production-ready Free Fire Room platform"
```

در github.com یک ریپوی خالی بساز (بدون README)، بعد:

```powershell
git remote add origin https://github.com/USER/REPO.git
git branch -M main
git push -u origin main
```

اگر `gh` لاگین است:

```powershell
gh repo create FREE-FIRE-ROOM --private --source=. --remote=origin --push
```

`.env` داخل `.gitignore` است و نباید پوش شود. روی VPS فقط `.env` را جدا می‌سازی.

## معماری (خلاصه)

```
Telegram ── webhook/polling ──► Bot (aiogram 3) ──┐
پنل Next.js ── HTTPS/Nginx ──► FastAPI ──────────┼── PostgreSQL
Celery worker/beat ◄── Redis broker/lock/cache ──┘
```

- **FastAPI**: API، وب‌هوک، احراز هویت، RBAC
- **aiogram 3**: منوی بازیکن، ویزارد برگزارکننده، Deep Link
- **PostgreSQL**: منبع حقیقت + قفل ردیف برای ظرفیت
- **Redis**: FSM، Rate Limit، Distributed Lock، صف Celery
- **Celery Beat**: هر ۵ ثانیه Jobهای سررسیدشده را با `FOR UPDATE SKIP LOCKED` برمی‌دارد (Idempotent)
- **Fernet**: رمزنگاری Room ID / Password / TOTP secret

دلیل انتخاب Celery به‌جای فقط APScheduler: چند Worker، Restart امن، Retry و صف جدا برای broadcast.

جزئیات: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) و [docs/ERD.md](docs/ERD.md)

## راه‌اندازی سریع (توسعه)

پیش‌نیاز: Docker Desktop، یک ربات از ‎@BotFather، و ربات را در کانال اصلی **ادمین** کنید.

```powershell
cd "C:\Users\Atomic\Desktop\FREE FIRE ROOM"
copy .env.example .env
```

در `.env` حداقل این‌ها را پر کنید:

- `BOT_TOKEN`
- `BOT_USERNAME`
- `POSTGRES_PASSWORD`
- `APP_SECRET_KEY` (رشته تصادفی بلند)
- `ROOM_CREDENTIALS_KEY` :

```powershell
docker compose run --rm api python -m app.cli.main gen-key
```

سپس:

```powershell
docker compose up --build
```

سرویس‌ها:

- پنل: http://localhost:3000
- API از طریق Nginx: http://localhost:8080/api
- OpenAPI (فقط غیر Production): http://localhost:8080/api/docs
- Health: http://localhost:8080/health/live

ساخت Super Admin (بعد از بالا آمدن دیتابیس):

```powershell
docker compose exec api python -m app.cli.main create-super-admin --telegram-id YOUR_TG_ID --password "StrongPass!234"
```

با `/start` در ربات، همان اکانت را یک‌بار استارت کنید تا پروفایل تلگرام ذخیره شود، بعد با Telegram ID و رمز وارد پنل شوید. 2FA را از مسیر مدیریت فعال کنید.

## مسیر پذیرش MVP

1. Super Admin از پنل «کانال‌های اجباری» کانال اصلی را اضافه کند (ربات باید ادمین باشد).
2. برگزارکننده کانال خود را وصل کند و مالکیت تأیید شود.
3. کاستوم با زمان، ظرفیت، کانال اجباری و دعوت لازم ساخته شود.
4. مدیر کاستوم را تأیید کند.
5. بازیکن از Deep Link `https://t.me/BOT?start=event_TOKEN` وارد شود.
6. عضویت اجباری و Referral بررسی شود و ثبت‌نام از ظرفیت عبور نکند.
7. در زمان مقرر فقط واجدین شرایط Room ID/Password را **یک‌بار** در خصوصی بگیرند.
8. لغو/تغییر زمان Jobها را به‌روز کند؛ Ban فوری اعمال شود.

## تست

```powershell
docker compose exec api pytest -q
```

یا محلی (Python 3.12):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
$env:ROOM_CREDENTIALS_KEY = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
pytest -q
```

## Production

1. `.env` با `APP_ENV=production`، `DEBUG=false`، `TELEGRAM_MODE=webhook`، `OPENAPI_ENABLED=false`
2. `PUBLIC_BASE_URL=https://your-domain`
3. گواهی TLS را در مسیر Nginx قرار دهید
4. `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
5. وب‌هوک: کانتینر `bot` در حالت webhook فقط webhook را ثبت می‌کند؛ آپدیت‌ها به FastAPI می‌رسند

Backup:

```powershell
docker compose exec postgres pg_dump -U ffroom ffroom > backup.sql
```

بازگردانی: `psql` روی فایل دامپ. Rollback مهاجرت: `alembic downgrade -1`

## امنیت و محدودیت تلگرام

مستند کامل: [docs/SECURITY.md](docs/SECURITY.md) و [docs/TELEGRAM_LIMITS.md](docs/TELEGRAM_LIMITS.md)

نکتهٔ مهم: فوروارد بنر به N دوست **قابل اثبات قطعی نیست**. شرط دعوت با Referral Link پیاده شده است. اسکرین‌شات/بازنشر رمز را تلگرام تضمینی مسدود نمی‌کند.

## ساختار پوشه

```
backend/app/{api,bot,core,models,services,workers,cli}
frontend/src/app/{admin,organizer,login}
nginx/
docs/
```

## نسخه بعد

تقویم، دنبال‌کردن برگزارکننده، مدرک پرداخت جایزه، PWA، تیکت پشتیبانی، Funnel. فهرست: [docs/NEXT_VERSION.md](docs/NEXT_VERSION.md)
