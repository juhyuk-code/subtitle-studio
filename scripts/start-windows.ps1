$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "  $Message" -ForegroundColor Cyan
}

function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Ensure-Winget {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Windows Package Manager is required. Install 'App Installer' from the Microsoft Store, then double-click this file again."
    }
}

function Install-IfMissing(
    [string]$Command,
    [string]$PackageId,
    [string]$DisplayName
) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) {
        return
    }
    Ensure-Winget
    Write-Step "Installing $DisplayName for the first run"
    & winget.exe install --id $PackageId --exact --silent `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "$DisplayName installation failed with code $LASTEXITCODE."
    }
    Refresh-Path
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "$DisplayName was installed, but Windows has not refreshed PATH yet. Close this window and double-click Start Subtitle Studio again."
    }
}

function Find-Python {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3.11 -c "import sys" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return @($launcher.Source, "-3.11")
        }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }
    return $null
}

try {
    Write-Host ""
    Write-Host "  SUBTITLE STUDIO" -ForegroundColor White
    Write-Host "  Preparing your local workspace" -ForegroundColor DarkGray

    Install-IfMissing "node.exe" "OpenJS.NodeJS.LTS" "Node.js"
    if (-not (Find-Python)) {
        Ensure-Winget
        Write-Step "Installing Python 3.11 for the first run"
        & winget.exe install --id "Python.Python.3.11" --exact --silent `
            --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw "Python installation failed with code $LASTEXITCODE."
        }
        Refresh-Path
    }
    Install-IfMissing "ffmpeg.exe" "Gyan.FFmpeg" "FFmpeg"

    $pythonCommand = Find-Python
    if (-not $pythonCommand) {
        throw "Python 3.11 was not found after installation. Run this launcher again."
    }

    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Step "Creating the application environment"
        $pythonExecutable = $pythonCommand[0]
        $pythonArguments = @()
        if ($pythonCommand.Count -gt 1) {
            $pythonArguments += $pythonCommand[1..($pythonCommand.Count - 1)]
        }
        $pythonArguments += @("-m", "venv", ".venv")
        & $pythonExecutable $pythonArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the Python environment."
        }
    }

    Write-Step "Checking application components"
    & $venvPython -m pip install --disable-pip-version-check --quiet -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the backend components."
    }

    & $venvPython -c "import whisper" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "Installing Whisper speech recognition (first run can take several minutes)"
        & $venvPython -m pip install --disable-pip-version-check openai-whisper
        if ($LASTEXITCODE -ne 0) {
            throw "Could not install Whisper."
        }
    }

    & npm.cmd install --no-audit --no-fund --silent
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the web application components."
    }

    Write-Step "Starting Subtitle Studio"
    Write-Host "  The app will open at http://localhost:3000" -ForegroundColor DarkGray
    Write-Host "  Keep this window open while you work. Press Ctrl+C to stop." -ForegroundColor DarkGray

    $browserJob = Start-Job -ScriptBlock {
        for ($attempt = 0; $attempt -lt 90; $attempt++) {
            try {
                $response = Invoke-WebRequest -UseBasicParsing "http://localhost:3000" -TimeoutSec 1
                if ($response.StatusCode -eq 200) {
                    Start-Process "http://localhost:3000"
                    return
                }
            } catch {
                Start-Sleep -Milliseconds 750
            }
        }
    }

    & npm.cmd run dev
    Stop-Job $browserJob -ErrorAction SilentlyContinue
    Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
}
catch {
    Write-Host ""
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Fix the message above, then run Start Subtitle Studio again." -ForegroundColor Yellow
    exit 1
}
