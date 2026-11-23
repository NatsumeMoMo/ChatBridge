param([string]$Root='C:\ChatBridge')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$taskRoot=[IO.Path]::GetFullPath($Root).TrimEnd('\')
$taskPrivate=Join-Path $taskRoot 'private'
$taskMutex=[Threading.Mutex]::new($false,'Local\ChatBridge.Launcher');$taskOwned=$false
try{$taskOwned=$taskMutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$taskOwned=$true}
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class ChatBridgeLauncherWindow{[DllImport("user32.dll",CharSet=CharSet.Unicode)]public static extern IntPtr FindWindow(string c,string n);[DllImport("user32.dll")]public static extern bool ShowWindowAsync(IntPtr h,int c);[DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);[DllImport("user32.dll")]public static extern bool AllowSetForegroundWindow(uint p);}'
if(!$taskOwned){$taskExisting=[ChatBridgeLauncherWindow]::FindWindow($null,'ChatBridge 启动');if($taskExisting -ne [IntPtr]::Zero){[ChatBridgeLauncherWindow]::ShowWindowAsync($taskExisting,9) | Out-Null;[ChatBridgeLauncherWindow]::SetForegroundWindow($taskExisting) | Out-Null};$taskMutex.Dispose();exit}
$script:taskWorker=$null;$script:taskWorkerOwned=$false;$script:taskRunId='';$script:taskLastState=$null
$taskBg=[Drawing.ColorTranslator]::FromHtml('#1b1e24');$taskFg=[Drawing.ColorTranslator]::FromHtml('#dfe5ec');$taskMuted=[Drawing.ColorTranslator]::FromHtml('#98a3b3')
$taskForm=New-Object Windows.Forms.Form
$taskForm.Text='ChatBridge 启动';$taskForm.ClientSize=[Drawing.Size]::new(470,338);$taskForm.StartPosition='CenterScreen';$taskForm.FormBorderStyle='FixedDialog';$taskForm.MaximizeBox=$false;$taskForm.BackColor=$taskBg;$taskForm.ForeColor=$taskFg;$taskForm.Font=[Drawing.Font]::new('Microsoft YaHei UI',9)
function New-Text([string]$Text,[int]$X,[int]$Y,[int]$Width,[int]$Height){$node=[Windows.Forms.Label]::new();$node.Text=$Text;$node.Location=[Drawing.Point]::new($X,$Y);$node.Size=[Drawing.Size]::new($Width,$Height);$taskForm.Controls.Add($node);return $node}
$taskBrand=New-Text 'ChatBridge' 26 20 420 24;$taskBrand.Font=[Drawing.Font]::new('Segoe UI',12,[Drawing.FontStyle]::Bold)
$taskTitle=New-Text '正在检查…' 26 58 420 30;$taskTitle.Font=[Drawing.Font]::new('Microsoft YaHei UI',15,[Drawing.FontStyle]::Bold)
$taskDetail=New-Text '检查微信、消息服务与连接通道。' 27 99 413 48;$taskDetail.ForeColor=$taskMuted
$taskRows=@{}
$taskRowY=159
foreach($taskItem in @(@('client','微信'),@('gateway','消息服务'),@('relay','连接通道'))){
    $taskDot=New-Text '●' 29 $taskRowY 20 24;$taskDot.ForeColor=$taskMuted
    $taskLabel=New-Text $taskItem[1] 56 $taskRowY 180 24
    $taskValue=New-Text '检查中' 288 $taskRowY 148 24;$taskValue.TextAlign='TopRight';$taskValue.ForeColor=$taskMuted
    $taskRows[$taskItem[0]]=@{dot=$taskDot;value=$taskValue};$taskRowY+=33
}
function New-Action([string]$Text,[int]$X,[int]$Width){$node=[Windows.Forms.Button]::new();$node.Text=$Text;$node.Location=[Drawing.Point]::new($X,285);$node.Size=[Drawing.Size]::new($Width,32);$node.FlatStyle='Flat';$node.FlatAppearance.BorderSize=1;$node.FlatAppearance.BorderColor=[Drawing.ColorTranslator]::FromHtml('#424955');$node.ForeColor=$taskFg;$node.BackColor=[Drawing.ColorTranslator]::FromHtml('#272c34');$taskForm.Controls.Add($node);return $node}
$taskOpen=New-Action '打开微信' 26 112
$taskRetry=New-Action '重新检查' 146 100
$taskLog=New-Action '查看日志' 254 88
$taskClose=New-Action '关闭' 350 94
function Start-Check {
    if($script:taskWorker -and !$script:taskWorker.HasExited){return}
    $script:taskRunId=[Guid]::NewGuid().ToString('N');$script:taskLastState=$null
    $taskTitle.Text='正在检查…';$taskDetail.Text='检查微信、消息服务与连接通道。'
    try{
        $taskStart=[Diagnostics.ProcessStartInfo]::new("$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe")
        $taskStart.UseShellExecute=$false;$taskStart.CreateNoWindow=$true
        $taskStart.Arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskPrivate+'\Start-Worker.ps1" -Root "'+$taskRoot+'" -RunId '+$script:taskRunId
        $script:taskWorker=[Diagnostics.Process]::Start($taskStart);$script:taskWorkerOwned=$true
        [ChatBridgeLauncherWindow]::AllowSetForegroundWindow([uint32]$script:taskWorker.Id) | Out-Null
        $taskRetry.Enabled=$false
    }catch{$taskTitle.Text='启动未完成';$taskDetail.Text=$_.Exception.Message;$taskRetry.Enabled=$true}
}
$taskTimer=[Windows.Forms.Timer]::new();$taskTimer.Interval=500
$taskTimer.Add_Tick({
    try{
        $taskStatePath=Join-Path $taskPrivate 'launcher-status.json'
        if([IO.File]::Exists($taskStatePath)){
            $taskState=[IO.File]::ReadAllText($taskStatePath) | ConvertFrom-Json
            if($taskState.runId -eq $script:taskRunId){
                $script:taskLastState=$taskState;$taskTitle.Text=$taskState.title;$taskDetail.Text=$taskState.detail
                $taskLabels=@{checking='检查中';starting='启动中';running='已启动';ready='已就绪';login='需要登录'}
                foreach($taskKey in @('client','gateway','relay')){
                    $taskValue=[string]$taskState.steps.$taskKey;$taskRows[$taskKey].value.Text=$taskLabels[$taskValue]
                    $taskColor=if($taskValue -in @('ready','running')){'#82c4a5'}elseif($taskValue -eq 'login'){'#d9bb80'}else{'#98a3b3'}
                    $taskRows[$taskKey].dot.ForeColor=[Drawing.ColorTranslator]::FromHtml($taskColor);$taskRows[$taskKey].value.ForeColor=[Drawing.ColorTranslator]::FromHtml($taskColor)
                }
                $taskTitle.ForeColor=if($taskState.phase -eq 'failed'){[Drawing.ColorTranslator]::FromHtml('#e39d9d')}else{$taskFg}
                if($taskState.phase -eq 'ready'){$taskClose.Text='完成'}
                if($taskState.phase -in @('ready','failed')){$taskRetry.Enabled=$true}
            }
        }
        if($script:taskWorker -and $script:taskWorker.HasExited){
            $taskRetry.Enabled=$true
            if(!$script:taskLastState){$taskTitle.Text='启动器未能完成检查';$taskDetail.Text='可点“重新检查”。如仍失败，请点击“查看日志”。'}
        }
    }catch{}
})
$taskOpen.Add_Click({
    $taskClientPid=$script:taskLastState.health.client.pid
    if($taskClientPid){
        $taskArguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskPrivate+'\Show-Client.ps1" -ProcessId '+$taskClientPid
        Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $taskArguments -WindowStyle Hidden
    }
})
$taskRetry.Add_Click({Start-Check})
$taskLog.Add_Click({$taskPath=Join-Path $taskPrivate 'launcher.log';if([IO.File]::Exists($taskPath)){Start-Process -FilePath 'notepad.exe' -ArgumentList ('"'+$taskPath+'"') -WindowStyle Normal}})
$taskClose.Add_Click({$taskForm.Close()})
$taskForm.Add_Shown({
    $taskUiState=@{pid=$PID;session=[Diagnostics.Process]::GetCurrentProcess().SessionId;shownUtc=[DateTime]::UtcNow.ToString('o');visible=$taskForm.Visible;elevated=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}
    [IO.File]::WriteAllText((Join-Path $taskPrivate 'launcher-ui.json'),($taskUiState | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
    $taskTimer.Start();Start-Check
})
$taskForm.Add_FormClosed({$taskTimer.Stop();if($script:taskWorkerOwned -and $script:taskWorker -and !$script:taskWorker.HasExited){$script:taskWorker.Kill()}})
try{[Windows.Forms.Application]::Run($taskForm)}finally{$taskTimer.Dispose();$taskForm.Dispose();$taskMutex.ReleaseMutex();$taskMutex.Dispose()}
