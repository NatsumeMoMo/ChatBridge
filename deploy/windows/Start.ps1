param([string]$Root='C:\ChatBridge',[int]$WaitSeconds=900,[switch]$Headless)
$ErrorActionPreference='Stop'
$taskPrivate=Join-Path ([IO.Path]::GetFullPath($Root)) 'private'
if($Headless){& (Join-Path $taskPrivate 'Start-Worker.ps1') -Root $Root -WaitSeconds $WaitSeconds -RunId ([Guid]::NewGuid().ToString('N'));exit $LASTEXITCODE}
# 旧命令入口也打开同一启动器，避免桌面和脚本走两套状态判断。
$taskArguments='-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskPrivate+'\Launcher.ps1" -Root "'+$Root+'"'
Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $taskArguments -WindowStyle Hidden
