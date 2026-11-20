$ErrorActionPreference='Stop'
$taskRoot='C:\ChatBridge';$taskPinned='C:\ChatBridgeValidation\runtime\Weixin4110'
function Save-Task($value){$value | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$taskRoot\private\client-start-state.json" -Encoding UTF8}
function Test-TaskHook {
    $taskQueue='C:\ChatBridgeValidation\private\hook-queue'
    $taskId=[Guid]::NewGuid().ToString('N');$taskRequest="$taskQueue\req-$taskId.json";$taskResponse="$taskQueue\res-$taskId.json"
    [IO.File]::WriteAllText($taskRequest+'.tmp',(@{id=$taskId;op='status'} | ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath ($taskRequest+'.tmp') -Destination $taskRequest
    for($taskPoll=0;$taskPoll -lt 150;$taskPoll++){
        Start-Sleep -Milliseconds 100
        if(Test-Path -LiteralPath $taskResponse){
            $taskReply=Get-Content -LiteralPath $taskResponse -Raw -Encoding UTF8 | ConvertFrom-Json
            [IO.File]::Delete($taskResponse)
            return $taskReply.ok -eq $true
        }
    }
    return $false
}
try{
    $taskPin=Get-Content -LiteralPath "$taskRoot\private\client-pin.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach($taskFile in @(@{path="$taskPinned\Weixin.exe";hash=$taskPin.launcherSha256},@{path="$taskPinned\4.1.10.27\Weixin.dll";hash=$taskPin.coreSha256},@{path="$taskPinned\version.dll";hash=$taskPin.hookSha256})){
        if((Get-FileHash -LiteralPath $taskFile.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $taskFile.hash){throw 'Pinned component hash mismatch'}
    }
    $taskExisting=@(Get-CimInstance Win32_Process -Filter "Name='Weixin.exe'" | Where-Object {$_.ExecutablePath -eq "$taskPinned\Weixin.exe" -and $_.CommandLine -notmatch '--type='})
    if($taskExisting.Count -gt 1){throw 'Multiple pinned client processes require inspection'}
    if($taskExisting.Count){$taskProcess=Get-Process -Id $taskExisting[0].ProcessId}
    else{$taskProcess=Start-Process -FilePath "$taskPinned\Weixin.exe" -ArgumentList @('StartPort=30001','RecvType=1','CallBackURL=http://127.0.0.1:18998/recv') -WorkingDirectory $taskPinned -WindowStyle Normal -PassThru}
    # 启动后的实际加载模块也须核对，避免只验证磁盘上没有被使用的旧文件。
    $taskVerified=$false
    for($taskAttempt=0;$taskAttempt -lt 15;$taskAttempt++){
        Start-Sleep -Seconds 1
        if($taskProcess.HasExited){throw 'Pinned client exited during startup'}
        $taskCore=@((Get-Process -Id $taskProcess.Id).Modules | Where-Object ModuleName -eq 'Weixin.dll')
        if($taskCore.Count -eq 1){
            if($taskCore[0].FileName -ne "$taskPinned\4.1.10.27\Weixin.dll"){throw 'Unexpected loaded client core'}
            $taskVerified=$true;break
        }
    }
    if(!$taskVerified){throw 'Loaded client core was not observed'}
    # 上游 Hook 会从常规模块列表移除自身，用只读队列回包判断它是否就绪。
    if(!(Test-TaskHook)){throw 'Pinned Hook did not respond'}
    Save-Task @{phase='running-verified';pid=$taskProcess.Id;version='4.1.10.27';hookFileSha256=$taskPin.hookSha256;hookResponded=$true;utc=[DateTime]::UtcNow.ToString('o')}
    $taskProcess.WaitForExit()
}catch{Save-Task @{phase='failed';error=$_.Exception.Message;utc=[DateTime]::UtcNow.ToString('o')};exit 1}
