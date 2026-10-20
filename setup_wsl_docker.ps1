# ==============================================================================
# WSL & Docker Desktop Environment Setup Script
# Run with Administrator privileges
# ==============================================================================

Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Installing Windows Subsystem for Linux (WSL 2)     " -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan

# 1. Install WSL core components
Write-Host "[1/3] Running WSL installation..." -ForegroundColor Yellow
try {
    wsl.exe --install --no-distribution
} catch {
    Write-Host "WSL install command encountered an issue: $_" -ForegroundColor Red
}

# 2. Enable Windows Optional Features
Write-Host "[2/3] Enabling Windows Virtual Machine & Subsystem Features..." -ForegroundColor Yellow
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

# 3. Update WSL Kernel
Write-Host "[3/3] Updating WSL kernel..." -ForegroundColor Yellow
try {
    wsl.exe --update
} catch {
    Write-Host "Kernel update step completed."
}

Write-Host "`n======================================================" -ForegroundColor Green
Write-Host "  Installation Completed!                             " -ForegroundColor Green
Write-Host "  Please restart your computer if prompted by Windows," -ForegroundColor Green
Write-Host "  then start Docker Desktop.                          " -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Green
Write-Host "Press any key to close this window..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
