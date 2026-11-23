param([string]$Root='C:\ChatBridge')
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$taskPrivate=Join-Path $taskRoot 'private'
function Save-Guard($State){$State.guardPid=$PID;[IO.File]::WriteAllText((Join-Path $taskPrivate 'client-start-state.json'),($State | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))}
function Test-Hook([string]$Queue){
    $taskId=[Guid]::NewGuid().ToString('N');$taskRequest=Join-Path $Queue ('req-'+$taskId+'.json');$taskResponse=Join-Path $Queue ('res-'+$taskId+'.json')
    [IO.File]::WriteAllText(($taskRequest+'.tmp'),(@{id=$taskId;op='status'} | ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    [IO.File]::Move(($taskRequest+'.tmp'),$taskRequest)
    $taskWatch=[Diagnostics.Stopwatch]::StartNew()
    do{
        if([IO.File]::Exists($taskResponse)){
            try{$taskReply=[IO.File]::ReadAllText($taskResponse) | ConvertFrom-Json;[IO.File]::Delete($taskResponse);return [bool]$taskReply.ok}catch{}
        }
        Start-Sleep -Milliseconds 100
    }while($taskWatch.Elapsed.TotalSeconds -lt 20)
    return $false
}
try{
    Save-Guard @{phase='checking';utc=[DateTime]::UtcNow.ToString('o')}
    $taskProfilePath=Join-Path $taskPrivate 'profile.json'
    if([IO.File]::Exists($taskProfilePath)){$taskProfile=[IO.File]::ReadAllText($taskProfilePath) | ConvertFrom-Json}
    else{
        $taskPin=[IO.File]::ReadAllText((Join-Path $taskPrivate 'client-pin.json')) | ConvertFrom-Json
        $taskPinned='C:\ChatBridgeValidation\runtime\Weixin4110'
        $taskProfile=@{wechatExe="$taskPinned\Weixin.exe";wechatCore="$taskPinned\4.1.10.27\Weixin.dll";hookDll="$taskPinned\version.dll";hookQueue='C:\ChatBridgeValidation\private\hook-queue';hashes=@{launcher=$taskPin.launcherSha256;core=$taskPin.coreSha256;hook=$taskPin.hookSha256}}
    }
    foreach($taskFile in @(@{path=$taskProfile.wechatExe;hash=$taskProfile.hashes.launcher},@{path=$taskProfile.wechatCore;hash=$taskProfile.hashes.core},@{path=$taskProfile.hookDll;hash=$taskProfile.hashes.hook})){
        if((Get-FileHash -LiteralPath $taskFile.path -Algorithm SHA256).Hash -ne $taskFile.hash){throw 'Pinned component hash mismatch'}
    }
    # 辅助进程也叫 Weixin，只有实际加载核心的进程可以被守卫接管。
    $taskExisting=@(foreach($taskCandidate in [Diagnostics.Process]::GetProcessesByName('Weixin')){
        try{if($taskCandidate.MainModule.FileName -eq $taskProfile.wechatExe -and @($taskCandidate.Modules | Where-Object ModuleName -eq 'Weixin.dll').Count){$taskCandidate}}catch{}
    })
    if($taskExisting.Count -gt 1){throw 'Multiple client main processes require inspection'}
    if($taskExisting.Count){$taskClient=$taskExisting[0]}
    else{$taskClient=Start-Process -FilePath $taskProfile.wechatExe -ArgumentList @('StartPort=30001','RecvType=1','CallBackURL=http://127.0.0.1:18998/recv') -WorkingDirectory (Split-Path $taskProfile.wechatExe) -WindowStyle Hidden -PassThru}
    Save-Guard @{phase='starting';pid=$taskClient.Id;utc=[DateTime]::UtcNow.ToString('o')}
    $taskReady=$false
    for($taskAttempt=0;$taskAttempt -lt 30;$taskAttempt++){
        Start-Sleep -Seconds 1
        if($taskClient.HasExited){throw 'Pinned client exited during startup'}
        $taskCore=@((Get-Process -Id $taskClient.Id).Modules | Where-Object ModuleName -eq 'Weixin.dll')
        if($taskCore.Count -eq 1){if($taskCore[0].FileName -ne $taskProfile.wechatCore){throw 'Unexpected loaded client core'};$taskReady=$true;break}
    }
    if(!$taskReady){throw 'Client core was not observed'}
    if(!(Test-Hook $taskProfile.hookQueue)){throw 'Pinned Hook did not respond'}
    Save-Guard @{phase='running-verified';pid=$taskClient.Id;coreVerified=$true;hookResponded=$true;hookFileSha256=$taskProfile.hashes.hook;utc=[DateTime]::UtcNow.ToString('o')}
    $taskClient.WaitForExit()
}catch{Save-Guard @{phase='failed';error=$_.Exception.Message;utc=[DateTime]::UtcNow.ToString('o')};exit 1}
