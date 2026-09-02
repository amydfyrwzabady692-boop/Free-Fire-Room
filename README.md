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

## پنل مالک ربات (داخل تلگرام)

`/admin` یا دکمهٔ «پنل مالک ربات». همهٔ بخش‌ها **صفحه‌بندی‌شده** و روی یک پیام ویرایش می‌شوند
(قبلاً هر بخش ۱۰ تا ۲۰ پیام جدا می‌فرستاد و به سقف ارسال تلگرام می‌خورد).

| بخش | کاری که می‌کند |
|---|---|
| داشبورد | آمار کل + فهرست «نیاز به رسیدگی» |
| کاستوم‌های در انتظار | تأیید / رد، با اطلاع خودکار به برگزارکننده |
| برگزارکنندگان | مرتب‌شده بر اساس منتظر تأیید و سپس کم‌اعتبارترین |
| جستجوی کاربر | پروندهٔ کامل: آمار، بن، سابقهٔ اعتبار |
| کانال اجباری | افزودن، روشن/خاموش، **حذف** |
| گزارش تخلف | تأیید تخلف (کسر اعتبار)، بن برگزاری، بستن |
| برنده‌ها | دیدن اسکرین‌شات، تأیید یا رد ادعا |
| همه کاستوم‌ها | برای هر کاستوم: **قیف کامل** و لغو اضطراری |

## امتیاز اعتبار برگزارکننده

جدول `organizer_trust_events` و ستون `organizers.trust_score` از ابتدا وجود داشتند ولی
هیچ‌جا نوشته نمی‌شدند. حالا هر رویداد امتیاز را جابه‌جا می‌کند و یک ردیف قابل حسابرسی
با دلیلش ثبت می‌شود:

| رویداد | تغییر |
|---|---|
| ارسال موفق ROOM ID / PASS | +۶ |
| نفرستادن مشخصات در مهلت | −۱۸ |
| گزارش تأییدشدهٔ «جایزه نداد» | −۱۴ |
| گزارش تخلف تأییدشده | −۱۰ |
| تأیید پرداخت جایزه | +۴ |

امتیاز بین ۰ تا ۱۰۰ محدود می‌شود و زیر ۳۰ به بازیکن **هشدار** نشان داده می‌شود.

## زمان کاستوم دست برگزارکننده است

هیچ‌چیز کاستوم را ساعت نمی‌بندد. کاستوم تا وقتی در «کاستوم‌های پیش‌رو» می‌ماند و
ثبت‌نام و ارسال ROOM ID / PASS باز است که برگزارکننده خودش دکمهٔ
**«کاستوم شروع شد — انتقال به گذشته»** را در «کاستوم‌ها و آمار من» بزند.

| قبل | حالا |
|---|---|
| ساعت شروع باید حداقل ۱۰ دقیقه بعد باشد | هر ساعتی از الان به بعد |
| مهلت ارسال مشخصات: ۵ دقیقه بعد از شروع | تا وقتی «کاستوم شروع شد» زده نشود |
| پر شدن کاستوم: ۲۰ دقیقه بعد از شروع | تا وقتی «کاستوم شروع شد» زده نشود |
| بعد از ساعت شروع خودکار به «گذشته» می‌رفت | فقط با دکمهٔ برگزارکننده |
| ظرفیت ۱۰۰ نفر | بدون محدودیت |

ستون `events.archived_at` (مهاجرت `0008`) تنها معیار «گذشته» است. تنها استثنا
`AUTO_ARCHIVE_HOURS` (پیش‌فرض ۱۲ ساعت) است: پشتیبان کسی که هیچ‌وقت دکمه را نمی‌زند،
تا کاستومی برای همیشه در فهرست نماند.

ظرفیت `0` یعنی نامحدود؛ کاستوم‌های ساخته‌شده در ربات همین‌طورند و کاستوم‌های زندهٔ
قبلی هم در مهاجرت `0008` به همین حالت منتقل می‌شوند.

## فالو اینستاگرام / یوتیوب (اختیاری)

برگزارکننده هنگام ثبت کاستوم می‌تواند آدرس پیج بدهد یا «رد کردن» را بزند. اگر بدهد،
این **مرحلهٔ آخر بازیکن بعد از جوین کانال‌های اجباری** می‌شود:

```
جوین کانال‌ها → دکمهٔ «عضو شدم» → ارسال اسکرین فالو → تأیید برگزارکننده → ثبت‌نام قطعی
```

اسکرین در `social_proofs` ذخیره و برای برگزارکننده (و اگر نبود، برای مالک ربات) با دکمهٔ
«تأیید ثبت‌نام / رد» فرستاده می‌شود. تا تأیید نشود ثبت‌نام `pending` می‌ماند و ROOM ID /
PASS هم برای آن بازیکن نمی‌رود — این شرط هم هنگام ثبت‌نام و هم دوباره لحظهٔ ارسال
مشخصات بررسی می‌شود.

## برنده، آیدی جایزه و گفت‌وگوی دوطرفه

- برگزارکننده هنگام ثبت کاستوم یک **آیدی دریافت جایزه** می‌دهد (`@my_id`). دفعهٔ بعد
  با یک دکمه همان را انتخاب می‌کند، یا از «آیدی دریافت جایزه» در پنل عوضش می‌کند.
- اسکرین ادعای برنده با دکمه‌های **تأیید برنده / رد / پیام به برنده** برای برگزارکننده
  و مالک ربات می‌رود؛ آیدی و یوزرنیم بازیکن هم کنارش هست تا بشود مستقیم پی‌وی داد.
- با تأیید، ربات آیدی دریافت جایزه را برای برنده می‌فرستد به‌همراه دکمهٔ رفتن به پی‌وی.
- هر دو طرف می‌توانند **داخل ربات** جواب بدهند: «پیام به برنده» و «پاسخ به برگزارکننده».
  هر پیام در `winner_messages` ثبت می‌شود.

## قیف هر کاستوم

`event_views` (مهاجرت `0007`) بالای قیف را ثبت می‌کند — چیزی که قبلاً اصلاً دیده نمی‌شد:

```
کارت را دیدند → وارد ثبت‌نام شدند → شرایط را کامل کردند → مشخصات را گرفتند
```

برگزارکننده از «قیف و آمار» و مالک ربات از کارت هر کاستوم می‌بیند، به‌همراه یک جملهٔ
پیشنهاد دربارهٔ بزرگ‌ترین محل ریزش. خروجی CSV شرکت‌کننده‌ها هم از همان‌جا گرفته می‌شود.

## اجرا در کنار ربات‌های دیگر روی یک VPS

حالت سبک (`--profile bot`) عمداً طوری تنظیم شده که به بقیهٔ سرور کاری نداشته باشد:

| ویژگی | مقدار |
|---|---|
| پورت باز روی هاست | **هیچ** — ربات فقط polling می‌زند |
| سقف حافظه | bot 512m + postgres 64m + redis 20m ≈ **۶۰۰ مگابایت** |
| سقف CPU ربات | ۱ هسته |
| لاگ هر کانتینر | حداکثر ۳ فایل × ۱۰ مگابایت |

لاگ‌ها سقف دارند چون درایور پیش‌فرض داکر هیچ‌وقت rotate نمی‌کند؛ روی سروری که
چند stack دارد، یک لاگ بی‌سقف دیسک را پر می‌کند و **همهٔ** کانتینرها با هم
می‌خوابند.

دستور استقرار:

```bash
cd /opt/Free-Fire-Room
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.minimal.yml --profile bot up -d --build migrate bot
```

`--build migrate bot` نه فقط `bot`: مهاجرت دیتابیس داخل سرویس `migrate` اجرا
می‌شود و اگر image آن rebuild نشود، از نسخهٔ کش‌شدهٔ قدیمی اجرا می‌شود و
مهاجرت‌های جدید اعمال نمی‌شوند.

اگر ربات مدام ری‌استارت شد و در لاگ فقط `bot_step=1` و `bot_step=2` تکرار شد
بدون هیچ traceback، یعنی کرنل کشته‌اش — سقف حافظه کم است:

```bash
docker inspect free-fire-room-bot-1 --format 'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}'
```

`OOMKilled=true` یا `ExitCode=137` یعنی `mem_limit` سرویس `bot` را باید بالاتر برد.

**دستورهایی که روی این سرور نباید بزنید:**

| دستور | چرا |
|---|---|
| `docker system prune -a` | image و شبکهٔ ربات‌های دیگر را هم پاک می‌کند |
| `docker compose down -v` | volume دیتابیس را پاک می‌کند — کل داده‌ها می‌رود |
| `--remove-orphans` | کانتینرهایی را که برچسب همین پروژه را دارند ولی در فایل نیستند حذف می‌کند |

برای دیدن اینکه فقط کانتینرهای همین پروژه دست خورده‌اند:

```bash
docker compose -f docker-compose.yml -f docker-compose.minimal.yml ps
docker ps --format 'table {{.Names}}	{{.Status}}'
```

اگر روزی پنل وب را هم بالا آوردید و پورت ۸۰ سرور قبلاً گرفته است، در `.env`
مقدار `HTTP_PORT` را عوض کنید (مثلاً `HTTP_PORT=8080`) تا با سایت موجود
تداخل نکند.

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
3. کاستوم با زمان، کانال اجباری، آیدی دریافت جایزه و در صورت تمایل شرط فالو ساخته شود (ظرفیت نامحدود).
4. مدیر کاستوم را تأیید کند.
5. بازیکن از Deep Link `https://t.me/BOT?start=event_TOKEN` وارد شود.
6. عضویت اجباری بررسی شود و اگر شرط فالو فعال باشد، تا تأیید اسکرین ثبت‌نام قطعی نشود.
7. در زمان مقرر فقط واجدین شرایط Room ID/Password را **یک‌بار** در خصوصی بگیرند.
8. لغو/تغییر زمان Jobها را به‌روز کند؛ Ban فوری اعمال شود.
9. برگزارکننده «کاستوم شروع شد» را بزند و کاستوم به «گذشته» برود.
10. برنده اسکرین بفرستد، برگزارکننده تأیید کند و آیدی دریافت جایزه برایش ارسال شود.

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
