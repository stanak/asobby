param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$base64 = $env:WINDOWS_CODESIGN_PFX_BASE64
$password = $env:WINDOWS_CODESIGN_PFX_PASSWORD
if (-not $base64 -or -not $password) {
    Write-Host "Code signing skipped (WINDOWS_CODESIGN_PFX_* not set)."
    exit 0
}

$pfxPath = Join-Path $env:RUNNER_TEMP "codesign.pfx"
[IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($base64))

$signtool = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe |
    Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
    Sort-Object FullName -Descending |
    Select-Object -First 1

if (-not $signtool) {
    Write-Error "signtool.exe not found"
    exit 1
}

$timestamp = "http://timestamp.digicert.com"
& $signtool.FullName sign /fd SHA256 /f $pfxPath /p $password /tr $timestamp /td SHA256 /a $Path
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Signed: $Path"
