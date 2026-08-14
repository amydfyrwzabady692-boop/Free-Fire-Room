"""Encrypted logical backup helper. Does not print secrets."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    key = settings.backup_key
    if not key:
        raise SystemExit("BACKUP_ENCRYPTION_KEY or ROOM_CREDENTIALS_KEY required")
    out_dir = Path("backups")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw = out_dir / f"ffroom-{stamp}.sql"
    enc = out_dir / f"ffroom-{stamp}.sql.enc"
    subprocess.check_call(
        [
            "pg_dump",
            "-h",
            settings.postgres_host,
            "-U",
            settings.postgres_user,
            "-d",
            settings.postgres_db,
            "-f",
            str(raw),
        ]
    )
    data = raw.read_bytes()
    token = Fernet(key.encode() if isinstance(key, str) else key).encrypt(data)
    enc.write_bytes(token)
    raw.unlink()
    print(f"wrote {enc}")


if __name__ == "__main__":
    main()
