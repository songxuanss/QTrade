# Entry point for the dedicated Ubuntu 26 environment. Does not convert another
# distro, initialize Git history, reset data, or request a Windows reboot.
[CmdletBinding()]
param(
    [ValidateSet('status', 'bootstrap', 'keepalive')][string]$Action = 'status',
    [string]$Proxy = ''
)

$ErrorActionPreference = 'Stop'
$qtradeDistro = 'QTrade-Ubuntu-26'
$qtradeRoot = Split-Path -Parent $PSScriptRoot
$qtradeSso = Join-Path (Split-Path -Parent $qtradeRoot) 'sso'
$qtradeWsl = 'C:\Program Files\WSL\wsl.exe'
if (-not (Test-Path -LiteralPath $qtradeWsl)) {
    throw 'Install modern WSL first; this machine uses its signed Program Files executable.'
}
$qtradeKeepaliveState = Join-Path $qtradeRoot '.tools/windows-environment/keepalive.json'

function Get-QTradeKeepalive {
    if (-not (Test-Path -LiteralPath $qtradeKeepaliveState)) { return $null }
    $qtradeRecord = Get-Content -LiteralPath $qtradeKeepaliveState -Raw | ConvertFrom-Json
    $qtradeProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$qtradeRecord.pid)
    if ($qtradeProcess -and $qtradeProcess.ExecutablePath -eq $qtradeWsl -and
        $qtradeProcess.CreationDate.ToUniversalTime().Ticks -eq $qtradeRecord.created_ticks -and
        $qtradeProcess.CommandLine -match '--distribution\s+QTrade-Ubuntu-26\s' -and
        $qtradeProcess.CommandLine -match '--exec\s+/bin/sleep\s+infinity') {
        return $qtradeProcess
    }
    return $null
}

if ($Action -eq 'keepalive') {
    # systemd services do not keep a WSL instance alive. Hold one owned Windows
    # foreground WSL client open without a visible terminal or a CPU busy loop.
    $qtradeExisting = Get-QTradeKeepalive
    if ($qtradeExisting) {
        Write-Output ('Existing Ubuntu session retained: ' + $qtradeExisting.ProcessId)
        exit 0
    }
    $qtradeKeeper = Start-Process -FilePath $qtradeWsl -WindowStyle Hidden -PassThru `
        -ArgumentList @('--distribution', $qtradeDistro, '--user', 'qtrade', '--exec', '/bin/sleep', 'infinity')
    Start-Sleep -Milliseconds 500
    $qtradeKeeper.Refresh()
    if ($qtradeKeeper.HasExited) { throw 'The Ubuntu keepalive client exited during startup' }
    $qtradeKeeperInfo = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $qtradeKeeper.Id)
    New-Item -ItemType Directory -Path (Split-Path -Parent $qtradeKeepaliveState) -Force | Out-Null
    @{ pid = $qtradeKeeper.Id; created_ticks = $qtradeKeeperInfo.CreationDate.ToUniversalTime().Ticks } |
        ConvertTo-Json -Compress | Set-Content -LiteralPath $qtradeKeepaliveState -Encoding utf8
    Write-Output ('Ubuntu session held open: ' + $qtradeKeeper.Id)
    exit 0
}

function Convert-QTradeLinuxPath([string]$Path) {
    $qtradeConverted = & $qtradeWsl --distribution $qtradeDistro --user root --exec /usr/bin/wslpath -a $Path
    if ($LASTEXITCODE -ne 0) { throw 'WSL could not translate the source path' }
    return ($qtradeConverted | Out-String).Trim()
}

if ($Action -eq 'status') {
    Write-Output ('Owned Ubuntu keepalive active: ' + [bool](Get-QTradeKeepalive))
    $qtradeVersion = (& $qtradeWsl --version | Out-String).Replace("`0", '')
    Write-Output $qtradeVersion
    & $qtradeWsl --distribution $qtradeDistro --user root --exec /bin/bash -lc '
        . /etc/os-release
        printf "Distribution: %s\nKernel: " "$PRETTY_NAME"
        uname -r
        printf "Docker service: "
        systemctl is-active docker || true
        if command -v docker >/dev/null; then docker compose version; fi
        if test -x /home/qtrade/workspaces/QTrade/.venv/bin/python; then
            /home/qtrade/workspaces/QTrade/.venv/bin/python --version
        fi
    '
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu 26 environment status check failed' }
    exit 0
}

if (-not (Test-Path -LiteralPath (Join-Path $qtradeSso 'pyproject.toml'))) {
    throw 'The sibling SSO project is required; no replacement identity provider will be created.'
}
if ($Proxy) {
    $qtradeProxyUri = [Uri]$Proxy
    if ($qtradeProxyUri.Scheme -notin @('http', 'https') -or
        -not $qtradeProxyUri.IsLoopback -or $qtradeProxyUri.UserInfo -or
        $Proxy -match '[\r\n"\\]') {
        throw 'Use a credential-free loopback HTTP proxy URL or omit Proxy for direct access.'
    }
}
$qtradeLinuxRoot = Convert-QTradeLinuxPath $qtradeRoot
$qtradeLinuxSso = Convert-QTradeLinuxPath $qtradeSso
& $qtradeWsl --distribution $qtradeDistro --user root --exec /usr/bin/env `
    "http_proxy=$Proxy" "https_proxy=$Proxy" /bin/bash `
    "$qtradeLinuxRoot/scripts/setup-wsl-linux.sh" $qtradeLinuxRoot $qtradeLinuxSso
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu 26 bootstrap incomplete; inspect the reported failed stage.' }
