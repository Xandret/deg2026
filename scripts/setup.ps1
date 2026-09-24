# deg2026 environment setup (Windows). ASCII-only on purpose: PowerShell 5.1 misreads UTF-8 without BOM.
# Usage: double-click setup.cmd in the project root (or: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "setup.log"
Start-Transcript -Path $log -Force | Out-Null
try {
    Write-Host "== deg2026 setup: $(Get-Date -Format s)"
    Write-Host "Project root: $root"

    # --- Machine facts (used to size parallel jobs) ---
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $os  = Get-CimInstance Win32_OperatingSystem
    Write-Host ("CPU: {0}; cores={1}; logical={2}" -f $cpu.Name.Trim(), $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors)
    Write-Host ("RAM total GB: {0:N1}; free GB: {1:N1}" -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB))
    $drive = (Get-Item $root).PSDrive
    Write-Host ("Disk {0}: free GB {1:N1}" -f $drive.Name, ($drive.Free/1GB))

    # --- Find a Python >= 3.11 (py launcher first, then python on PATH) ---
    $pyExe = $null; $pyArgs = @()
    foreach ($c in @("py -3.12", "py -3.13", "py -3.11", "py -3", "python")) {
        $exe = $c.Split(' ')[0]
        $a = @($c.Split(' ') | Select-Object -Skip 1)
        try {
            $v = & $exe @a -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $parts = "$v".Trim().Split('.')
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { $pyExe = $exe; $pyArgs = $a; Write-Host "Found Python $v via: $c"; break }
                else { Write-Host "Skipping Python $v via $c (need >= 3.11)" }
            }
        } catch { }
    }
    if (-not $pyExe) {
        Write-Host "RESULT: NO_PYTHON. Install Python 3.12 from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH' and 'py launcher'), then run setup.cmd again."
        exit 2
    }

    # --- venv + packages ---
    $venv = Join-Path $root ".venv"
    if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
        Write-Host "Creating venv at $venv"
        & $pyExe @pyArgs -m venv $venv
    }
    $vpy = Join-Path $venv "Scripts\python.exe"
    & $vpy -m pip install --upgrade pip | Out-Host
    & $vpy -m pip install -r (Join-Path $root "requirements.txt") | Out-Host
    & $vpy -c "import duckdb, pyarrow, pandas, orjson; print('duckdb', duckdb.__version__, '| pyarrow', pyarrow.__version__, '| pandas', pandas.__version__)"
    & $vpy -c "import os; print('os.cpu_count =', os.cpu_count())"
    # --- git lives on the Windows side (the Linux VM cannot delete lock files in this folder) ---
    $lock = Join-Path $root ".git\index.lock"
    if (Test-Path $lock) { Remove-Item $lock -Force; Write-Host "Removed stale .git\index.lock" }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) { Push-Location $root; & git --version; & git status --short --branch; Pop-Location }
    else { Write-Host "GIT: NOT_FOUND. Install Git for Windows: https://git-scm.com/download/win" }
    Write-Host "RESULT: OK"
}
catch {
    Write-Host "RESULT: ERROR $_"
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
