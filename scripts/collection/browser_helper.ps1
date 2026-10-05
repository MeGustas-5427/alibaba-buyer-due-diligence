# Only manages an explicitly selected, locally reviewed helper. Never installs or repairs Chrome.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('inspect','start','stop')][string]$Action,
    [Parameter(Mandatory)][string]$RunPath,
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][string]$RecordPath,
    [string]$Executable,
    [string]$ScriptFile,
    [string]$AuthorityPath
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Write-Result([string]$Status, $HelperPid = $null) {
    @{status=$Status; runId=$RunId; pid=$HelperPid} | ConvertTo-Json -Compress
}
function Get-Scoped([string]$ProcessName) {
    $queryErrors = @()
    $found = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue -ErrorVariable queryErrors)
    if (@($queryErrors | Where-Object { $_.FullyQualifiedErrorId -notlike 'NoProcessFoundForGivenName*' }).Count) {
        throw 'Scoped process query failed'
    }
    return $found
}
function Get-OwnedCandidate([int]$HelperPid) {
    $queryErrors = @()
    $found = @(Get-Process -Id $HelperPid -ErrorAction SilentlyContinue -ErrorVariable queryErrors)
    if (@($queryErrors | Where-Object { $_.FullyQualifiedErrorId -notlike 'NoProcessFoundForGivenId*' }).Count) {
        throw 'Scoped PID query failed'
    }
    return $found
}
function Save-Identity($Identity) {
    $temporary = $RecordPath + '.tmp'
    if (Test-Path -LiteralPath $temporary) { throw 'Unexpected temporary identity file' }
    [IO.File]::WriteAllText($temporary, ($Identity | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporary -Destination $RecordPath -Force
}

try {
    $resolvedRun = (Resolve-Path -LiteralPath $RunPath).Path
    $expectedRecord = Join-Path (Join-Path $resolvedRun 'collection') 'browser-helper-identity.private.json'
    if ([IO.Path]::GetFullPath($RecordPath) -ne $expectedRecord -or (Split-Path -Leaf $resolvedRun) -ne $RunId) { throw 'Run binding mismatch' }
    $identity = $null
    if (Test-Path -LiteralPath $RecordPath) {
        if ((Get-Item -LiteralPath $RecordPath).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe identity path' }
        $identity = Get-Content -Raw -Encoding utf8 -LiteralPath $RecordPath | ConvertFrom-Json
        if ($identity.runId -ne $RunId -or $identity.run -ne $resolvedRun) { throw 'Identity belongs to another run' }
        if ($Executable -and [IO.Path]::GetFullPath($Executable) -ne $identity.executable) { throw 'Executable differs from saved identity' }
        if ($ScriptFile -and [IO.Path]::GetFullPath($ScriptFile) -ne $identity.script) { throw 'Script differs from saved identity' }
        $Executable = $identity.executable
        $ScriptFile = $identity.script
    }
    if (-not $Executable -or [IO.Path]::GetFileName($Executable) -notin @('AutoHotkey64.exe','AutoHotkey32.exe')) { throw 'Select the exact AutoHotkey executable' }
    # A current-run inspect/stop touches only its recorded PID. Start checks both AHK bitnesses
    # so a #SingleInstance helper cannot replace another pre-existing instance.
    $helpers = if ($identity -and $Action -ne 'start') { @(Get-OwnedCandidate $identity.pid) }
               else { @(Get-Scoped 'AutoHotkey64') + @(Get-Scoped 'AutoHotkey32') }
    $owned = $null
    if ($identity) {
        $owned = @($helpers | Where-Object { $_.Id -eq $identity.pid })
        if ($owned.Count -gt 1) { throw 'Ambiguous process identity' }
        if ($owned.Count -eq 1) {
            $owned = $owned[0]
            $null = $owned.Handle # Pin the actual process handle before comparison and any termination.
            if ($owned.Path -ne $identity.executable -or $owned.StartTime.ToUniversalTime().Ticks -ne $identity.startTimeUtcTicks -or
                (Get-FileHash -LiteralPath $identity.executable -Algorithm SHA256).Hash -ne $identity.executableSha256 -or
                (Get-FileHash -LiteralPath $identity.script -Algorithm SHA256).Hash -ne $identity.scriptSha256) { throw 'Process or script identity changed' }
        } else { $owned = $null }
    }
    if ($Action -eq 'inspect') {
        if ($owned) { Write-Result 'verified' $owned.Id }
        elseif ($helpers.Count) { Write-Result 'unverified' }
        elseif ($identity) { Write-Result 'exited' }
        else { Write-Result 'absent' }
        exit 0
    }
    if ($Action -eq 'stop') {
        if (-not $identity) { throw 'No current-run helper identity' }
        if ($owned) {
            $owned.Kill()
            if (-not $owned.WaitForExit(5000)) { throw 'Helper did not exit' }
            Write-Result 'stopped' $identity.pid
        } else { Write-Result 'exited' }
        exit 0
    }
    if ($helpers.Count) { throw 'A helper already exists; inspect instead of replacing or starting another' }
    if (-not $AuthorityPath -or [IO.Path]::GetFullPath($AuthorityPath) -ne (Join-Path (Join-Path $resolvedRun 'collection') 'browser-helper-authorization.private.json')) { throw 'Current authorization receipt required' }
    $authority = Get-Content -Raw -Encoding utf8 -LiteralPath $AuthorityPath | ConvertFrom-Json
    $age = ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($authority.checkedAt)).TotalSeconds
    if ($authority.schemaVersion -ne 'alibaba.browser-helper-authorization.v1' -or $authority.runId -ne $RunId -or $authority.run -ne $resolvedRun -or
        $authority.nativeDevtools -ne $true -or $authority.hostAllowsDevtools -ne $true -or $authority.ahkHelper -ne $true -or
        -not $authority.userApproval -or -not $authority.hostPolicy -or $age -lt 0 -or $age -gt 60 -or
        $authority.executable -ne $Executable -or $authority.script -ne $ScriptFile -or
        (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash -ne $authority.executableSha256 -or
        (Get-FileHash -LiteralPath $ScriptFile -Algorithm SHA256).Hash -ne $authority.scriptSha256) { throw 'Invalid or stale authorization receipt' }
    if ($ScriptFile.Contains('"') -or [IO.Path]::GetExtension($ScriptFile) -ne '.ahk') { throw 'Invalid script argument' }
    $started = Start-Process -FilePath $Executable -ArgumentList @(('"' + $ScriptFile + '"')) -WindowStyle Hidden -PassThru
    try {
        $started.Refresh()
        $receipt = @{runId=$RunId; run=$resolvedRun; pid=$started.Id; executable=$started.Path;
            startTimeUtcTicks=$started.StartTime.ToUniversalTime().Ticks; script=$ScriptFile;
            executableSha256=$authority.executableSha256; scriptSha256=$authority.scriptSha256}
        Save-Identity $receipt
    } catch {
        # Only this invocation's exact process handle; never a global/name-based kill.
        if (-not $started.HasExited) { $started.Kill() }
        throw
    }
    Write-Result 'started' $started.Id
} catch {
    # Do not leak private paths, arbitrary helper text or permission-prompt contents.
    [Console]::Error.WriteLine('Scoped helper operation failed; preserve identity and inspect locally. No absence or browser-login conclusion is established.')
    exit 2
}
