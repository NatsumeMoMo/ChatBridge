param([Parameter(Mandatory=$true)][int]$ProcessId,[switch]$RequestLogin,[switch]$InspectOnly,[string]$ResultFile='')
$ErrorActionPreference='Stop'
$taskResult=@{shown=$false;loginRequested=$false;uiState='unknown';clientPid=$ProcessId}
try{
    if(!('ChatBridgeStartWindows' -as [type])){
        Add-Type -TypeDefinition @'
using System;using System.Text;using System.Runtime.InteropServices;
public static class ChatBridgeStartWindows {
 public delegate bool EnumProc(IntPtr h,IntPtr p);
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int Left,Top,Right,Bottom;}
 [StructLayout(LayoutKind.Sequential)] public struct POINT {public int X,Y;}
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb,IntPtr p);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h,IntPtr dc,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPhysicalPoint(POINT p);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h,uint f);
 [DllImport("user32.dll")] public static extern bool SetPhysicalCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
 [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint f,bool i,uint a);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr h,int n,StringBuilder s,int l,out uint needed);
 public static bool InteractiveDesktop(){IntPtr h=OpenInputDesktop(0,false,1);if(h==IntPtr.Zero)return false;try{var s=new StringBuilder(128);uint n;return GetUserObjectInformation(h,2,s,256,out n)&&s.ToString().Equals("Default",StringComparison.OrdinalIgnoreCase);}finally{CloseDesktop(h);}}
}
'@
    }
    $taskResult.interactiveDesktop=[ChatBridgeStartWindows]::InteractiveDesktop()
    if(!$taskResult.interactiveDesktop){throw '当前桌面已锁定，未操作微信窗口。'}
    Add-Type -AssemblyName UIAutomationClient
    $script:taskLoginWindow=[IntPtr]::Zero;$script:taskMainWindow=[IntPtr]::Zero;$script:taskOtherWindow=[IntPtr]::Zero;$script:taskCandidateWindows=[Collections.Generic.List[IntPtr]]::new()
    [ChatBridgeStartWindows]::EnumWindows({param($h,$p)
        $taskWindowPid=[uint32]0;[ChatBridgeStartWindows]::GetWindowThreadProcessId($h,[ref]$taskWindowPid) | Out-Null
        if($taskWindowPid -eq $ProcessId){
            $taskClass=[Text.StringBuilder]::new(180);[ChatBridgeStartWindows]::GetClassName($h,$taskClass,180) | Out-Null
            if($taskClass.ToString() -eq 'mmui::LoginWindow'){$script:taskLoginWindow=$h}
            $taskRect=[ChatBridgeStartWindows+RECT]::new()
            if([ChatBridgeStartWindows]::GetWindowRect($h,[ref]$taskRect) -and $taskRect.Right-$taskRect.Left -gt 250 -and $taskRect.Bottom-$taskRect.Top -gt 250){$script:taskOtherWindow=$h;$script:taskCandidateWindows.Add($h)}
        }
        return $true
    },[IntPtr]::Zero) | Out-Null
    foreach($taskCandidate in $script:taskCandidateWindows){
        # Qt 的原生窗口类与 UIA 语义类不同，应按登录页面的语义类识别。
        $taskCandidateElement=[System.Windows.Automation.AutomationElement]::FromHandle($taskCandidate)
        if($taskCandidateElement.Current.ClassName -eq 'mmui::LoginWindow'){$script:taskLoginWindow=$taskCandidate}
        if($taskCandidateElement.Current.ClassName -eq 'mmui::MainWindow'){$script:taskMainWindow=$taskCandidate}
    }
    $taskWindow=if($script:taskMainWindow -ne [IntPtr]::Zero){$taskResult.uiState='main';$script:taskMainWindow}elseif($script:taskLoginWindow -ne [IntPtr]::Zero){$taskResult.uiState='login';$script:taskLoginWindow}else{$script:taskOtherWindow}
    if($taskWindow -eq [IntPtr]::Zero){throw '微信窗口尚未创建'}
    if(!$InspectOnly){[ChatBridgeStartWindows]::ShowWindowAsync($taskWindow,9) | Out-Null;[ChatBridgeStartWindows]::SetForegroundWindow($taskWindow) | Out-Null;$taskResult.shown=$true}
    if(!$InspectOnly -and $RequestLogin -and $taskResult.uiState -eq 'login'){
        Add-Type -AssemblyName UIAutomationClient
        $taskElement=[System.Windows.Automation.AutomationElement]::FromHandle($taskWindow)
        $taskLoginNames=[System.Windows.Automation.OrCondition]::new([System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'登录'),[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'进入微信'))
        $taskCondition=[System.Windows.Automation.AndCondition]::new($taskLoginNames,[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty,[System.Windows.Automation.ControlType]::Button))
        $taskButton=$taskElement.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$taskCondition)
        $taskBounds=$null
        if($taskButton -and $taskButton.Current.IsEnabled){
            $taskBounds=$taskButton.Current.BoundingRectangle;$taskResult.buttonSource='uia'
        }else{
            # 固定客户端的进入按钮不暴露 UIA；只在可正常截取的登录小窗确认绿色按钮区域。
            Add-Type -AssemblyName System.Drawing
            $taskRect=[ChatBridgeStartWindows+RECT]::new();[ChatBridgeStartWindows]::GetWindowRect($taskWindow,[ref]$taskRect) | Out-Null
            $taskWidth=$taskRect.Right-$taskRect.Left;$taskHeight=$taskRect.Bottom-$taskRect.Top
            $taskCore=@((Get-Process -Id $ProcessId).Modules | Where-Object ModuleName -eq 'Weixin.dll')
            $taskFixedClient=$taskCore.Count -eq 1 -and (Get-FileHash -LiteralPath $taskCore[0].FileName -Algorithm SHA256).Hash -eq 'f487e4f56930456a25fb0d7b058c77e2dcd364e268d49bdfff4dc0e3be26ba36'
            if($taskFixedClient -and $taskWidth -ge 250 -and $taskWidth -le 400 -and $taskHeight -ge 340 -and $taskHeight -le 520){
                $taskBitmap=[Drawing.Bitmap]::new($taskWidth,$taskHeight);$taskGraphics=[Drawing.Graphics]::FromImage($taskBitmap)
                try{
                    $taskHdc=$taskGraphics.GetHdc();try{$taskPrinted=[ChatBridgeStartWindows]::PrintWindow($taskWindow,$taskHdc,2)}finally{$taskGraphics.ReleaseHdc($taskHdc)}
                    if($taskPrinted){
                        $taskY=[int]($taskHeight*.74);$taskGreen=$true
                        foreach($taskX in @([int]($taskWidth*.25),[int]($taskWidth*.75))){$taskPixel=$taskBitmap.GetPixel($taskX,$taskY);if($taskPixel.R -gt 70 -or $taskPixel.G -lt 130 -or $taskPixel.B -gt 180){$taskGreen=$false}}
                        if($taskGreen){$taskBounds=@{X=$taskRect.Left+$taskWidth*.2;Y=$taskRect.Top+$taskY-10;Width=$taskWidth*.6;Height=20};$taskResult.buttonSource='login-window-pixels'}
                    }
                }finally{$taskGraphics.Dispose();$taskBitmap.Dispose()}
            }
        }
        if($taskBounds){
            $taskRect=[ChatBridgeStartWindows+RECT]::new();[ChatBridgeStartWindows]::GetWindowRect($taskWindow,[ref]$taskRect) | Out-Null
            $taskPoint=[ChatBridgeStartWindows+POINT]::new();$taskPoint.X=[int]($taskBounds.X+$taskBounds.Width/2);$taskPoint.Y=[int]($taskBounds.Y+$taskBounds.Height/2)
            # 只点击已识别登录小窗内的登录按钮，聊天窗口、遮挡或锁屏时不发送鼠标输入。
            $taskHit=[ChatBridgeStartWindows]::GetAncestor([ChatBridgeStartWindows]::WindowFromPhysicalPoint($taskPoint),2)
            if($taskRect.Right-$taskRect.Left -lt 600 -and $taskRect.Bottom-$taskRect.Top -lt 700 -and $taskBounds.Width -gt 30 -and $taskBounds.Height -gt 15 -and $taskPoint.X -gt $taskRect.Left -and $taskPoint.X -lt $taskRect.Right -and $taskPoint.Y -gt $taskRect.Top -and $taskPoint.Y -lt $taskRect.Bottom -and $taskHit -eq $taskWindow){
                [ChatBridgeStartWindows]::SetPhysicalCursorPos($taskPoint.X,$taskPoint.Y) | Out-Null
                [ChatBridgeStartWindows]::mouse_event(2,0,0,0,[UIntPtr]::Zero);[ChatBridgeStartWindows]::mouse_event(4,0,0,0,[UIntPtr]::Zero);$taskResult.loginRequested=$true
            }
        }
    }
}catch{$taskResult.detail=$_.Exception.Message}
if($ResultFile){[IO.File]::WriteAllText($ResultFile,($taskResult | ConvertTo-Json),[Text.UTF8Encoding]::new($false))}
