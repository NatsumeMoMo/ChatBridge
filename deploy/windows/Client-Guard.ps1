param([string]$Root='C:\ChatBridge')
$ErrorActionPreference='Stop'
$taskProfile=Get-Content -LiteralPath "$Root\private\profile.json" -Raw -Encoding UTF8 | ConvertFrom-Json
try{
    foreach($taskFile in @(@($taskProfile.wechatExe,$taskProfile.hashes.launcher),@($taskProfile.wechatCore,$taskProfile.hashes.core),@($taskProfile.hookDll,$taskProfile.hashes.hook))){
        if((Get-FileHash -LiteralPath $taskFile[0] -Algorithm SHA256).Hash -ne $taskFile[1]){throw 'Pinned component hash mismatch'}
    }
    $taskExisting=@(Get-CimInstance Win32_Process -Filter "Name='Weixin.exe'" | Where-Object {$_.ExecutablePath -eq $taskProfile.wechatExe -and $_.CommandLine -notmatch '--type='})
    if($taskExisting.Count -gt 1){throw 'Multiple client main processes require inspection'}
    if($taskExisting.Count){$taskClient=Get-Process -Id $taskExisting[0].ProcessId}
    else{$taskClient=Start-Process -FilePath $taskProfile.wechatExe -ArgumentList @('StartPort=30001','RecvType=1','CallBackURL=http://127.0.0.1:18998/recv') -WorkingDirectory (Split-Path $taskProfile.wechatExe) -WindowStyle Hidden -PassThru}
    $taskReady=$false
    for($taskAttempt=0;$taskAttempt -lt 30;$taskAttempt++){
        Start-Sleep -Seconds 1
        if($taskClient.HasExited){throw 'Pinned client exited during startup'}
        $taskCore=@((Get-Process -Id $taskClient.Id).Modules | Where-Object ModuleName -eq 'Weixin.dll')
        if($taskCore.Count -eq 1){if($taskCore[0].FileName -ne $taskProfile.wechatCore){throw 'Unexpected loaded client core'};$taskReady=$true;break}
    }
    if(!$taskReady){throw 'Client core was not observed'}
    @{phase='running-verified';pid=$taskClient.Id;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath "$Root\private\client-start-state.json" -Encoding UTF8
    $taskClient.WaitForExit()
}catch{@{phase='failed';error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath "$Root\private\client-start-state.json" -Encoding UTF8;exit 1}
