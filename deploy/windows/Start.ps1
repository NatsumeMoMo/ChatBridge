param([string]$Root='C:\ChatBridge',[int]$WaitSeconds=90)
$ErrorActionPreference='Stop'
$taskMutex=[Threading.Mutex]::new($false,'Local\ChatBridge.ManualStart');$taskOwned=$false
try{
    $taskOwned=$taskMutex.WaitOne(0);if(!$taskOwned){throw 'Another startup check is already running'}
    foreach($taskName in @('ChatBridge-Client','ChatBridge-RelayClient','ChatBridge-Gateway')){
        $task=Get-ScheduledTask -TaskName $taskName
        if($task.State -eq 'Disabled'){Enable-ScheduledTask -TaskName $taskName | Out-Null}
        if($task.State -ne 'Running'){Start-ScheduledTask -TaskName $taskName}
    }
    $taskWatch=[Diagnostics.Stopwatch]::StartNew()
    do{
        $taskStatus=& "$Root\runtime\Scripts\python.exe" "$Root\app\tools\status_guest.py" --root $Root | ConvertFrom-Json
        $taskListener=@(Get-NetTCPConnection -State Listen -LocalPort 19443 -ErrorAction SilentlyContinue | Where-Object {$_.LocalAddress -eq '127.0.0.1' -and $_.OwningProcess -eq $taskStatus.gatewayPid})
        $taskRelays=@(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" | Where-Object ExecutablePath -eq "$Root\bin\frpc.exe")
        $taskConnected=@(foreach($taskRelay in $taskRelays){Get-NetTCPConnection -State Established -OwningProcess $taskRelay.ProcessId -ErrorAction SilentlyContinue}).Count -gt 0
        if($taskStatus.ready -and $taskListener.Count -gt 0 -and $taskConnected){Write-Host 'Ready. The VM can stay locked while PC B connects.';exit 0}
        Write-Host 'Waiting for the current gateway, relay and WeChat login...'
        Start-Sleep -Seconds 2
    }while($taskWatch.Elapsed.TotalSeconds -lt $WaitSeconds)
    throw 'Not ready yet. Open the VM desktop and complete WeChat login, then run again.'
}finally{if($taskOwned){$taskMutex.ReleaseMutex()};$taskMutex.Dispose()}
