#!/usr/bin/env bash
# =============================================================================
# UDM DocTrack — guided setup for macOS and Linux
#
#   bash scripts/start.sh
#
# Checks every prerequisite, explains what is wrong in plain language, and
# STOPS rather than carrying on and leaving you with a half-built system.
# Safe to run repeatedly.
# =============================================================================

set -uo pipefail   # deliberately NOT -e: we handle failures ourselves

BOLD=$'\033[1m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'
RED=$'\033[0;31m'; CYAN=$'\033[0;36m'; GRAY=$'\033[0;90m'; OFF=$'\033[0m'

FAILED=0
section() { printf "\n${GRAY}──────────────────────────────────────────────────────────${OFF}\n"
            printf "${CYAN} %s${OFF}\n" "$1"
            printf "${GRAY}──────────────────────────────────────────────────────────${OFF}\n"; }
ok()   { printf "  ${GREEN}[OK]   %s${OFF}\n" "$1"; }
warn() { printf "  ${YELLOW}[WARN] %s${OFF}\n" "$1"; }
fail() { printf "  ${RED}[FAIL] %s${OFF}\n" "$1"; FAILED=1; }
info() { printf "${GRAY}         %s${OFF}\n" "$1"; }

confirm() {
    local question="$1" default="${2:-Y}" hint answer
    [ "$default" = "Y" ] && hint="[Y/n]" || hint="[y/N]"
    while true; do
        read -r -p "  $question $hint " answer
        answer="${answer:-$default}"
        case "$answer" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
            *) printf "  ${YELLOW}Please answer y or n.${OFF}\n" ;;
        esac
    done
}

run_step() {
    local description="$1"; shift
    info "$description"
    if "$@" 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/"; then
        return 0
    fi
    fail "$description failed."
    return 1
}

cd "$(dirname "$0")/.."

printf "\n  ${BOLD}UDM DocTrack — guided setup${OFF}\n"
printf "${GRAY}  Working folder: %s${OFF}\n" "$(pwd)"

# =============================================================================
section "1. Checking what is installed"
# =============================================================================

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
            PYTHON="$candidate"; break
        fi
    fi
done

if [ -n "$PYTHON" ]; then
    ok "$($PYTHON --version 2>&1)"
else
    fail "Python 3.11 or newer was not found."
    info "macOS:  brew install python@3.12"
    info "Ubuntu: sudo apt install python3 python3-venv python3-pip"
fi

if command -v psql >/dev/null 2>&1; then
    ok "$(psql --version 2>&1)"
    PSQL_OK=1
else
    warn "psql not found on PATH — the PostgreSQL client tools are missing."
    info "macOS:  brew install postgresql@16"
    info "Ubuntu: sudo apt install postgresql postgresql-contrib"
    PSQL_OK=0
fi

# Is the SERVER actually up? This is the check that catches the usual failure.
SERVER_RUNNING=0
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
        ok "PostgreSQL server is running on port 5432"; SERVER_RUNNING=1
    fi
elif command -v nc >/dev/null 2>&1; then
    if nc -z 127.0.0.1 5432 2>/dev/null; then
        ok "PostgreSQL server is accepting connections on port 5432"; SERVER_RUNNING=1
    fi
fi

if [ "$SERVER_RUNNING" -eq 0 ]; then
    fail "Nothing is listening on port 5432 — PostgreSQL is not running."
    info "macOS:  brew services start postgresql@16"
    info "Ubuntu: sudo systemctl start postgresql"
    if confirm "Try to start it now?"; then
        if command -v brew >/dev/null 2>&1; then
            brew services start postgresql@16 >/dev/null 2>&1 || brew services start postgresql >/dev/null 2>&1
        elif command -v systemctl >/dev/null 2>&1; then
            sudo systemctl start postgresql
        fi
        sleep 3
        if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
            ok "PostgreSQL started"; SERVER_RUNNING=1; FAILED=0
        else
            fail "Could not start PostgreSQL automatically."
        fi
    fi
fi

command -v git >/dev/null 2>&1 && ok "$(git --version)" || warn "Git is not installed — you can run the project but not share work."

if [ "$FAILED" -eq 1 ]; then
    printf "\n  ${RED}Stopping here. Fix the [FAIL] items above, then run this script again.${OFF}\n"
    printf "${GRAY}  Detailed help: docs/SETUP.md${OFF}\n\n"
    exit 1
fi

echo
confirm "Prerequisites look good. Continue with setup?" || { printf "  ${YELLOW}Cancelled — nothing changed.${OFF}\n"; exit 0; }

# =============================================================================
section "2. Python virtual environment"
# =============================================================================
if [ -d .venv ]; then
    ok ".venv already exists"
else
    info "Creating .venv ..."
    "$PYTHON" -m venv .venv || { fail "Could not create the virtual environment."; exit 1; }
    ok "Created .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate
[ -n "${VIRTUAL_ENV:-}" ] && ok "Virtual environment active" || { fail "Could not activate .venv"; exit 1; }

# =============================================================================
section "3. Python packages"
# =============================================================================
if DJANGO_VERSION=$(python -c "import django; print(django.get_version())" 2>/dev/null); then
    ok "Django $DJANGO_VERSION already installed"
    confirm "Re-install/update dependencies anyway?" "N" && pip install -r requirements-dev.txt --quiet
else
    info "Installing dependencies — this takes a minute or two ..."
    python -m pip install --upgrade pip --quiet
    if ! pip install -r requirements-dev.txt --quiet; then
        fail "Dependency install failed. Check your internet connection."; exit 1
    fi
    ok "Dependencies installed"
fi

# =============================================================================
section "4. Configuration (.env)"
# =============================================================================
if [ -f .env ]; then
    ok ".env already exists — leaving your settings alone"
else
    cp .env.example .env
    SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(50))')
    python - "$SECRET" <<'PYEOF'
import pathlib, sys
path = pathlib.Path(".env")
path.write_text(path.read_text().replace("change-me-before-you-deploy-anything", sys.argv[1]))
PYEOF
    ok "Created .env with a freshly generated secret key"
fi

get_env() { grep -E "^\s*$1\s*=" .env 2>/dev/null | head -1 | cut -d= -f2- | xargs; }
DB_NAME="$(get_env POSTGRES_DB)"; DB_NAME="${DB_NAME:-udm_doctrack}"
DB_USER="$(get_env POSTGRES_USER)"; DB_USER="${DB_USER:-udm}"
DB_PASS="$(get_env POSTGRES_PASSWORD)"
info "Database: $DB_NAME    User: $DB_USER"

# =============================================================================
section "5. Testing the database connection"
# =============================================================================
CONNECT_OK=0
if [ "$PSQL_OK" -eq 1 ]; then
    if PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -h 127.0.0.1 -c "SELECT 1;" >/dev/null 2>&1; then
        ok "Connected to '$DB_NAME' as '$DB_USER'"; CONNECT_OK=1
    fi
fi

if [ "$CONNECT_OK" -eq 0 ]; then
    warn "Could not connect as '$DB_USER' to '$DB_NAME'."
    info "Either the database, the user, or the password does not exist yet."
    echo
    if confirm "Create the database and user now?"; then
        SUPER="postgres"
        command -v sudo >/dev/null 2>&1 && [ "$(uname)" != "Darwin" ] && PSQL_SUPER=(sudo -u postgres psql) || PSQL_SUPER=(psql -U postgres -h 127.0.0.1 -d postgres)

        "${PSQL_SUPER[@]}" <<SQL 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/"
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';
  ELSE
    ALTER ROLE $DB_USER LOGIN PASSWORD '$DB_PASS';
  END IF;
END \$\$;
SQL
        # CREATE DATABASE cannot run inside a DO block; failures here are harmless.
        "${PSQL_SUPER[@]}" -c "CREATE DATABASE $DB_NAME;" >/dev/null 2>&1
        "${PSQL_SUPER[@]}" -c "ALTER DATABASE $DB_NAME OWNER TO $DB_USER;" >/dev/null 2>&1
        "${PSQL_SUPER[@]}" -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" >/dev/null 2>&1

        if PGPASSWORD="$DB_PASS" psql -U "$DB_USER" -d "$DB_NAME" -h 127.0.0.1 -c "SELECT 1;" >/dev/null 2>&1; then
            ok "Database and user are ready"; CONNECT_OK=1
        else
            fail "Still cannot connect. See docs/SETUP.md for the manual SQL steps."
        fi
    fi
fi

if [ "$CONNECT_OK" -eq 0 ]; then
    printf "\n  ${RED}Cannot continue without a database connection.${OFF}\n"
    printf "${GRAY}  Manual steps are in docs/SETUP.md, section 3.${OFF}\n\n"
    exit 1
fi

# =============================================================================
section "6. Building the database tables"
# =============================================================================
run_step "Generating migration files" python manage.py makemigrations accounts core tracking documents search --noinput || exit 1
run_step "Applying migrations" python manage.py migrate --noinput || exit 1
ok "Database tables are ready"

# =============================================================================
section "7. Search extensions (pg_trgm, unaccent)"
# =============================================================================
if python manage.py init_db 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/"; then
    ok "Search extensions ready"
else
    warn "Extensions could not be enabled. Search still works, minus misspelling tolerance."
fi

# =============================================================================
section "8. Demo data"
# =============================================================================
USER_COUNT=$(python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from django.contrib.auth import get_user_model
print(get_user_model().objects.count())" 2>/dev/null | tr -d '[:space:]')

LOAD_DEMO=1
if [[ "$USER_COUNT" =~ ^[0-9]+$ ]] && [ "$USER_COUNT" -gt 0 ]; then
    ok "$USER_COUNT user account(s) already exist"
    confirm "Reload the demo data anyway? (safe — updates rather than duplicates)" "N" || LOAD_DEMO=0
else
    info "No user accounts found — the demo data has not been loaded yet."
    confirm "Load demo offices, users and sample documents now?" || LOAD_DEMO=0
fi

if [ "$LOAD_DEMO" -eq 1 ]; then
    if python manage.py seed_demo 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/"; then
        ok "Demo data loaded"
    else
        fail "Demo data failed. NOTHING was saved — the whole command runs in one transaction."
        info "Copy the error above and check it before continuing."
        exit 1
    fi
fi

CHECK=$(python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
from django.contrib.auth import get_user_model
U = get_user_model(); u = U.objects.filter(username='admin').first()
print('MISSING' if not u else ('OK' if u.check_password('DocTrack2026!') else 'BADPASS'))" 2>/dev/null | tr -d '[:space:]')

case "$CHECK" in
    OK)      ok "Verified: admin account exists and the demo password works" ;;
    BADPASS) warn "The admin account exists but the password is not the demo one." ;;
    MISSING) warn "No admin account found. Run: python manage.py seed_demo" ;;
esac

# =============================================================================
section "9. Final checks"
# =============================================================================
python manage.py collectstatic --noinput >/dev/null 2>&1 && ok "Static files collected"
python manage.py check 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/"
info "Checking sign-in health ..."
python manage.py fix_login 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/"

echo
if confirm "Run the end-to-end self check? (creates a test document, then rolls it back)"; then
    python manage.py selfcheck 2>&1 | sed "s/^/${GRAY}         /;s/$/${OFF}/" \
        || warn "The self check found problems. The app still runs — see the failures above."
fi

# =============================================================================
printf "\n${GRAY}──────────────────────────────────────────────────────────${OFF}\n"
printf "${GREEN}${BOLD}  Setup complete.${OFF}\n"
printf "${GRAY}──────────────────────────────────────────────────────────${OFF}\n\n"
printf "  ${BOLD}Sign-in accounts:${OFF}\n"
printf "    admin       / DocTrack2026!   (administrator)\n"
printf "    records     / DocTrack2026!   (records officer)\n"
printf "    med.staff   / DocTrack2026!   (regular user, has a document waiting)\n\n"

if confirm "Start the development server now?"; then
    printf "\n  ${CYAN}Open http://127.0.0.1:8000 — press Ctrl+C to stop.${OFF}\n\n"
    python manage.py runserver
else
    printf "${GRAY}  To start it later:${OFF}\n"
    printf "    source .venv/bin/activate\n    python manage.py runserver\n\n"
fi
