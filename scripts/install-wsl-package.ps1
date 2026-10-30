# Install only the Microsoft WSL package verified during this setup. Run from
# an elevated Windows PowerShell; no distro conversion or reboot is performed.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = 'Stop'
$qtradeExpectedHash = 'b0d0c317ee7249eb4a40788b1516d3c3053cdfce62ff7bef7f0c35f4d2606005'
try {
    $qtradeIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $qtradePrincipal = New-Object Security.Principal.WindowsPrincipal($qtradeIdentity)
    if (-not $qtradePrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'An elevated Windows administrator token is required'
    }
    if ((Get-FileHash -LiteralPath $BundlePath -Algorithm SHA256).Hash -ne $qtradeExpectedHash) {
        throw 'Microsoft WSL 3.0.3 bundle hash mismatch'
    }
    # Windows deployment also verifies the package's publisher signature.
    Add-AppxPackage -Path $BundlePath -ErrorAction Stop
    @{ state = 'installed'; version = '3.0.3'; restart_requested = $false } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $ResultPath -Encoding UTF8
} catch {
    # Keep diagnostics free of unrelated environment values or credentials.
    @{ state = 'failed'; error_id = $_.FullyQualifiedErrorId; hresult = $_.Exception.HResult } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    exit 1
}
