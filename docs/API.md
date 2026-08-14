# OpenAPI / API map

Base: `/api`

| Method | Path | Permission |
|---|---|---|
| POST | /auth/login/password | public (admin password+2FA) |
| POST | /auth/login/otp | public |
| POST | /auth/login/telegram | public |
| POST | /auth/otp/request | public (no user enumeration leak beyond generic ok) |
| GET | /auth/me | auth |
| PATCH | /users/me | auth |
| POST | /users/me/delete | auth |
| GET | /events | public list |
| GET | /events/{token} | public detail (no secrets) |
| POST | /events | organizer |
| POST | /events/{id}/submit | organizer owner |
| POST | /events/{id}/cancel | organizer owner |
| PUT | /events/{id}/credentials | organizer owner |
| POST | /registrations/{token} | player |
| GET | /organizers/me | organizer |
| GET | /organizers/me/events | organizer |
| GET | /organizers/me/events/{id}/participants | organizer owner |
| GET | /organizers/me/events/{id}/deliveries | organizer owner |
| GET | /organizers/me/events/{id}/export | organizer owner |
| POST | /channels/connect | auth |
| POST | /reports | auth |
| GET | /admin/dashboard | admin.dashboard |
| GET/POST | /admin/users... | admin.users |
| GET/POST | /admin/events... | admin.events |
| GET/POST | /admin/organizers... | admin.organizers |
| GET/POST | /admin/global-channels... | admin.channels |
| GET | /admin/audit | admin.audit |
| POST | /admin/broadcasts... | admin.broadcasts |
| GET/PUT | /admin/settings | super_admin |

خطاها: `{code, message, details}` با HTTP 4xx/5xx.
در Production، `/api/docs` غیرفعال است.
