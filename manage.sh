#!/bin/bash
# Account commands on the server (run as the admin user; needs sudo). Examples:
#   /opt/arportfoliobot/manage.sh invite --role admin       one-time sign-up link
#   /opt/arportfoliobot/manage.sh reset-password EMAIL      one-time link to choose a new password
#   /opt/arportfoliobot/manage.sh reset-two-step EMAIL      for a lost phone
#   /opt/arportfoliobot/manage.sh users                     list accounts
set -euo pipefail
ROOT=/opt/arportfoliobot
exec sudo systemd-run --quiet --wait --pipe --collect \
    --uid=arpb --gid=arpb -p EnvironmentFile=/etc/arportfoliobot/arportfoliobot.env \
    -p WorkingDirectory="$ROOT/backend" \
    "$ROOT/backend/.venv/bin/python" -m app.cli "$@"
