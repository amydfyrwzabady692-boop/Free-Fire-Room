# معماری سیستم

## نقش‌ها و جریان اصلی

```mermaid
flowchart TD
  P[بازیکن] -->|/start یا Deep Link| TOS[پذیرش قوانین]
  TOS --> G[عضویت کانال‌های اجباری سراسری]
  G --> M[منوی اصلی]
  M --> L[فهرست کاستوم]
  L --> C[چک‌لیست شرایط]
  C --> R[ثبت‌نام با قفل ظرفیت]
  R --> W[Waitlist در صورت پر بودن]
  O[برگزارکننده] --> CH[اتصال و احراز کانال]
  CH --> WZ[ویزارد ساخت کاستوم]
  WZ --> AP[صف تأیید Super Admin]
  AP --> PUB[انتشار]
  SA[Super Admin] --> GC[کانال اجباری سراسری]
  SA --> BAN[Ban / تأیید / Broadcast]
```

## ارسال رمز اتاق

```mermaid
sequenceDiagram
  participant Beat as Celery Beat
  participant DB as PostgreSQL
  participant Redis as Redis Lock
  participant W as Worker
  participant TG as Telegram
  Beat->>DB: claim due jobs SKIP LOCKED
  W->>Redis: SET NX lock:creds:event:version
  W->>DB: load confirmed registrations
  loop each user
    W->>TG: getChatMember for required channels
    alt eligible and delivery not sent
      W->>TG: private message Room ID/Password
      W->>DB: deliveries unique idempotency_key SENT
    else already sent
      W-->>W: skip
    else left channel / banned
      W->>DB: skip + mark ineligible
    end
  end
```

## انتخاب‌های فنی

| انتخاب | دلیل |
|---|---|
| aiogram 3 | FSM رسمی، webhook/polling، سازگار با Python 3.12 |
| FastAPI | OpenAPI، async، همان دامنه سرویس‌ها با ربات |
| PostgreSQL | تراکنش، `SELECT FOR UPDATE SKIP LOCKED` برای ظرفیت و Job |
| Redis | Rate limit، lock توزیع‌شده، FSM، broker |
| Celery | Restart-safe، صف جدا، retry |
| Fernet | رمزنگاری متقارن reversible برای ارسال بعدی |
| Next.js | پنل RTL جدا از توکن ربات |

Jobهای زمان‌دار در جدول `scheduled_jobs` ذخیره می‌شوند نه فقط ETA سلری؛ Restart و چند Worker باعث ارسال دوباره نمی‌شود.
