# فرض‌ها

کاربر در لحظهٔ ساخت، BOT_TOKEN، دامنهٔ نهایی، شناسهٔ کانال اصلی و سرور Production را نداده است.

فرض‌های منطقی (همه از `.env` قابل تغییر):

1. توسعه روی Docker Compose محلی با پورت 8080
2. Polling تا وقتی `TELEGRAM_MODE=webhook` نشود
3. منطقهٔ زمانی پیش‌فرض Asia/Tehran
4. تأیید کاستوم توسط Super Admin روشن است
5. سقف دعوت ۲۰ و سقف کاستوم فعال ۱۰
6. Super Admin فقط با CLI ساخته می‌شود

پس از دریافت دامنهٔ واقعی: `PUBLIC_BASE_URL`، گواهی TLS، و `TELEGRAM_MODE=webhook` را تنظیم کنید.
