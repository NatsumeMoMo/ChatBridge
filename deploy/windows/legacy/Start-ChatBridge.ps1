param([string]$ResultPath='',[int]$WaitSeconds=900)
$ErrorActionPreference='Stop'
$taskRoot='C:\ChatBridge'
if($ResultPath){
    & "$taskRoot\private\Start-Worker.ps1" -Root $taskRoot -WaitSeconds $WaitSeconds -RunId ([Guid]::NewGuid().ToString('N'))
    if([IO.File]::Exists("$taskRoot\private\launcher-status.json")){[IO.File]::Copy("$taskRoot\private\launcher-status.json",$ResultPath,$true)}
    exit $LASTEXITCODE
}
# 兼容旧路径，桌面只保留新的可视化入口。
Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList '-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\ChatBridge\private\Launcher.ps1' -WindowStyle Hidden
