param([string]$Root='C:\ChatBridge',[int]$WaitSeconds=900,[string]$RunId='')
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$taskPrivate=Join-Path $taskRoot 'private'
$taskStatePath=Join-Path $taskPrivate 'launcher-status.json'
$taskLogPath=Join-Path $taskPrivate 'launcher.log'
$taskMutex=[Threading.Mutex]::new($false,'Local\ChatBridge.StartWorker')
$taskOwned=$false;$taskPhase='';$taskScheduler=$null;$taskAttempted=@{};$taskRestarted=@{}
$taskState=@{version=2;runId=$RunId;workerPid=$PID;startedUtc=[DateTime]::UtcNow.ToString('o');phase='checking';title='正在检查';detail='检查微信、消息服务与连接通道';steps=@{client='checking';gateway='checking';relay='checking'}}
function Save-State([string]$Phase,[string]$Title,[string]$Detail){
    $taskState.phase=$Phase;$taskState.title=$Title;$taskState.detail=$Detail;$taskState.updatedUtc=[DateTime]::UtcNow.ToString('o')
    $taskState.ok=$Phase -eq 'ready'
    $taskTemporary=$taskStatePath+'.tmp-'+$PID
    [IO.File]::WriteAllText($taskTemporary,($taskState | ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false))
    for($taskWriteAttempt=0;$taskWriteAttempt -lt 5;$taskWriteAttempt++){
        try{Move-Item -LiteralPath $taskTemporary -Destination $taskStatePath -Force;break}
        catch{if($taskWriteAttempt -eq 4){throw};Start-Sleep -Milliseconds 50}
    }
    # 旧诊断入口仍读此文件，应同步本轮状态，避免旧失败记录误导后续排查。
    [IO.File]::WriteAllText((Join-Path $taskPrivate 'startup-status.json'),($taskState | ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false))
    if($script:taskPhase -ne $Phase){[IO.File]::AppendAllText($taskLogPath,([DateTime]::UtcNow.ToString('o')+' '+$Phase+' '+$Detail+"`r`n"),[Text.UTF8Encoding]::new($false));$script:taskPhase=$Phase}
}
function Read-Health {
    $taskJson=& "$taskRoot\runtime\Scripts\python.exe" "$taskRoot\app\tools\startup_probe.py" --root $taskRoot
    if($LASTEXITCODE -ne 0){throw '消息服务的运行环境无法启动，请查看启动日志。'}
    $taskHealth=$taskJson | ConvertFrom-Json
    if($taskHealth.error){throw ('启动检查无法完成：'+$taskHealth.detail)}
    return $taskHealth
}
function Start-Component([string]$Name,[bool]$Missing,[int]$RecoverySeconds=25){
    if(!$Missing){return}
    if(!$script:taskScheduler){$script:taskScheduler=New-Object -ComObject 'Schedule.Service';$script:taskScheduler.Connect()}
    $task=$script:taskScheduler.GetFolder('\').GetTask($Name)
    if(!$task.Enabled){$task.Enabled=$true}
    if(!$script:taskAttempted.ContainsKey($Name)){
        if($task.State -ne 4){$task.Run($null) | Out-Null}
        $script:taskAttempted[$Name]=[DateTime]::UtcNow
        [IO.File]::AppendAllText($taskLogPath,([DateTime]::UtcNow.ToString('o')+' start-request '+$Name+"`r`n"))
    }elseif(!$script:taskRestarted.ContainsKey($Name) -and ([DateTime]::UtcNow-$script:taskAttempted[$Name]).TotalSeconds -gt $RecoverySeconds){
        # 任务标记为运行却没有对应进程时，只恢复该组件一次，不重启正常微信。
        if($task.State -eq 4){$task.Stop(0)}
        $task.Run($null) | Out-Null;$script:taskRestarted[$Name]=$true
        [IO.File]::AppendAllText($taskLogPath,([DateTime]::UtcNow.ToString('o')+' component-recovery '+$Name+"`r`n"))
    }
}
try{
    try{$taskOwned=$taskMutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$taskOwned=$true}
    if(!$taskOwned){exit 0}
    Save-State 'checking' '正在检查' '检查微信、消息服务与连接通道'
    # 守卫在启动客户端前校验完整哈希；重连已验证进程不应重复读取数百 MB 的组件。
    $taskWatch=[Diagnostics.Stopwatch]::StartNew();$taskReadyWatch=[Diagnostics.Stopwatch]::StartNew();$taskShownLogin=$false;$taskLoginPid=0;$taskUiState='unknown';$taskUiChecked=-10;$taskLastShow=-10
    do{
        $taskHealth=Read-Health
        if($taskLoginPid -ne $taskHealth.client.pid){$taskShownLogin=$false;$taskLoginPid=$taskHealth.client.pid;$taskUiState='unknown';$taskUiChecked=-10}
        $taskState.health=$taskHealth
        $taskState.steps.client=if($taskHealth.client.verified){'running'}else{'starting'}
        $taskState.steps.gateway=if($taskHealth.gateway.listening){'running'}else{'starting'}
        $taskState.steps.relay=if($taskHealth.relay.connected){'ready'}else{'starting'}
        if($taskHealth.ready){$taskState.steps.client='ready';$taskState.steps.gateway='ready';Save-State 'ready' '已就绪' '现在可以锁屏虚拟机，在 PC B 打开 ChatBridge 连接。';exit 0}
        Start-Component 'ChatBridge-Gateway' (!$taskHealth.gateway.alive)
        Start-Component 'ChatBridge-RelayClient' (!$taskHealth.relay.alive)
        Start-Component $taskHealth.clientTask (!$taskHealth.client.alive) 180
        if($taskHealth.client.pid -and !$taskHealth.gateway.ready -and $taskWatch.Elapsed.TotalSeconds-$taskUiChecked -ge 5){
            $taskUiChecked=$taskWatch.Elapsed.TotalSeconds
            $taskShow=Join-Path $taskPrivate 'Show-Client.ps1';$taskLoginResult=Join-Path $taskPrivate 'launcher-login.json'
            & $taskShow -ProcessId $taskHealth.client.pid -InspectOnly -ResultFile $taskLoginResult
            $taskFeedback=[IO.File]::ReadAllText($taskLoginResult) | ConvertFrom-Json
            if($taskFeedback.uiState -ne 'unknown'){$taskUiState=$taskFeedback.uiState}
        }
        if($taskUiState -eq 'login'){
            # 手机确认的等待时间不能挤占消息接口初始化的超时预算。
            $taskReadyWatch.Reset()
            $taskState.steps.client='login'
            Save-State 'awaitingLogin' '等待微信登录' '服务已经启动。请按微信窗口提示，在手机确认登录或扫码；完成后会自动继续。'
            if(!$taskShownLogin -and $taskHealth.client.pid -and $taskWatch.Elapsed.TotalSeconds-$taskLastShow -ge 5){
                $taskLastShow=$taskWatch.Elapsed.TotalSeconds
                $taskShow=Join-Path $taskPrivate 'Show-Client.ps1'
                $taskLoginResult=Join-Path $taskPrivate 'launcher-login.json'
                if([IO.File]::Exists($taskShow)){
                    & $taskShow -ProcessId $taskHealth.client.pid -RequestLogin -ResultFile $taskLoginResult
                    $taskFeedback=[IO.File]::ReadAllText($taskLoginResult) | ConvertFrom-Json
                    $taskShownLogin=[bool]$taskFeedback.loginRequested
                }else{$taskShownLogin=$true}
            }
        }elseif($taskUiState -eq 'main' -and !$taskHealth.gateway.ready){
            if(!$taskReadyWatch.IsRunning){$taskReadyWatch.Start()}
            # 主界面证明客户端已经登录，接口标记滞后时不能再次要求用户扫码。
            $taskState.steps.client='running';Save-State 'initializing' '正在初始化消息接口' '微信已登录，正在等待消息接口可读；无需重新登录。'
        }elseif(!$taskHealth.relay.connected -and $taskHealth.relay.alive){Save-State 'connecting' '正在连接通道' '后台服务已启动，正在等待网络连接；不需要重复双击。'}
        else{Save-State 'starting' '正在启动' '正在等待微信与消息服务就绪；正常运行的组件会继续保留。'}
        if($taskUiState -ne 'login' -and !$taskReadyWatch.IsRunning){$taskReadyWatch.Start()}
        if($taskReadyWatch.Elapsed.TotalSeconds -gt 120 -and $taskUiState -ne 'login'){
            if(!$taskHealth.gateway.alive -or !$taskHealth.relay.alive){throw '部分后台组件没有正常启动。已记录具体状态，可点击“查看日志”。'}
            Save-State 'initializing' '正在等待消息接口' '初始化耗时较长，后台仍在检查。可查看日志；无需反复重启或扫码。'
        }
        Start-Sleep -Seconds 1
    }while($taskReadyWatch.Elapsed.TotalSeconds -lt $WaitSeconds)
    if($taskUiState -eq 'login'){Save-State 'awaitingLogin' '等待微信登录' '微信尚未完成登录。完成手机确认后，可点“重新检查”；服务会保持运行。';exit 0}
    $taskErrors=@($taskHealth.accounts | Where-Object {$_.code} | ForEach-Object {$_.code})
    $taskTimeoutDetail=if($taskErrors.Count){'消息接口仍未就绪：'+($taskErrors -join ', ')+'。可查看日志后重新检查。'}else{'连接仍未完成，请检查虚拟机网络或点击“查看日志”。'}
    Save-State 'failed' '暂未就绪' $taskTimeoutDetail
}catch{
    try{Save-State 'failed' '启动未完成' $_.Exception.Message}catch{}
    exit 1
}finally{if($taskOwned){$taskMutex.ReleaseMutex()};$taskMutex.Dispose()}
