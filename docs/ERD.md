# ERD و سیاست داده

همهٔ تاریخ‌ها UTC. نمایش با منطقهٔ زمانی کاربر (پیش‌فرض Asia/Tehran) و در UI امکان جلالی.

## جداول اصلی

- `users` 1—1 `user_profiles`
- `users` N—N `roles` از طریق `user_roles`
- `roles` N—N `permissions` از طریق `role_permissions`
- `users` 1—N `bans` (scope: bot | organize | participate)
- `users` 1—1 `organizers`
- `channels` N—N `users` از طریق `channel_ownerships`
- `global_required_channels` → `channels`
- `organizers` 1—N `events`
- `events` 1—N `event_prizes`, `event_requirements`, `event_required_channels`
- `events` 1—1 `room_credentials` (encrypted)
- `events` 1—N `registrations` (unique event_id+user_id)
- `registrations` 1—N `registration_requirement_statuses`
- `waitlist_entries`
- `referral_links` / `referrals` (unique event+invitee)
- `scheduled_jobs` (unique idempotency_key)
- `deliveries` (unique idempotency_key)
- `notifications` / `notification_preferences`
- `reports`
- `organizer_trust_events`
- `admins` / `admin_sessions`
- `audit_logs` (append-only)
- `system_settings` / `bot_contents`
- `broadcast_campaigns` / `broadcast_deliveries`

## ایندکس و محدودیت حیاتی

- `users.telegram_id` UNIQUE
- `events.public_token` UNIQUE (غیرترتیبی)
- یک ثبت‌نام فعال برای هر جفت کاربر/کاستوم
- `confirmed_count` فقط داخل تراکنش با `SELECT FOR UPDATE` زیاد می‌شود
- `referrals`: هر دعوت‌شونده در هر کاستوم فقط یک معرف
- `deliveries.idempotency_key` مثل `creds:{event}:{user}:{version}`

## نگهداری

| داده | سیاست |
|---|---|
| Room credentials | پاک/غیرقابل‌نمایش N روز پس از ارسال (پیش‌فرض ۷) |
| Audit | نگهداری طولانی؛ بدون UPDATE/DELETE از اپ |
| Soft delete | users, events, channels |
| حذف حساب | `deleted_at` + وضعیت deleted؛ لاگ امنیتی موقتاً می‌ماند |
