#!/usr/bin/env bash
# Runs ON THE SERVER (push.sh calls it for you). Safe to run again at any time:
# installs Docker if needed, adds swap on small machines, then (re)starts the tracker.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "== Installing Docker"
  # Oracle's network doesn't route IPv6 by default, which can make apt hang forever.
  [ -d /etc/apt/apt.conf.d ] && echo 'Acquire::ForceIPv4 "true";' | sudo tee /etc/apt/apt.conf.d/99force-ipv4 >/dev/null
  # A brand-new Ubuntu server runs its own updates for the first few minutes; wait for them.
  for i in $(seq 1 120); do
    sudo fuser /var/lib/apt/lists/lock /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break
    [ "$i" = 1 ] && echo "   (waiting for the server's first-boot updates to finish...)"
    sleep 5
  done
  curl -fsSL https://get.docker.com | sudo sh
fi

# 1 GB machines (Google's free e2-micro, Oracle's AMD micro) need swap for pandas/yfinance.
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 2000000 ] && ! sudo swapon --show | grep -q .; then
  echo "== Adding 2 GB of swap"
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

[ -f .env ] || { echo "No .env here. Run deploy/push.sh from your Mac first."; exit 1; }
[ -f "API Keys" ] || { echo "No 'API Keys' file here. Run deploy/push.sh from your Mac first."; exit 1; }
mkdir -p data/logs backups

# Settings for docker compose itself (not the tracker): run as this user, and the tunnel token.
token=$(grep -E '^TUNNEL_TOKEN=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true)
umask 077
printf 'HOST_UID=%s\nHOST_GID=%s\nTUNNEL_TOKEN=%s\n' "$(id -u)" "$(id -g)" "$token" > deploy/.env

compose="sudo docker compose -f deploy/docker-compose.yml"
if [ -n "$token" ]; then services="poller web tunnel"; else services="poller web"; fi
echo "== Starting: $services"
$compose up -d --build --remove-orphans $services
$compose restart poller web >/dev/null   # pick up code changes (code is mounted, not baked in)
[ -n "$token" ] || $compose rm -sf tunnel >/dev/null 2>&1 || true

# Nightly database backup at 03:15
line="15 3 * * * cd $PWD && deploy/backup.sh >> data/logs/backup.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'deploy/backup.sh' ; echo "$line" ) | crontab -

sleep 6
if $compose exec -T web python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)" 2>/dev/null; then
  echo "== Website is up inside the server."
else
  echo "== Website isn't answering yet. Check: $compose logs --tail 50 web"
fi
if [ -z "$token" ]; then
  echo "   No TUNNEL_TOKEN in .env yet, so it isn't reachable from outside. Add it, then run push.sh again."
fi
echo "   Poller log: $compose logs -f poller"
