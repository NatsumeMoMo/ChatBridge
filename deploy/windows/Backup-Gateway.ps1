param([string]$Root='C:\ChatBridge',[Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$taskDestination=[IO.Path]::GetFullPath($Destination).TrimEnd('\')
if(Test-Path -LiteralPath $taskDestination){throw 'Choose a new backup directory'}
if($taskDestination.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Back up outside the gateway root'}
$taskWasRunning=(Get-ScheduledTask 'ChatBridge-Gateway').State -eq 'Running'
try{
    Stop-ScheduledTask 'ChatBridge-Gateway';Start-Sleep -Seconds 2
    New-Item -ItemType Directory -Path $taskDestination | Out-Null
    $taskSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $taskDestination /inheritance:r /grant:r ('*'+$taskSid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Cannot restrict backup directory'}
    Copy-Item -LiteralPath "$taskRoot\data" -Destination $taskDestination -Recurse
    Copy-Item -LiteralPath "$taskRoot\private" -Destination $taskDestination -Recurse
    $taskManifest=@(Get-ChildItem -LiteralPath $taskDestination -Recurse -File | ForEach-Object {@{path=$_.FullName.Substring($taskDestination.Length+1);bytes=$_.Length;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower()}})
    @{utc=[DateTime]::UtcNow.ToString('o');stoppedGateway=$true;dpapiBoundToCurrentUser=$true;portableRecoveryRequiresAuthorityBackup=$true;files=$taskManifest} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath "$taskDestination\manifest.json" -Encoding UTF8
    Write-Host 'Backup complete. Keep it private with the encrypted authority backup and passphrase.'
}finally{if($taskWasRunning){Start-ScheduledTask 'ChatBridge-Gateway'}}
