# امنیت

## کنترل‌ها

- اسرار فقط از محیط؛ هیچ توکنی در کد نیست
- Room ID/Password و TOTP با Fernet
- لاگ ساخت‌یافته با رد کلیدهای حساس
- RBAC + بررسی سمت سرور؛ Frontend منبع اعتماد نیست
- نشست JWT کوتاه + جدول `admin_sessions` قابل لغو
- Super Admin: رمز Argon2 + TOTP اختیاری/قابل‌اجبار
- محدودیت تلاش ورود و قفل موقت
- Rate limit روی start / membership / register / referral / login
- CORS محدود، CSRF برای کوکی در صورت استفاده، XSS با متن کنترل‌شده تلگرام/React
- SQLAlchemy parameterized؛ بدون SQL خام کاربر
- SSRF: chat_ref فقط به Telegram API داده می‌شود نه fetch دلخواه URL
- حجم/نوع فایل بنر محدود
- OpenAPI در Production خاموش
- Metrics پشت Nginx deny
- Audit برای Ban، تأیید کاستوم، تغییر رمز، Broadcast
- Backup دیتابیس را جدا رمزنگاری کنید (`BACKUP_ENCRYPTION_KEY`)

## چک‌لیست پذیرش امنیت

- [ ] `.env` در git نیست
- [ ] `ROOM_CREDENTIALS_KEY` و `APP_SECRET_KEY` تصادفی‌اند
- [ ] HTTPS در Production
- [ ] `WEBHOOK_SECRET` با هدر تلگرام چک می‌شود
- [ ] Super Admin 2FA فعال است
- [ ] ربات در کانال‌های اجباری ادمین است
- [ ] تست ظرفیت و ارسال یک‌باره سبز است
- [ ] Ban کاربر در ربات و API فوری است
