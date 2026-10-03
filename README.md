# AR Portfolio Bot

Private trading site for arportfoliobot.com. The build plan lives outside the repo
(`~/BUILD_PLAN.md` on the server).

## Layout

- `backend/` — Python (FastAPI) web backend and background worker, Alembic migrations, tests.
- `frontend/` — React + TypeScript (Vite).
- `deploy/` — systemd units, nginx site, nightly backup script.
- `deploy.sh` — build, test, migrate, install config, restart, health check.
- `docs/` — reference material, including the TradingView strategy script.

## On the server

- Code: `/opt/arportfoliobot` (services run as the `arpb` user).
- Config: `/etc/arportfoliobot/arportfoliobot.env` (root-only, not in git).
- Database: PostgreSQL `arportfoliobot`, local socket only, peer authentication (no password).
- Backups: `/var/backups/arportfoliobot` (root-only), nightly at 07:00 UTC, 14 days kept.
- Services: `arportfoliobot-api`, `arportfoliobot-worker`, `arportfoliobot-backup.timer`.

## Development

    cd backend && .venv/bin/pytest -q      # needs the arportfoliobot_test database
    cd frontend && npm test && npm run dev
