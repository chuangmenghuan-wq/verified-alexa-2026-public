param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -c 'import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize(chr(80))==8'
if ($LASTEXITCODE) { throw 'Choose a Python 3.12 x64 executable with -Python' }
& $Python -m venv .venv
if ($LASTEXITCODE) { throw 'Python 3.12 x64 is required' }
& ./.venv/Scripts/python.exe -m pip install --no-index --find-links wheelhouse -r requirements-windows.lock
if ($LASTEXITCODE) { throw 'Dependency install failed' }
& ./.venv/Scripts/python.exe -m pip install --no-index --no-deps --no-build-isolation .
if ($LASTEXITCODE) { throw 'App install failed' }
Write-Host 'Installed. Run ./.venv/Scripts/verified-alexa-sim.exe'
