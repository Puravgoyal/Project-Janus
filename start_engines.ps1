<#
.SYNOPSIS
    Project Janus - Dual Ollama Engine Launcher & Health Check Orchestrator
.DESCRIPTION
    Spawns and monitors isolated Ollama daemons for Project Janus:
      - GPU Engine on Port 11434: RTX 4050 with Flash Attention (interactive streaming chat)
      - CPU Engine on Port 11435: Isolated CPU 8-thread execution (background triage/JSON extraction)
    Compiles custom models from base llama model (artifish/llama3.2-uncensored:3b or llama3.2:3b):
      - janus-chat on Port 11434 via Modelfile.gpu
      - janus-extractor on Port 11435 via Modelfile.cpu
    Performs loopback health-check polling with a 30-second deadline.
.PARAMETER TimeoutSeconds
    Maximum seconds to wait for both Ollama engine HTTP endpoints (Default: 30).
.PARAMETER ForceRestart
    Terminates existing processes listening on 11434/11435 before spawning new instances.
.PARAMETER SkipModelCompile
    Skips the 'ollama create' step if models are already compiled.
#>

[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 30,
    [switch]$ForceRestart,
    [switch]$SkipModelCompile
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) {
    $ScriptDir = Get-Location | Select-Object -ExpandProperty Path
}

$GpuPort = 11434
$CpuPort = 11435
$GpuHost = "127.0.0.1:$GpuPort"
$CpuHost = "127.0.0.1:$CpuPort"
$GpuVersionUrl = "http://${GpuHost}/api/version"
$CpuVersionUrl = "http://${CpuHost}/api/version"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Project Janus - Dual Ollama Engine Launcher           " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Working Directory : $ScriptDir"
Write-Host "GPU Engine Target : $GpuHost (OLLAMA_FLASH_ATTENTION=1)"
Write-Host "CPU Engine Target : $CpuHost (CUDA_VISIBLE_DEVICES='', OMP_NUM_THREADS=8)"
Write-Host "Health Check Timeout: $TimeoutSeconds seconds"
Write-Host "----------------------------------------------------------"

# 1. Locate Ollama Executable
$ollamaCmd = Get-Command "ollama" -ErrorAction SilentlyContinue
$ollamaExe = if ($ollamaCmd) { $ollamaCmd.Source } else { $null }

if (-not $ollamaExe) {
    $userOllama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $userOllama) {
        $ollamaExe = $userOllama
    }
}

if (-not $ollamaExe) {
    Write-Error "[FATAL] 'ollama' executable was not found in PATH or standard install directories."
    exit 1
}

Write-Host "[1/5] Verified Ollama executable: $ollamaExe" -ForegroundColor Green

# 2. Port Sanitation & Conflict Resolution
function Test-PortListening([int]$Port) {
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return ($null -ne $conn)
    } catch {
        return $false
    }
}

function Test-HttpHealthy([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Stop-ProcessOnPort([int]$Port) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            $pidToKill = $c.OwningProcess
            if ($pidToKill -and $pidToKill -gt 0) {
                Write-Host "  -> Terminating process PID $pidToKill on port $Port..." -ForegroundColor Yellow
                Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        # Fallback if Get-NetTCPConnection has permission limitations
        Write-Warning "Unable to query owning process on port ${Port}: $_"
    }
}

if ($ForceRestart) {
    Write-Host "[2/5] Force restart requested. Clearing ports $GpuPort and $CpuPort..." -ForegroundColor Yellow
    Stop-ProcessOnPort $GpuPort
    Stop-ProcessOnPort $CpuPort
    Start-Sleep -Seconds 2
} else {
    Write-Host "[2/5] Checking existing engine listeners..."
}

# 3. Spawning Daemons using .NET ProcessStartInfo for cross-PowerShell compatibility
function Start-OllamaDaemon {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$HostBinding,
        [Parameter(Mandatory=$true)][hashtable]$EnvOverrides
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $ollamaExe
    $psi.Arguments = "serve"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

    # Inherit current environment variables
    foreach ($envKey in [System.Environment]::GetEnvironmentVariables().Keys) {
        $strKey = [string]$envKey
        if (-not $psi.EnvironmentVariables.ContainsKey($strKey)) {
            $psi.EnvironmentVariables[$strKey] = [System.Environment]::GetEnvironmentVariable($strKey)
        }
    }

    # Apply process-specific overrides
    $psi.EnvironmentVariables["OLLAMA_HOST"] = $HostBinding
    foreach ($k in $EnvOverrides.Keys) {
        $psi.EnvironmentVariables[[string]$k] = [string]$EnvOverrides[$k]
    }

    Write-Host "  -> Spawning $Name on $HostBinding..." -ForegroundColor Cyan
    $proc = [System.Diagnostics.Process]::Start($psi)
    return $proc
}

# Launch GPU daemon if not already healthy
$gpuHealthy = Test-HttpHealthy $GpuVersionUrl
if (-not $gpuHealthy) {
    if (Test-PortListening $GpuPort) {
        Write-Warning "Port $GpuPort is listening but not responding HTTP 200. Cleaning up..."
        Stop-ProcessOnPort $GpuPort
        Start-Sleep -Seconds 1
    }
    $gpuEnv = @{
        "OLLAMA_FLASH_ATTENTION" = "1"
        "OLLAMA_KEEP_ALIVE"       = "-1"
    }
    Start-OllamaDaemon -Name "GPU Engine" -HostBinding $GpuHost -EnvOverrides $gpuEnv | Out-Null
} else {
    Write-Host "  -> GPU Engine on $GpuHost is already running and healthy." -ForegroundColor Green
}

# Launch CPU daemon if not already healthy
$cpuHealthy = Test-HttpHealthy $CpuVersionUrl
if (-not $cpuHealthy) {
    if (Test-PortListening $CpuPort) {
        Write-Warning "Port $CpuPort is listening but not responding HTTP 200. Cleaning up..."
        Stop-ProcessOnPort $CpuPort
        Start-Sleep -Seconds 1
    }
    $cpuEnv = @{
        "CUDA_VISIBLE_DEVICES" = ""
        "OMP_NUM_THREADS"      = "8"
        "OLLAMA_KEEP_ALIVE"    = "-1"
    }
    Start-OllamaDaemon -Name "CPU Engine" -HostBinding $CpuHost -EnvOverrides $cpuEnv | Out-Null
} else {
    Write-Host "  -> CPU Engine on $CpuHost is already running and healthy." -ForegroundColor Green
}

# 4. Health Check Polling Loop (Max 30s)
Write-Host "[3/5] Polling health check on both endpoints (Deadline: ${TimeoutSeconds}s)..." -ForegroundColor Cyan
$startTime = [System.DateTime]::UtcNow
$bothHealthy = $false

while (([System.DateTime]::UtcNow - $startTime).TotalSeconds -lt $TimeoutSeconds) {
    $gpuCheck = Test-HttpHealthy $GpuVersionUrl
    $cpuCheck = Test-HttpHealthy $CpuVersionUrl

    $elapsed = [math]::Round(([System.DateTime]::UtcNow - $startTime).TotalSeconds, 1)
    Write-Host "  [${elapsed}s] GPU (Port $GpuPort): $(if ($gpuCheck) { 'HEALTHY' } else { 'WAITING' }) | CPU (Port $CpuPort): $(if ($cpuCheck) { 'HEALTHY' } else { 'WAITING' })"

    if ($gpuCheck -and $cpuCheck) {
        $bothHealthy = $true
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $bothHealthy) {
    Write-Error "[FAIL] Health check timed out after $TimeoutSeconds seconds. One or both engines failed to respond HTTP 200."
    exit 1
}

Write-Host "  -> Both engines responded HTTP 200 successfully!" -ForegroundColor Green

# 5. Base Model Verification & Model Compilation
if (-not $SkipModelCompile) {
    Write-Host "[4/5] Checking base models and compiling Janus models..." -ForegroundColor Cyan

    # Query local models via port 11434
    $env:OLLAMA_HOST = $GpuHost
    $installedModels = & $ollamaExe list 2>$null | Out-String

    $primaryBase = "artifish/llama3.2-uncensored:3b"
    $fallbackBase = "llama3.2:3b"
    $selectedBase = $primaryBase

    $hasPrimary = ($installedModels -match "artifish/llama3.2-uncensored:3b")
    $hasFallback = ($installedModels -match "llama3.2:3b")

    if (-not $hasPrimary -and -not $hasFallback) {
        Write-Host "  -> Base model not found locally. Attempting to pull '$primaryBase'..." -ForegroundColor Yellow
        try {
            & $ollamaExe pull $primaryBase
            $selectedBase = $primaryBase
        } catch {
            Write-Warning "Pull of '$primaryBase' failed. Attempting fallback '$fallbackBase'..."
            try {
                & $ollamaExe pull $fallbackBase
                $selectedBase = $fallbackBase
            } catch {
                Write-Warning "Base model pull failed. Continuing with existing Modelfiles..."
            }
        }
    } elseif (-not $hasPrimary -and $hasFallback) {
        Write-Host "  -> Found fallback model '$fallbackBase'. Updating Modelfiles base image..." -ForegroundColor Yellow
        $selectedBase = $fallbackBase
    } else {
        Write-Host "  -> Found primary model '$primaryBase'." -ForegroundColor Green
        $selectedBase = $primaryBase
    }

    $modelfileGpu = Join-Path $ScriptDir "Modelfile.gpu"
    $modelfileCpu = Join-Path $ScriptDir "Modelfile.cpu"

    if (-not (Test-Path $modelfileGpu) -or -not (Test-Path $modelfileCpu)) {
        Write-Error "Modelfile.gpu or Modelfile.cpu not found in $ScriptDir"
        exit 1
    }

    # Ensure Modelfiles reference the verified base model
    if ($selectedBase -ne $primaryBase) {
        (Get-Content $modelfileGpu) -replace "^FROM\s+.*", "FROM $selectedBase" | Set-Content $modelfileGpu
        (Get-Content $modelfileCpu) -replace "^FROM\s+.*", "FROM $selectedBase" | Set-Content $modelfileCpu
    }

    # Compile GPU model: janus-chat
    Write-Host "  -> Compiling 'janus-chat' on GPU Engine ($GpuHost)..." -ForegroundColor Cyan
    $env:OLLAMA_HOST = $GpuHost
    & $ollamaExe create janus-chat -f $modelfileGpu
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[FAIL] Failed to compile 'janus-chat' model."
        exit 1
    }

    # Compile CPU model: janus-extractor
    Write-Host "  -> Compiling 'janus-extractor' on CPU Engine ($CpuHost)..." -ForegroundColor Cyan
    $env:OLLAMA_HOST = $CpuHost
    & $ollamaExe create janus-extractor -f $modelfileCpu
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[FAIL] Failed to compile 'janus-extractor' model."
        exit 1
    }

    Write-Host "  -> Both models compiled successfully!" -ForegroundColor Green
} else {
    Write-Host "[4/5] Skipping model compilation as requested." -ForegroundColor Yellow
}

# 6. Warmup & Resident Lock
Write-Host "[5/5] Sending resident keep-alive warmups..." -ForegroundColor Cyan
try {
    $bodyGpu = @{ model = "janus-chat"; prompt = ""; keep_alive = -1 } | ConvertTo-Json
    Invoke-RestMethod -Uri "http://${GpuHost}/api/generate" -Method Post -Body $bodyGpu -ContentType "application/json" -TimeoutSec 15 -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  -> janus-chat resident on GPU ($GpuHost)" -ForegroundColor Green
} catch {
    Write-Warning "Warmup ping to janus-chat returned warning (non-fatal): $_"
}

try {
    $bodyCpu = @{ model = "janus-extractor"; prompt = ""; keep_alive = -1 } | ConvertTo-Json
    Invoke-RestMethod -Uri "http://${CpuHost}/api/generate" -Method Post -Body $bodyCpu -ContentType "application/json" -TimeoutSec 15 -ErrorAction SilentlyContinue | Out-Null
    Write-Host "  -> janus-extractor resident on CPU ($CpuHost)" -ForegroundColor Green
} catch {
    Write-Warning "Warmup ping to janus-extractor returned warning (non-fatal): $_"
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "   Project Janus Dual Engines Ready!                     " -ForegroundColor Green
Write-Host "   - GPU Engine: http://${GpuHost} (janus-chat)           " -ForegroundColor Green
Write-Host "   - CPU Engine: http://${CpuHost} (janus-extractor)      " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green

exit 0
