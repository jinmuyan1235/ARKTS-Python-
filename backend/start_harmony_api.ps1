param(
    [string]$PythonExe = "python",
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ApiKey,
    [ValidateSet("demo", "production")]
    [string]$AppMode = "production",
    [ValidateSet("demo", "molscribe", "decimer", "ensemble")]
    [string]$Backend = "decimer"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$env:APP_MODE = $AppMode
$env:OCSR_BACKEND = $Backend
$env:HARMONY_API_KEY = $ApiKey

Push-Location -LiteralPath $ProjectRoot
try {
    & $PythonExe -c "import fastapi, uvicorn, multipart"
    if ($LASTEXITCODE -ne 0) {
        throw "API dependencies are missing. Run: $PythonExe -m pip install -r requirements-api.txt"
    }
    & $PythonExe -m uvicorn api_server:app --host $HostAddress --port $Port
} finally {
    Pop-Location
}
