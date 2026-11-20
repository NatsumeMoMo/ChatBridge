param([string]$Root='C:\ChatBridge',[switch]$UpgradeDependencies)
$ErrorActionPreference='Stop'
$taskSource=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$taskRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
if($taskRoot.Length -le 3 -or $taskSource.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Run updates from a separate source checkout'}
$taskTag=[DateTime]::UtcNow.ToString('yyyyMMddHHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,6)
$taskIncoming=Join-Path $taskRoot ('incoming-'+$taskTag)
New-Item -ItemType Directory -Path $taskIncoming | Out-Null
foreach($taskDirectory in @('gateway','tools','deploy')){Copy-Item -LiteralPath (Join-Path $taskSource $taskDirectory) -Destination $taskIncoming -Recurse}
$taskPython="$taskRoot\runtime\Scripts\python.exe"
$taskLegacy=!(Test-Path -LiteralPath "$taskRoot\private\profile.json") -and (Test-Path -LiteralPath "$taskRoot\private\client-pin.json")
if($taskLegacy){
    if($taskRoot -ne 'C:\ChatBridge'){throw 'Legacy compatibility is limited to its original C:\ChatBridge layout'}
    # 既有桌面入口依赖旧的只读状态脚本，更新网关不能把这些入口一起移走。
    New-Item -ItemType Directory -Path "$taskIncoming\ops" | Out-Null
    Copy-Item -Path "$taskIncoming\deploy\windows\legacy\*" -Destination "$taskIncoming\ops"
}
& $taskPython -m compileall -q "$taskIncoming\gateway\src"
if($LASTEXITCODE -ne 0){throw 'Incoming code does not compile'}
$taskDependencyChange=(Get-FileHash "$taskIncoming\gateway\requirements.windows.lock").Hash -ne (Get-FileHash "$taskRoot\app\gateway\requirements.windows.lock").Hash
if($taskDependencyChange -and !$UpgradeDependencies){throw 'Dependency lock changed. Review it and pass -UpgradeDependencies, or deploy a fresh root'}
$taskWasRunning=(Get-ScheduledTask 'ChatBridge-Gateway').State -eq 'Running'
$taskPrevious=Join-Path $taskRoot ('app-previous-'+$taskTag);$taskSwitched=$false
try{
    Stop-ScheduledTask 'ChatBridge-Gateway';Start-Sleep -Seconds 2
    Rename-Item -LiteralPath "$taskRoot\app" -NewName (Split-Path -Leaf $taskPrevious)
    Rename-Item -LiteralPath $taskIncoming -NewName 'app';$taskSwitched=$true
    if($taskDependencyChange){& $taskPython -m pip install -r "$taskRoot\app\gateway\requirements.windows.lock";if($LASTEXITCODE -ne 0){throw 'Dependency upgrade failed'}}
    if($taskLegacy){
        [IO.File]::WriteAllText("$taskRoot\private\start-pinned-wechat.ps1",[IO.File]::ReadAllText("$taskRoot\app\ops\Start-PinnedWeChat.ps1"),[Text.UTF8Encoding]::new($true))
        [IO.File]::WriteAllText("$taskRoot\private\Start-ChatBridge.ps1",[IO.File]::ReadAllText("$taskRoot\app\ops\Start-ChatBridge.ps1"),[Text.UTF8Encoding]::new($true))
    }else{foreach($taskFile in @('Client-Guard.ps1','Start.ps1')){[IO.File]::WriteAllText((Join-Path "$taskRoot\private" $taskFile),[IO.File]::ReadAllText((Join-Path "$taskRoot\app\deploy\windows" $taskFile)),[Text.UTF8Encoding]::new($true))}}
    if($taskWasRunning){
        Start-ScheduledTask 'ChatBridge-Gateway';$taskHealthy=$false
        for($taskAttempt=0;$taskAttempt -lt 60;$taskAttempt++){
            Start-Sleep -Seconds 1
            $taskState=& $taskPython "$taskRoot\app\tools\status_guest.py" --root $taskRoot | ConvertFrom-Json
            $taskListening=@(Get-NetTCPConnection -State Listen -LocalPort 19443 -ErrorAction SilentlyContinue | Where-Object {$_.OwningProcess -eq $taskState.gatewayPid -and $_.LocalAddress -eq '127.0.0.1'})
            if($taskState.ready -and $taskListening.Count){$taskHealthy=$true;break}
        }
        if(!$taskHealthy){throw 'Updated gateway is not ready'}
    }
    @{ok=$true;previousCode=$taskPrevious;historyAndKeysPreserved=$true;gatewayRemainsStopped=!$taskWasRunning;legacyStartupPreserved=$taskLegacy} | ConvertTo-Json
}catch{
    $taskFailure=$_.Exception.Message
    Stop-ScheduledTask 'ChatBridge-Gateway' -ErrorAction SilentlyContinue
    if($taskSwitched){Rename-Item -LiteralPath "$taskRoot\app" -NewName ('app-failed-'+$taskTag)}
    if(Test-Path -LiteralPath $taskPrevious){Rename-Item -LiteralPath $taskPrevious -NewName 'app'}
    if($taskDependencyChange -and $taskSwitched){& $taskPython -m pip install -r "$taskRoot\app\gateway\requirements.windows.lock";if($LASTEXITCODE -ne 0){throw ('Code restored but dependency rollback failed: '+$taskFailure)}}
    if($taskLegacy){Copy-Item -LiteralPath "$taskRoot\app\ops\Start-PinnedWeChat.ps1" -Destination "$taskRoot\private\start-pinned-wechat.ps1" -Force}
    else{foreach($taskFile in @('Client-Guard.ps1','Start.ps1')){Copy-Item -LiteralPath "$taskRoot\app\deploy\windows\$taskFile" -Destination "$taskRoot\private\$taskFile" -Force}}
    if($taskWasRunning){Start-ScheduledTask 'ChatBridge-Gateway'}
    throw ('Update failed; previous code restored. '+$taskFailure)
}
