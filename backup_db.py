"""Create a consistent SQLite backup and archive attachment files."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import DB_PATH, UPLOAD_DIR  # noqa: E402

backup_root = DB_PATH.parent / "backups"
backup_root.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
destination = backup_root / f"hostel_bot_{stamp}.sqlite3"
if not DB_PATH.exists():
    raise SystemExit(f"Database does not exist yet: {DB_PATH}")

source = sqlite3.connect(DB_PATH)
target = sqlite3.connect(destination)
try:
    source.backup(target)
finally:
    target.close()
    source.close()

print(f"Database backup written: {destination}")
if UPLOAD_DIR.exists():
    archive = shutil.make_archive(str(backup_root / f"uploads_{stamp}"), "zip", UPLOAD_DIR)
    print(f"Attachment backup written: {archive}")
