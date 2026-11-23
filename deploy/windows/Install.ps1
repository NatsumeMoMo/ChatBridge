param([Parameter(Mandatory=$true)][string]$ProfilePath,[Parameter(Mandatory=$true)][string]$EnrollmentPath)
$ErrorActionPreference='Stop'
$taskSource=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$taskProfile=Get-Content -LiteralPath $ProfilePath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRoot=[IO.Path]::GetFullPath($taskProfile.root).TrimEnd('\')
if($taskRoot.Length -le 3 -or $taskRoot.StartsWith($taskSource+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Use a dedicated install root outside the source checkout'}
if(Test-Path -LiteralPath "$taskRoot\private\secrets.dpapi"){throw 'Existing installation: use Update.ps1; keys and history will not be replaced'}
function Assert-TaskHash([string]$Path,[string]$Hash){
    if($Hash -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Hash){throw ('Hash mismatch: '+$Path)}
}
Assert-TaskHash $taskProfile.frpc $taskProfile.frpcSha256
Assert-TaskHash $taskProfile.wechatExe $taskProfile.hashes.launcher
Assert-TaskHash $taskProfile.wechatCore $taskProfile.hashes.core
Assert-TaskHash $taskProfile.hookDll $taskProfile.hashes.hook
if(!(Test-Path -LiteralPath $taskProfile.accountRoot -PathType Container)){throw 'Log in to the dedicated client and set the correct accountRoot first'}
foreach($taskPython in @($taskProfile.python,$taskProfile.voicePython,$taskProfile.codecPython)){if(!(Test-Path -LiteralPath $taskPython)){throw ('Python environment missing: '+$taskPython)}}
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
$taskSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $taskRoot /inheritance:r /grant:r ('*'+$taskSid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot restrict installation directory'}
foreach($taskDirectory in @('app','bin','private','data','staging')){New-Item -ItemType Directory -Path (Join-Path $taskRoot $taskDirectory) -Force | Out-Null}
$taskHookPrivate=Split-Path $taskProfile.hookQueue
New-Item -ItemType Directory -Path $taskProfile.hookQueue -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $taskHookPrivate 'upstream-logs') -Force | Out-Null
& icacls.exe $taskHookPrivate /inheritance:r /grant:r ('*'+$taskSid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'Cannot restrict Hook private directory'}
foreach($taskDirectory in @('gateway','tools','deploy')){Copy-Item -LiteralPath (Join-Path $taskSource $taskDirectory) -Destination "$taskRoot\app" -Recurse -Force}
Copy-Item -LiteralPath $taskProfile.frpc -Destination "$taskRoot\bin\frpc.exe"
& $taskProfile.python -m venv "$taskRoot\runtime"
if($LASTEXITCODE -ne 0){throw 'Cannot create gateway environment'}
$taskPython="$taskRoot\runtime\Scripts\python.exe"
& $taskPython -m pip install -r "$taskRoot\app\gateway\requirements.windows.lock" -e "$taskRoot\app\gateway"
if($LASTEXITCODE -ne 0){throw 'Gateway dependency installation failed'}
& $taskPython "$taskRoot\app\tools\enroll_guest.py" --root $taskRoot --enrollment ([IO.Path]::GetFullPath($EnrollmentPath))
if($LASTEXITCODE -ne 0){throw 'Enrollment failed; no tasks were registered'}
$taskConfig=@{bind='127.0.0.1';port=19443;pollSeconds=0.5;dataDirectory="$taskRoot\data";staging="$taskRoot\staging";secretsFile="$taskRoot\private\secrets.dpapi";devicesFile="$taskRoot\private\devices.json";logFile="$taskRoot\private\gateway.log";adapters=@(@{type='wechat';accountId='wechat:primary';hookQueue=$taskProfile.hookQueue;accountRoot=$taskProfile.accountRoot;ownId=$taskProfile.ownId;voicePython=$taskProfile.voicePython;codecPython=$taskProfile.codecPython;backgroundCaptureLimit=1;contactPageSeconds=2;tableRefreshSeconds=30})}
$taskConfig | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$taskRoot\private\gateway.json" -Encoding UTF8
$taskProfile | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$taskRoot\private\profile.json" -Encoding UTF8
foreach($taskName in @('Client-Guard.ps1','Start.ps1')){[IO.File]::WriteAllText((Join-Path "$taskRoot\private" $taskName),[IO.File]::ReadAllText((Join-Path $PSScriptRoot $taskName)),[Text.UTF8Encoding]::new($true))}
$taskUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskPrincipal=New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Highest
$taskSettings=New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskTrigger=New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$taskActions=@{
    'ChatBridge-Client'=(New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskRoot+'\private\Client-Guard.ps1" -Root "'+$taskRoot+'"'))
    'ChatBridge-Gateway'=(New-ScheduledTaskAction -Execute $taskPython -Argument ('-m chatbridge.main --config "'+$taskRoot+'\private\gateway.json"') -WorkingDirectory "$taskRoot\app\gateway")
    'ChatBridge-RelayClient'=(New-ScheduledTaskAction -Execute "$taskRoot\bin\frpc.exe" -Argument ('-c "'+$taskRoot+'\private\frpc.toml"'))
}
foreach($taskName in $taskActions.Keys){Register-ScheduledTask -TaskName $taskName -Action $taskActions[$taskName] -Principal $taskPrincipal -Settings $taskSettings -Trigger $taskTrigger -Force | Out-Null}
& (Join-Path $PSScriptRoot 'Install-Launcher.ps1') -Root $taskRoot -NoLaunch
Write-Host 'Installed. Use the desktop launcher after completing Windows / WeChat login.'
