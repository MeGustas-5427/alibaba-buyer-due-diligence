# Synthetic process provider: never launches, inspects or kills a real process.
[CmdletBinding()]
param([string]$Case, [string]$Action, [string]$RunPath, [string]$Executable, [string]$ScriptFile, [string]$HelperScript)
$ErrorActionPreference = 'Stop'
function New-FakeProcess {
    $process = [pscustomobject]@{Id=987654; Path=$Executable; StartTime=[datetime]::new(2026,10,5,0,0,0,[DateTimeKind]::Utc); Handle=123; HasExited=$false}
    $process | Add-Member ScriptMethod Refresh { }
    $process | Add-Member ScriptMethod Kill { [IO.File]::WriteAllText((Join-Path $RunPath 'synthetic-kill-called'), 'SYNTHETIC') }
    $process | Add-Member ScriptMethod WaitForExit { param($Milliseconds); return $true }
    return $process
}
function Get-Process {
    [CmdletBinding()]
    param([string]$Name, [int]$Id)
    if ($Case -eq 'query_failed') { Write-Error 'SYNTHETIC query failed' -ErrorId 'SyntheticInspectionError'; return }
    if ($Case -in @('verified','pid_reused','script_changed','exited') -and $Name) { throw 'Recorded identities must use a scoped PID query' }
    if ($Case -in @('verified','pid_reused','script_changed','unowned','existing_start')) { return New-FakeProcess }
}
function Start-Process {
    [CmdletBinding()]
    param([string]$FilePath, [string[]]$ArgumentList, [string]$WindowStyle, [switch]$PassThru)
    if ($Case -ne 'start' -or $WindowStyle -ne 'Hidden' -or -not $PassThru -or
        $FilePath -ne $Executable -or $ArgumentList.Count -ne 1 -or $ArgumentList[0] -ne ('"'+$ScriptFile+'"')) { throw 'Unsafe synthetic start arguments' }
    [IO.File]::WriteAllText((Join-Path $RunPath 'synthetic-start-called'), 'SYNTHETIC')
    return New-FakeProcess
}
$invoke = @{Action=$Action; RunPath=$RunPath; RunId=(Split-Path -Leaf $RunPath);
    RecordPath=(Join-Path $RunPath 'collection/browser-helper-identity.private.json'); Executable=$Executable; ScriptFile=$ScriptFile}
if ($Action -eq 'start') { $invoke.AuthorityPath = Join-Path $RunPath 'collection/browser-helper-authorization.private.json' }
& $HelperScript @invoke
if ($LASTEXITCODE -ne 0) {
    # These errors contain synthetic paths/processes only; production never emits raw exceptions.
    foreach ($problem in $Error) { [Console]::Error.WriteLine(($problem | Out-String)) }
}
exit $LASTEXITCODE
