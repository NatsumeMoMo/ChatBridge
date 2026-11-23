param([string]$Root='C:\ChatBridge',[switch]$NoLaunch)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$taskPrivate=Join-Path $taskRoot 'private'
if(![IO.File]::Exists((Join-Path $taskPrivate 'gateway.json'))){throw 'Install the gateway before its desktop launcher'}
$taskBackup=Join-Path $taskPrivate ('launcher-backups\'+[DateTime]::UtcNow.ToString('yyyyMMddHHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,6))
[IO.Directory]::CreateDirectory($taskBackup) | Out-Null
foreach($taskName in @('Launcher.ps1','Start-Worker.ps1','Show-Client.ps1','Client-Guard.ps1')){
    $taskSource=Join-Path $PSScriptRoot $taskName;$taskTarget=Join-Path $taskPrivate $taskName
    if([IO.File]::Exists($taskTarget)){[IO.File]::Copy($taskTarget,(Join-Path $taskBackup $taskName),$false)}
    # Windows PowerShell 5 需要 BOM，桌面直接双击也必须正确显示中文并解析脚本。
    [IO.File]::WriteAllText($taskTarget,[IO.File]::ReadAllText($taskSource,[Text.Encoding]::UTF8),[Text.UTF8Encoding]::new($true))
}
$taskCompatibility=if([IO.File]::Exists((Join-Path $taskPrivate 'profile.json'))){@{source='Start.ps1';target='Start.ps1'}}else{@{source='legacy\Start-ChatBridge.ps1';target='Start-ChatBridge.ps1'}}
$taskCompatTarget=Join-Path $taskPrivate $taskCompatibility.target
if([IO.File]::Exists($taskCompatTarget)){[IO.File]::Copy($taskCompatTarget,(Join-Path $taskBackup $taskCompatibility.target),$false)}
[IO.File]::WriteAllText($taskCompatTarget,[IO.File]::ReadAllText((Join-Path $PSScriptRoot $taskCompatibility.source),[Text.Encoding]::UTF8),[Text.UTF8Encoding]::new($true))
if($taskCompatibility.target -eq 'Start-ChatBridge.ps1'){
    $taskLegacyGuard=Join-Path $taskPrivate 'start-pinned-wechat.ps1'
    if([IO.File]::Exists($taskLegacyGuard)){[IO.File]::Copy($taskLegacyGuard,(Join-Path $taskBackup 'start-pinned-wechat.ps1'),$false)}
    [IO.File]::WriteAllText($taskLegacyGuard,[IO.File]::ReadAllText((Join-Path $PSScriptRoot 'legacy\Start-PinnedWeChat.ps1'),[Text.Encoding]::UTF8),[Text.UTF8Encoding]::new($true))
}
$taskProbeSource=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\tools\startup_probe.py'))
$taskProbeTarget=Join-Path $taskRoot 'app\tools\startup_probe.py'
[IO.Directory]::CreateDirectory((Split-Path $taskProbeTarget)) | Out-Null
if($taskProbeSource -ne $taskProbeTarget){[IO.File]::Copy($taskProbeSource,$taskProbeTarget,$true)}
$taskDesktop=[Environment]::GetFolderPath('Desktop')
foreach($taskName in @('启动 ChatBridge.cmd','Start ChatBridge.cmd')){
    $taskOld=Join-Path $taskDesktop $taskName
    if([IO.File]::Exists($taskOld)){[IO.File]::Move($taskOld,(Join-Path $taskBackup $taskName))}
}
$taskShell=New-Object -ComObject WScript.Shell
$taskShortcutPath=Join-Path $taskDesktop '启动 ChatBridge.lnk'
if([IO.File]::Exists($taskShortcutPath)){[IO.File]::Copy($taskShortcutPath,(Join-Path $taskBackup '启动 ChatBridge.lnk'),$false)}
$taskShortcut=$taskShell.CreateShortcut($taskShortcutPath)
$taskShortcut.TargetPath="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$taskShortcut.Arguments='-NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskPrivate+'\Launcher.ps1" -Root "'+$taskRoot+'"'
$taskShortcut.WorkingDirectory=$taskPrivate;$taskShortcut.Description='启动微信、消息服务与连接通道';$taskShortcut.IconLocation="$env:SystemRoot\System32\shell32.dll,16";$taskShortcut.WindowStyle=7;$taskShortcut.Save()
@{ok=$true;desktop=$taskShortcutPath;backup=$taskBackup;passwordStored=$false} | ConvertTo-Json
if(!$NoLaunch){Start-Process -FilePath $taskShortcut.TargetPath -ArgumentList $taskShortcut.Arguments -WindowStyle Hidden}
