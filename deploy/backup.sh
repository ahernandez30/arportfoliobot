#!/bin/bash
# Nightly dump of the arportfoliobot database into a root-only folder, keeping 14 days.
# Installed as /usr/local/sbin/arportfoliobot-backup and run by arportfoliobot-backup.timer.
set -euo pipefail
umask 077
DIR=/var/backups/arportfoliobot
KEEP_DAYS=14
mkdir -p "$DIR"
chmod 700 "$DIR"
STAMP=$(date -u +%Y-%m-%d_%H%M%S)
TMP="$DIR/.arportfoliobot_$STAMP.dump.partial"
runuser -u postgres -- pg_dump --format=custom arportfoliobot > "$TMP"
mv "$TMP" "$DIR/arportfoliobot_$STAMP.dump"
find "$DIR" -maxdepth 1 -name 'arportfoliobot_*.dump' -mtime +$((KEEP_DAYS - 1)) -delete
find "$DIR" -maxdepth 1 -name '.arportfoliobot_*.partial' -mmin +60 -delete
echo "backup written: arportfoliobot_$STAMP.dump"
