param([string]$ResultPath='', [int]$WaitSeconds=90)
$ErrorActionPreference='Stop'
$taskRoot='C:\ChatBridge';$taskPinned='C:\ChatBridgeValidation\runtime\Weixin4110'
$taskResult=@{ok=$false;utc=[DateTime]::UtcNow.ToString('o');startedTasks=@()}
$taskMutex=[Threading.Mutex]::new($false,'Local\ChatBridge.ManualStart');$taskOwned=$false
try {
    $taskOwned=$taskMutex.WaitOne(0)
    if(!$taskOwned){throw '已有启动检查在运行，请查看先前的启动窗口。'}
    Write-Host 'ChatBridge：检查固定微信、网关与中继…'
    $taskPin=Get-Content -LiteralPath "$taskRoot\private\client-pin.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach($taskFile in @(@{path="$taskPinned\Weixin.exe";hash=$taskPin.launcherSha256},@{path="$taskPinned\4.1.10.27\Weixin.dll";hash=$taskPin.coreSha256},@{path="$taskPinned\version.dll";hash=$taskPin.hookSha256})){
        if((Get-FileHash -LiteralPath $taskFile.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskFile.hash){throw '微信组件与固定清单不一致，请先修复版本，不要启动未知版本。'}
    }
    foreach($taskName in 'ChatBridgeValidation-WeChat4Launch','ChatBridge-RelayClient','ChatBridge-Gateway'){
        $task=Get-ScheduledTask -TaskName $taskName
        if($task.State -eq 'Disabled'){Enable-ScheduledTask -TaskName $taskName | Out-Null}
        if($task.State -ne 'Running'){Start-ScheduledTask -TaskName $taskName;$taskResult.startedTasks+=@($taskName)}
    }
    $taskWatch=[Diagnostics.Stopwatch]::StartNew();$taskStatus=$null
    do {
        $taskJson=& "$taskRoot\runtime\Scripts\python.exe" "$taskRoot\app\ops\Guest-Status.py"
        if($LASTEXITCODE -eq 0){$taskStatus=$taskJson | ConvertFrom-Json}
        $taskListener=@(Get-NetTCPConnection -State Listen -LocalPort 19443 -ErrorAction SilentlyContinue)
        $taskGateway=if($taskStatus.gatewayPid){Get-CimInstance Win32_Process -Filter ('ProcessId='+$taskStatus.gatewayPid)}else{$null}
        $taskGatewayOk=$taskGateway -and $taskGateway.ExecutablePath -in @("$taskRoot\runtime\Scripts\python.exe",'C:\ChatBridgeValidation\runtime\Python310\python.exe') -and $taskGateway.CommandLine -match '-m\s+chatbridge\.main' -and @($taskListener | Where-Object OwningProcess -eq $taskStatus.gatewayPid).Count -gt 0
        $taskRelays=@(Get-CimInstance Win32_Process -Filter "Name='frpc.exe'" | Where-Object ExecutablePath -eq "$taskRoot\bin\frpc.exe")
        $taskRelayConnected=@(foreach($taskRelay in $taskRelays){Get-NetTCPConnection -State Established -OwningProcess $taskRelay.ProcessId -ErrorAction SilentlyContinue}).Count -gt 0
        $taskClient=@(Get-CimInstance Win32_Process -Filter "Name='Weixin.exe'" | Where-Object {$_.ExecutablePath -eq "$taskPinned\Weixin.exe" -and $_.CommandLine -notmatch '--type='})
        $taskGuard=if(Test-Path -LiteralPath "$taskRoot\private\client-start-state.json"){Get-Content -LiteralPath "$taskRoot\private\client-start-state.json" -Raw -Encoding UTF8 | ConvertFrom-Json}else{$null}
        $taskClientOk=$taskClient.Count -eq 1 -and $taskGuard.phase -eq 'running-verified' -and $taskGuard.pid -eq $taskClient[0].ProcessId
        if($taskStatus.ready -and $taskGatewayOk -and $taskRelayConnected -and $taskClientOk){$taskResult.ok=$true;break}
        Write-Progress -Activity '启动 ChatBridge' -Status '等待微信登录、网关采集和中继连接；如微信提示，请用手机确认登录或扫码。'
        if($taskWatch.Elapsed.TotalSeconds -lt $WaitSeconds){Start-Sleep -Seconds 2}
    }while($taskWatch.Elapsed.TotalSeconds -lt $WaitSeconds)
    Write-Progress -Activity '启动 ChatBridge' -Completed
    $taskResult.accounts=$taskStatus.accounts;$taskResult.gatewayVerified=[bool]$taskGatewayOk;$taskResult.relayConnected=$taskRelayConnected;$taskResult.clientVerified=$taskClientOk
    if($taskResult.ok){Write-Host '已就绪。现在可锁屏虚拟机，在 PC B 打开 ChatBridge 插件连接。' -ForegroundColor Green}
    else{
        $taskResult.awaitingLogin=@($taskStatus.accounts | Where-Object status -eq 'loginRequired').Count -gt 0
        Write-Host '服务已请求启动，但尚未全部就绪。请查看微信窗口，完成手机确认或扫码；后台会继续尝试连接。' -ForegroundColor Yellow
        Write-Host ('诊断状态：'+($taskResult | ConvertTo-Json -Depth 4 -Compress))
    }
}catch{$taskResult.error=$_.Exception.Message;Write-Host ('启动未完成：'+$taskResult.error) -ForegroundColor Red}
finally{if($taskOwned){$taskMutex.ReleaseMutex()};$taskMutex.Dispose()}
$taskResult.completedUtc=[DateTime]::UtcNow.ToString('o')
$taskResult | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$taskRoot\private\startup-status.json" -Encoding UTF8
if($ResultPath){$taskResult | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ResultPath -Encoding UTF8}
if(!$ResultPath -and !$taskResult.ok){exit 1}
