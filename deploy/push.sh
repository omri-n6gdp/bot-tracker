#!/usr/bin/env bash
# Runs ON YOUR MAC. Copies the tracker to the server and starts it there.
#   deploy/push.sh ubuntu@203.0.113.7 [~/Downloads/ssh-key.key]
# Run it again after any change (code, .env, deposits CSV) to update the server.
set -euo pipefail
cd "$(dirname "$0")/.."
host=${1:?"usage: deploy/push.sh user@server-ip [ssh-key-file]"}
ssh_cmd=(ssh -o StrictHostKeyChecking=accept-new)
if [ -n "${2:-}" ]; then chmod 600 "$2"; ssh_cmd+=(-i "$2"); fi
rsh="${ssh_cmd[*]}"
remote=bot-tracker
py=.venv/bin/python; [ -x "$py" ] || py=python3

echo "== Stopping the tracker on this Mac (the server takes over, so only one poller uses the API)"
"$py" -m tracker stop || true

echo "== Copying code, settings and keys"
"${ssh_cmd[@]}" "$host" "mkdir -p $remote/data"
rsync -az -e "$rsh" \
  --exclude '.venv/' --exclude 'data/' --exclude 'backups/' --exclude '_to_delete/' \
  --exclude 'Bot Tracker.app/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude '.hypothesis/' --exclude 'deploy/.env' --exclude 'deploy/.hosted' --exclude '.DS_Store' \
  ./ "$host:$remote/"

if ! "${ssh_cmd[@]}" "$host" "ls $remote/data/*.db >/dev/null 2>&1"; then
  echo "== First push: copying your history (data/*.db)"
  for db in data/*.db; do
    [ -e "$db" ] && "$py" -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" "$db"
  done
  rsync -az -e "$rsh" --exclude 'logs/' --exclude '*-wal' --exclude '*-shm' data/ "$host:$remote/data/"
fi

echo "$host" > deploy/.hosted   # tells the Dock button not to start a second copy here
echo "== Starting it on the server"
"${ssh_cmd[@]}" -t "$host" "cd $remote && bash deploy/setup.sh"
