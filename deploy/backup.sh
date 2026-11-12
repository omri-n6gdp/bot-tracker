#!/usr/bin/env bash
# Nightly copy of every tracker database into backups/ (setup.sh adds this to cron).
# Keeps 30 days. To keep copies off the server too, sync backups/ somewhere (e.g. rclone).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
stamp=$(date +%Y-%m-%d)
for db in data/*.db; do
  [ -e "$db" ] || continue
  name=$(basename "$db" .db)
  python3 - "$db" "backups/$name-$stamp.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
  gzip -f "backups/$name-$stamp.db"
  echo "backup ok: backups/$name-$stamp.db.gz"
done
find backups -name '*.db.gz' -mtime +30 -delete
