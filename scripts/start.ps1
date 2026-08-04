# =============================================================================
# UDM DocTrack — guided setup for Windows
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#
# Unlike a plain install script, this one CHECKS each prerequisite, tells you
# what is wrong in plain language, and STOPS rather than carrying on and
# leaving you with a half-built system.
#
# Safe to run as many times as you like.
# =============================================================================

$ErrorActionPreference = "Continue"
$script:Failed = $false

# --- output helpers ----------------------------------------------------------
function Section($text) {
    Write-Host ""
    Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    Write-Host " $text" -ForegroundColor Cyan
    Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
}
function Ok($text)    { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "  [WARN] $text" -ForegroundColor Yellow }
function Fail($text)  { Write-Host "  [FAIL] $text" -ForegroundColor Red; $script:Failed = $true }
function Info($text)  { Write-Host "         $text" -ForegroundColor Gray }

function Confirm($question, $default = "Y") {
    $hint = if ($default -eq "Y") { "[Y/n]" } else { "[y/N]" }
    while ($true) {
        $answer = Read-Host "  $question $hint"
        if ([string]::IsNullOrWhiteSpace($answer)) { $answer = $default }
        switch ($answer.ToUpper()) {
            "Y" { return $true }
            "N" { return $false }
            default { Write-Host "  Please answer y or n." -ForegroundColor Yellow }
        }
    }
}

# Runs a command and reports failure clearly instead of silently continuing.
function Invoke-Step($description, $scriptBlock) {
    Info $description
    & $scriptBlock 2>&1 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        Fail "$description failed (exit code $LASTEXITCODE)."
        return $false
    }
    return $true
}

Set-Location (Join-Path $PSScriptRoot "..")

Write-Host ""
Write-Host "  UDM DocTrack — guided setup" -ForegroundColor White
Write-Host "  Working folder: $(Get-Location)" -ForegroundColor DarkGray

# =============================================================================
# 1. PREREQUISITE CHECKS — nothing is installed or changed in this section
# =============================================================================
Section "1. Checking what is installed"

# --- Python ------------------------------------------------------------------
$pythonOk = $false
try {
    $pythonRaw = (python --version 2>&1 | Out-String).Trim()
    if ($pythonRaw -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
            Ok "$pythonRaw"
            $pythonOk = $true
        } else {
            Fail "$pythonRaw is too old — this project needs Python 3.11 or newer."
            Info "Download: https://www.python.org/downloads/"
        }
    } else {
        Fail "Python did not report a version I understand: $pythonRaw"
    }
} catch {
    Fail "Python is not installed, or not on your PATH."
    Info "Install from https://www.python.org/downloads/"
    Info "IMPORTANT: tick 'Add Python to PATH' during installation."
}

# --- PostgreSQL client -------------------------------------------------------
$psqlOk = $false
try {
    $psqlRaw = (psql --version 2>&1 | Out-String).Trim()
    if ($psqlRaw -match "\d+\.\d+|\d+") { Ok "$psqlRaw"; $psqlOk = $true }
} catch {
    Warn "psql was not found on your PATH."
    Info "PostgreSQL may still be installed — the tools folder just is not on PATH."
    Info "Typical location: C:\Program Files\PostgreSQL\16\bin"
}

# --- Is the PostgreSQL SERVER actually running? ------------------------------
# This is the check that would have saved you the first time.
$serverRunning = $false
try {
    $probe = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -WarningAction SilentlyContinue
    if ($probe.TcpTestSucceeded) {
        Ok "PostgreSQL server is running and accepting connections on port 5432"
        $serverRunning = $true
    } else {
        Fail "Nothing is listening on port 5432 — the PostgreSQL server is not running."
    }
} catch {
    Warn "Could not probe port 5432. Checking the Windows service instead."
}

if (-not $serverRunning) {
    $service = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($service) {
        Info "Found Windows service '$($service.Name)' with status: $($service.Status)"
        if ($service.Status -ne "Running") {
            if (Confirm "Start the PostgreSQL service now?") {
                try {
                    Start-Service $service.Name -ErrorAction Stop
                    Start-Sleep -Seconds 3
                    Ok "Started $($service.Name)"
                    $serverRunning = $true
                    $script:Failed = $false
                } catch {
                    Fail "Could not start the service. Try running this script as Administrator."
                }
            }
        }
    } else {
        Info "No PostgreSQL Windows service found — it may not be installed."
        Info "Download: https://www.postgresql.org/download/windows/"
    }
}

# --- Git (optional but expected for a team project) --------------------------
try {
    $gitRaw = (git --version 2>&1 | Out-String).Trim()
    Ok "$gitRaw"
} catch {
    Warn "Git is not installed. You can still run the project, but you cannot share work."
    Info "Download: https://git-scm.com/download/win"
}

if ($script:Failed) {
    Write-Host ""
    Write-Host "  Stopping here. Fix the [FAIL] items above, then run this script again." -ForegroundColor Red
    Write-Host "  Detailed help: docs\SETUP.md" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

Write-Host ""
if (-not (Confirm "Prerequisites look good. Continue with setup?")) {
    Write-Host "  Cancelled — nothing was changed." -ForegroundColor Yellow
    exit 0
}

# =============================================================================
# 2. VIRTUAL ENVIRONMENT
# =============================================================================
Section "2. Python virtual environment"

if (Test-Path ".venv") {
    Ok ".venv already exists"
} else {
    Info "Creating .venv ..."
    python -m venv .venv
    if (-not (Test-Path ".venv")) {
        Fail "Could not create the virtual environment."
        exit 1
    }
    Ok "Created .venv"
}

& .\.venv\Scripts\Activate.ps1
if ($env:VIRTUAL_ENV) { Ok "Virtual environment active" } else { Fail "Could not activate .venv"; exit 1 }

# =============================================================================
# 3. DEPENDENCIES
# =============================================================================
Section "3. Python packages"

$djangoInstalled = $false
try {
    $djangoVersion = (python -c "import django; print(django.get_version())" 2>&1 | Out-String).Trim()
    if ($djangoVersion -match "^\d+\.") { Ok "Django $djangoVersion already installed"; $djangoInstalled = $true }
} catch { }

if (-not $djangoInstalled) {
    Info "Installing dependencies — this takes a minute or two ..."
    python -m pip install --upgrade pip --quiet
    pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Check your internet connection."; exit 1 }
    Ok "Dependencies installed"
} elseif (Confirm "Re-install/update dependencies anyway?" "N") {
    pip install -r requirements-dev.txt
    Ok "Dependencies updated"
}

# =============================================================================
# 4. ENVIRONMENT FILE
# =============================================================================
Section "4. Configuration (.env)"

if (Test-Path ".env") {
    Ok ".env already exists — leaving your settings alone"
} else {
    Copy-Item ".env.example" ".env"
    $secret = python -c "import secrets; print(secrets.token_urlsafe(50))"
    (Get-Content ".env") -replace "change-me-before-you-deploy-anything", $secret | Set-Content ".env"
    Ok "Created .env with a freshly generated secret key"
}

# Read the DB settings back so we can test them properly.
$envLines = Get-Content ".env" | Where-Object { $_ -match "^\s*[A-Z_]+\s*=" }
function Get-EnvValue($key) {
    $line = $envLines | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -First 1
    if ($line) { return ($line -split "=", 2)[1].Trim() }
    return ""
}
$dbName = Get-EnvValue "POSTGRES_DB"; if (-not $dbName) { $dbName = "udm_doctrack" }
$dbUser = Get-EnvValue "POSTGRES_USER"; if (-not $dbUser) { $dbUser = "udm" }
$dbPass = Get-EnvValue "POSTGRES_PASSWORD"

Info "Database: $dbName    User: $dbUser"

# =============================================================================
# 5. DATABASE CONNECTION TEST — before migrations, not after
# =============================================================================
Section "5. Testing the database connection"

$env:PGPASSWORD = $dbPass
$connectOk = $false
if ($psqlOk) {
    psql -U $dbUser -d $dbName -h 127.0.0.1 -c "SELECT 1;" *> $null
    if ($LASTEXITCODE -eq 0) { Ok "Connected to '$dbName' as '$dbUser'"; $connectOk = $true }
}

if (-not $connectOk) {
    Warn "Could not connect as '$dbUser' to '$dbName'."
    Info "Either the database, the user, or the password does not exist yet."
    Write-Host ""
    if (Confirm "Create the database and user now? (asks for your postgres superuser password)") {
        Write-Host ""
        Info "Enter the password you set for the 'postgres' user when you installed PostgreSQL."
        $pgPass = Read-Host "  postgres password" -AsSecureString
        $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pgPass))

        $sql = @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$dbUser') THEN
    CREATE ROLE $dbUser LOGIN PASSWORD '$dbPass';
  ELSE
    ALTER ROLE $dbUser LOGIN PASSWORD '$dbPass';
  END IF;
END `$`$;
"@
        $sql | psql -U postgres -h 127.0.0.1 -d postgres 2>&1 |
            ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }

        # CREATE DATABASE cannot run inside a DO block, so it is separate and
        # allowed to fail harmlessly if the database already exists.
        psql -U postgres -h 127.0.0.1 -d postgres -c "CREATE DATABASE $dbName;" *> $null
        psql -U postgres -h 127.0.0.1 -d postgres -c "ALTER DATABASE $dbName OWNER TO $dbUser;" *> $null
        psql -U postgres -h 127.0.0.1 -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE $dbName TO $dbUser;" *> $null

        $env:PGPASSWORD = $dbPass
        psql -U $dbUser -d $dbName -h 127.0.0.1 -c "SELECT 1;" *> $null
        if ($LASTEXITCODE -eq 0) { Ok "Database and user are ready"; $connectOk = $true }
        else { Fail "Still cannot connect. See docs\SETUP.md for the manual SQL steps." }
    }
}

if (-not $connectOk) {
    Write-Host ""
    Write-Host "  Cannot continue without a database connection." -ForegroundColor Red
    Write-Host "  Manual steps are in docs\SETUP.md, section 3." -ForegroundColor Gray
    exit 1
}

# =============================================================================
# 6. MIGRATIONS — each step checked, no silent failures
# =============================================================================
Section "6. Building the database tables"

if (-not (Invoke-Step "Generating migration files" {
    python manage.py makemigrations accounts core tracking documents search --noinput
})) { exit 1 }

if (-not (Invoke-Step "Applying migrations" { python manage.py migrate --noinput })) { exit 1 }
Ok "Database tables are ready"

# =============================================================================
# 7. SEARCH EXTENSIONS
# =============================================================================
Section "7. Search extensions (pg_trgm, unaccent)"

python manage.py init_db 2>&1 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -eq 0) { Ok "Search extensions ready" }
else { Warn "Extensions could not be enabled. Search still works, minus misspelling tolerance." }

# =============================================================================
# 8. DEMO DATA
# =============================================================================
Section "8. Demo data"

$userCount = (python -c "import django,os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup();from django.contrib.auth import get_user_model;print(get_user_model().objects.count())" 2>&1 | Out-String).Trim()

if ($userCount -match "^\d+$" -and [int]$userCount -gt 0) {
    Ok "$userCount user account(s) already exist"
    $loadDemo = Confirm "Reload the demo data anyway? (safe — it updates rather than duplicates)" "N"
} else {
    Info "No user accounts found — the demo data has not been loaded yet."
    $loadDemo = Confirm "Load demo offices, users and sample documents now?"
}

if ($loadDemo) {
    python manage.py seed_demo 2>&1 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        Fail "Demo data failed to load. NOTHING was saved — the whole command runs in one transaction."
        Info "Copy the error above and check it before continuing."
        exit 1
    }
    Ok "Demo data loaded"
}

# --- verify the admin account really works -----------------------------------
$check = (python -c @"
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
django.setup()
from django.contrib.auth import get_user_model
U = get_user_model()
u = U.objects.filter(username='admin').first()
print('MISSING' if not u else ('OK' if u.check_password('DocTrack2026!') else 'BADPASS'))
"@ 2>&1 | Out-String).Trim()

if ($check -match "OK") {
    Ok "Verified: admin account exists and the demo password works"
} elseif ($check -match "BADPASS") {
    Warn "The admin account exists but the password is not the demo one."
    Info "Someone changed it — that is fine if it was deliberate."
} elseif ($check -match "MISSING") {
    Warn "No admin account found. Run: python manage.py seed_demo"
}

# =============================================================================
# 9. STATIC FILES + FINAL CHECK
# =============================================================================
Section "9. Final checks"

python manage.py collectstatic --noinput *> $null
Ok "Static files collected"

python manage.py check 2>&1 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
if ($LASTEXITCODE -eq 0) { Ok "Django system check passed" } else { Warn "System check reported issues — see above." }

# --- diagnose and repair sign-in problems ------------------------------------
Info "Checking sign-in health ..."
python manage.py fix_login 2>&1 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }

Write-Host ""
if (Confirm "Run the end-to-end self check? (creates a test document, then rolls it back)") {
    python manage.py selfcheck 2>&1 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
    if ($LASTEXITCODE -ne 0) {
        Warn "The self check found problems. The app still runs — see the failures above."
    }
}

# =============================================================================
# DONE
# =============================================================================
Write-Host ""
Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host "──────────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Sign-in accounts:" -ForegroundColor White
Write-Host "    admin       / DocTrack2026!   (administrator)"
Write-Host "    records     / DocTrack2026!   (records officer)"
Write-Host "    med.staff   / DocTrack2026!   (regular user, has a document waiting)"
Write-Host ""

if (Confirm "Start the development server now?") {
    Write-Host ""
    Write-Host "  Opening http://127.0.0.1:8000 — press Ctrl+C to stop." -ForegroundColor Cyan
    Write-Host ""
    Start-Process "http://127.0.0.1:8000"
    python manage.py runserver
} else {
    Write-Host "  To start it later:" -ForegroundColor Gray
    Write-Host "    .\.venv\Scripts\Activate.ps1"
    Write-Host "    python manage.py runserver"
    Write-Host ""
}
