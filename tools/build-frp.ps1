param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$taskOutput=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $taskOutput){throw 'Choose a new output directory'}
New-Item -ItemType Directory -Path $taskOutput | Out-Null
& git clone --depth 1 --branch v0.68.1 https://github.com/fatedier/frp.git (Join-Path $taskOutput 'source')
if($LASTEXITCODE -ne 0){throw 'FRP source checkout failed'}
Push-Location (Join-Path $taskOutput 'source')
try{
    & go build -trimpath -o (Join-Path $taskOutput 'frpc.exe') ./cmd/frpc
    if($LASTEXITCODE -ne 0){throw 'frpc build failed'}
    & go build -trimpath -o (Join-Path $taskOutput 'frps.exe') ./cmd/frps
    if($LASTEXITCODE -ne 0){throw 'frps build failed'}
    $taskCommit=& git rev-parse HEAD
    @{version='0.68.1';commit=$taskCommit;frpcSha256=(Get-FileHash (Join-Path $taskOutput 'frpc.exe')).Hash.ToLower();frpsSha256=(Get-FileHash (Join-Path $taskOutput 'frps.exe')).Hash.ToLower()} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskOutput 'build-manifest.json') -Encoding UTF8
}finally{Pop-Location}
