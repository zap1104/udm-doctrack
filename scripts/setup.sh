#!/usr/bin/env bash
# UDM DocTrack — one-command setup for macOS and Linux.
#   bash scripts/setup.sh
set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; OFF=$'\033[0m'
step() { echo; echo "${BOLD}==> $1${OFF}"; }
ok()   { echo "${GREEN}  ✓ $1${OFF}"; }
warn() { echo "${YELLOW}  ! $1${OFF}"; }
die()  { echo "${RED}  ✗ $1${OFF}"; exit 1; }

cd "$(dirname "$0")/.."

step "Checking Python"
command -v python3 >/dev/null || die "Python 3 is not installed. Get it from python.org."
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11 or newer is required (found $PY_VERSION)."
ok "Python $PY_VERSION"

step "Creating the virtual environment"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ok "Created .venv"
else
  ok ".venv already exists"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

step "Installing dependencies (this takes a minute or two)"
python -m pip install --upgrade pip --quiet
pip install -r requirements-dev.txt --quiet
ok "Dependencies installed"

step "Preparing the .env file"
if [ ! -f .env ]; then
  cp .env.example .env
  SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(50))')
  python - "$SECRET" <<'PYEOF'
import pathlib, sys
path = pathlib.Path(".env")
text = path.read_text()
text = text.replace("change-me-before-you-deploy-anything", sys.argv[1])
path.write_text(text)
PYEOF
  ok "Created .env with a fresh secret key"
  warn "Open .env and set POSTGRES_PASSWORD to match your database."
else
  ok ".env already exists — leaving it alone"
fi

step "Checking PostgreSQL"
if command -v psql >/dev/null; then
  ok "psql found"
else
  warn "psql not found. Install PostgreSQL 14+ before continuing:"
  warn "  macOS:  brew install postgresql@16 && brew services start postgresql@16"
  warn "  Ubuntu: sudo apt install postgresql postgresql-contrib"
fi

step "Running migrations"
python manage.py makemigrations accounts core tracking documents search --noinput
python manage.py migrate --noinput
ok "Database tables created"

step "Enabling search extensions"
python manage.py init_db || warn "Could not enable extensions — see the message above. The app still runs."

step "Loading demo data"
python manage.py seed_demo
ok "Demo data loaded"

step "Collecting static files"
python manage.py collectstatic --noinput --clear >/dev/null
ok "Static files ready"

echo
echo "${GREEN}${BOLD}Setup complete.${OFF}"
echo
echo "  Start the server:  ${BOLD}source .venv/bin/activate && python manage.py runserver${OFF}"
echo "  Then open:         ${BOLD}http://127.0.0.1:8000${OFF}"
echo "  Sign in as:        ${BOLD}admin / DocTrack2026!${OFF}"
echo
