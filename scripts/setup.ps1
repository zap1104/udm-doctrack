# UDM DocTrack — one-command setup for Windows PowerShell.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }
function Die($text)  { Write-Host "  [x]  $text" -ForegroundColor Red; exit 1 }

Set-Location (Join-Path $PSScriptRoot "..")

Step "Checking Python"
try { $pythonVersion = (python --version) } catch { Die "Python is not installed. Get it from python.org and tick 'Add to PATH'." }
Ok $pythonVersion

Step "Creating the virtual environment"
if (-Not (Test-Path ".venv")) { python -m venv .venv; Ok "Created .venv" } else { Ok ".venv already exists" }
& .\.venv\Scripts\Activate.ps1

Step "Installing dependencies (this takes a minute or two)"
python -m pip install --upgrade pip --quiet
pip install -r requirements-dev.txt --quiet
Ok "Dependencies installed"

Step "Preparing the .env file"
if (-Not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    $secret = python -c "import secrets; print(secrets.token_urlsafe(50))"
    (Get-Content ".env") -replace "change-me-before-you-deploy-anything", $secret | Set-Content ".env"
    Ok "Created .env with a fresh secret key"
    Warn "Open .env and set POSTGRES_PASSWORD to match your database."
} else {
    Ok ".env already exists - leaving it alone"
}

Step "Running migrations"
python manage.py makemigrations accounts core tracking documents search --noinput
python manage.py migrate --noinput
Ok "Database tables created"

Step "Enabling search extensions"
try { python manage.py init_db } catch { Warn "Could not enable extensions. The app still runs." }

Step "Loading demo data"
python manage.py seed_demo
Ok "Demo data loaded"

Step "Collecting static files"
python manage.py collectstatic --noinput --clear | Out-Null
Ok "Static files ready"

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Start the server:  .\.venv\Scripts\Activate.ps1 ; python manage.py runserver"
Write-Host "  Then open:         http://127.0.0.1:8000"
Write-Host "  Sign in as:        admin / DocTrack2026!"
Write-Host ""
