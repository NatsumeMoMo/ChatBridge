$ErrorActionPreference='Stop'
$taskRoot=Split-Path $PSScriptRoot
$taskFailed=$false
Get-ChildItem -LiteralPath $taskRoot -Recurse -Filter '*.ps1' | Where-Object {$_.FullName -notmatch '\\(node_modules|\.private|\.local)\\'} | ForEach-Object {
    $taskTokens=$null;$taskErrors=$null
    [Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$taskTokens,[ref]$taskErrors) | Out-Null
    if($taskErrors.Count){$taskFailed=$true;Write-Error ($_.FullName+': '+($taskErrors.Message -join ' / ')) -ErrorAction Continue}
}
if($taskFailed){exit 1}
Write-Output 'PowerShell syntax OK'
