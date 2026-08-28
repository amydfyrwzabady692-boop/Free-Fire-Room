from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.security import encrypt_secret, generate_unguessable_token, hash_password
from app.core.session import SessionLocal, SyncSessionLocal
from app.models.admin import Admin
from app.models.user import Permission, Role, RolePermission, User, UserRole
from app.services.users import get_by_telegram


PERMISSIONS = [
    ("admin.dashboard", "داشبورد"),
    ("admin.users", "کاربران"),
    ("admin.organizers", "برگزارکنندگان"),
    ("admin.events", "کاستوم‌ها"),
    ("admin.channels", "کانال‌های اجباری"),
    ("admin.content", "محتوا"),
    ("admin.broadcasts", "ارسال همگانی"),
    ("admin.reports", "گزارش تخلف"),
    ("admin.audit", "لاگ حسابرسی"),
    ("admin.settings", "تنظیمات"),
]

ROLES = {
    "super_admin": [p[0] for p in PERMISSIONS],
    "admin": [p[0] for p in PERMISSIONS if p[0] != "admin.settings"],
    "moderator": ["admin.dashboard", "admin.users", "admin.reports", "admin.events"],
    "organizer": [],
    "player": [],
}


async def seed() -> None:
    async with SessionLocal() as db:
        perms = {}
        for code, desc in PERMISSIONS:
            row = await db.scalar(select(Permission).where(Permission.code == code))
            if not row:
                row = Permission(code=code, description=desc)
                db.add(row)
                await db.flush()
            perms[code] = row
        for name, codes in ROLES.items():
            role = await db.scalar(select(Role).where(Role.name == name))
            if not role:
                role = Role(name=name, description=name)
                db.add(role)
                await db.flush()
            for code in codes:
                exists = await db.scalar(
                    select(RolePermission).where(
                        RolePermission.role_id == role.id, RolePermission.permission_id == perms[code].id
                    )
                )
                if not exists:
                    db.add(RolePermission(role_id=role.id, permission_id=perms[code].id))
        from app.models.admin import BotContent, SystemSetting
        from app.locales.fa import TOS, HELP, PRIVACY, DISCLAIMER

        for key, body in {"welcome": TOS, "help": HELP, "privacy": PRIVACY, "disclaimer": DISCLAIMER}.items():
            exists = await db.scalar(select(BotContent).where(BotContent.key == key))
            if not exists:
                db.add(BotContent(key=key, locale="fa", body=body))
        await db.commit()
        print("seed ok")


async def create_super_admin(telegram_id: int, password: str, *, seed_first: bool = True) -> None:
    if seed_first:
        await seed()
    async with SessionLocal() as db:
        user = await get_by_telegram(db, telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, first_name="Super Admin", language="fa", timezone="Asia/Tehran")
            db.add(user)
            await db.flush()
        role = await db.scalar(select(Role).where(Role.name == "super_admin"))
        if role:
            exists = await db.scalar(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
            if not exists:
                db.add(UserRole(user_id=user.id, role_id=role.id))
        admin = await db.scalar(select(Admin).where(Admin.user_id == user.id))
        if admin is None:
            admin = Admin(user_id=user.id, is_super_admin=True, is_active=True)
            db.add(admin)
        admin.password_hash = hash_password(password)
        admin.is_super_admin = True
        admin.is_active = True
        await db.commit()
        print(f"super admin ready for telegram_id={telegram_id}")


def gen_key() -> None:
    from cryptography.fernet import Fernet

    print(Fernet.generate_key().decode())


async def bootstrap_from_env() -> None:
    from app.core.config import get_settings

    await seed()
    settings = get_settings()
    tg = settings.bootstrap_superadmin_telegram_id
    password = settings.bootstrap_superadmin_password
    if not tg or not password:
        print("bootstrap: seed done; super admin skipped (set BOOTSTRAP_SUPERADMIN_TELEGRAM_ID and BOOTSTRAP_SUPERADMIN_PASSWORD)")
        return
    await create_super_admin(int(tg), password, seed_first=False)
    print("bootstrap: super admin ensured from env")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ffroom")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    p = sub.add_parser("create-super-admin")
    p.add_argument("--telegram-id", type=int, required=True)
    p.add_argument("--password", required=True)
    sub.add_parser("gen-key")
    sub.add_parser("bootstrap")
    args = parser.parse_args()
    if args.cmd == "seed":
        asyncio.run(seed())
    elif args.cmd == "create-super-admin":
        asyncio.run(create_super_admin(args.telegram_id, args.password))
    elif args.cmd == "bootstrap":
        asyncio.run(bootstrap_from_env())
    elif args.cmd == "gen-key":
        gen_key()
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
