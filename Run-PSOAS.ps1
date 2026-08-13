# Run-PSOAS.ps1
# User launcher for PSOAS on Windows.
# Creates a private virtual environment, installs requirements, refreshes the
# existing markdown index, and starts the interactive PSOAS terminal.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $PsoasArguments
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)] [string] $FilePath,
        [Parameter(Mandatory = $false)] [string[]] $ArgumentList = @(),
        [Parameter(Mandatory = $false)] [string] $FailureMessage = "The command failed."
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Exit code: $LASTEXITCODE"
    }
}

Write-Host "Starting PSOAS..." -ForegroundColor Cyan

$VenvDir = Join-Path $ProjectRoot ".venv"
$PythonPath = Join-Path $VenvDir "Scripts\python.exe"

# Find an installed Python launcher. The official Python installer commonly
# provides py.exe even when the `python` command is not on PATH.
$PyCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue
$PythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue

if (-not $PyCommand -and -not $PythonCommand) {
    throw "Python was not found. Install the official Python 3.14.7 Windows installer (64-bit), then double-click Run-PSOAS.cmd again."
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    Write-Host "Preparing PSOAS for first use..." -ForegroundColor Yellow

    if ($PyCommand) {
        $Bootstrap = $PyCommand.Source
        Invoke-Checked -FilePath $Bootstrap -ArgumentList @("-3.14", "-m", "venv", $VenvDir) `
            -FailureMessage "Could not create the PSOAS Python environment."
    } else {
        $Bootstrap = $PythonCommand.Source
        Invoke-Checked -FilePath $Bootstrap -ArgumentList @("-m", "venv", $VenvDir) `
            -FailureMessage "Could not create the PSOAS Python environment."
    }
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "The PSOAS Python environment could not be created."
}

$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"
$RequirementsHashPath = Join-Path $VenvDir ".requirements.sha256"
$RequirementsHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $RequirementsPath).Hash
$InstalledRequirementsHash = ""

if (Test-Path -LiteralPath $RequirementsHashPath -PathType Leaf) {
    $InstalledRequirementsHash = (Get-Content -LiteralPath $RequirementsHashPath -Raw).Trim()
}

if ($RequirementsHash -ne $InstalledRequirementsHash) {
    Write-Host "Installing PSOAS components. This may take several minutes..." -ForegroundColor Yellow
    Invoke-Checked -FilePath $PythonPath -ArgumentList @(
        "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"
    ) -FailureMessage "Could not update the Python package installer. Check internet access."

    Invoke-Checked -FilePath $PythonPath -ArgumentList @(
        "-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "-r", $RequirementsPath
    ) -FailureMessage "Could not install PSOAS components. Check internet access or contact support."

    Set-Content -LiteralPath $RequirementsHashPath -Value $RequirementsHash -Encoding ASCII
}

# The USB copy contains the already-ingested markdown corpus. Refresh the
# deterministic chunk index before starting, when the chunker is available.
$ChunkerPath = Join-Path $ProjectRoot "src\scripts\PMS2\01_Chunk.py"
$IngestedDir = Join-Path $ProjectRoot "data\files_ingested"
if ((Test-Path -LiteralPath $ChunkerPath -PathType Leaf) -and
    (Test-Path -LiteralPath $IngestedDir -PathType Container)) {
    Write-Host "Refreshing the local document index..." -ForegroundColor Yellow
    Invoke-Checked -FilePath $PythonPath -ArgumentList @($ChunkerPath) `
        -FailureMessage "Could not refresh the local document index."
}

$PsoasPath = Join-Path $ProjectRoot "src\psoas.py"
if (-not (Test-Path -LiteralPath $PsoasPath -PathType Leaf)) {
    throw "The PSOAS application was not found in this folder."
}

Write-Host "PSOAS is ready. Ask the orchestrator a question." -ForegroundColor Green
& $PythonPath $PsoasPath @PsoasArguments
exit $LASTEXITCODE
