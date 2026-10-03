#!/bin/bash
# Build and deploy AR Portfolio Bot on this server.
# Run from anywhere as the admin user (needs sudo):  /opt/arportfoliobot/deploy.sh
# Steps: install dependencies, run tests, build the frontend, apply database
# migrations, install service/nginx/backup config, restart, and check health.
set -euo pipefail

ROOT=/opt/arportfoliobot
ENV_FILE=/etc/arportfoliobot/arportfoliobot.env
WEB_ROOT=/var/www/arportfoliobot
cd "$ROOT"

step() { printf '\n==> %s\n' "$*"; }

step "Backend dependencies"
[ -x backend/.venv/bin/python ] || python3 -m venv backend/.venv
backend/.venv/bin/pip install -q -r backend/requirements-dev.txt

step "Backend tests"
(cd backend && .venv/bin/pytest -q -p no:warnings)

step "Frontend dependencies, tests, build"
(cd frontend && npm ci --silent && npm test --silent && npm run build --silent)

step "Config file"
if ! sudo test -f "$ENV_FILE"; then
    sudo install -d -m 700 -o root -g root "$(dirname "$ENV_FILE")"
    echo "ARPB_ENV=production" | sudo tee "$ENV_FILE" >/dev/null
    sudo chmod 600 "$ENV_FILE"
fi

step "Database migrations"
sudo systemd-run --quiet --wait --pipe --collect \
    --uid=arpb --gid=arpb -p EnvironmentFile="$ENV_FILE" -p WorkingDirectory="$ROOT/backend" \
    "$ROOT/backend/.venv/bin/alembic" upgrade head

step "Frontend files"
sudo install -d -m 755 "$WEB_ROOT"
sudo rsync -a --delete frontend/dist/ "$WEB_ROOT/"

step "Services, backup, nginx"
sudo install -m 644 deploy/systemd/arportfoliobot-*.service deploy/systemd/arportfoliobot-*.timer /etc/systemd/system/
sudo install -m 755 deploy/backup.sh /usr/local/sbin/arportfoliobot-backup
sudo systemctl daemon-reload
sudo systemctl enable --quiet arportfoliobot-api arportfoliobot-worker arportfoliobot-backup.timer
sudo systemctl restart arportfoliobot-api arportfoliobot-worker
sudo systemctl start arportfoliobot-backup.timer
sudo install -m 644 deploy/nginx/arportfoliobot.conf /etc/nginx/sites-available/arportfoliobot
sudo ln -sfn /etc/nginx/sites-available/arportfoliobot /etc/nginx/sites-enabled/arportfoliobot
sudo rm -f /etc/nginx/sites-enabled/arportfoliobot-placeholder
sudo nginx -t -q
sudo systemctl reload nginx

step "Health check"
for _ in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8000/api/health 2>/dev/null | grep -q '"database":"ok"'; then
        curl -sS http://127.0.0.1:8000/api/health; echo
        echo "Deploy finished."
        exit 0
    fi
    sleep 1
done
echo "Backend did not become healthy. See: sudo journalctl -u arportfoliobot-api -n 50" >&2
exit 1
