$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$baselineDir = Join-Path $projectRoot '.tools/xenia-scripted'
New-Item -ItemType Directory -Path $baselineDir -Force | Out-Null
Copy-Item -LiteralPath '.tools/xenia/xenia_canary.exe','.tools/xenia/LICENSE' -Destination $baselineDir
Copy-Item -LiteralPath 'out/build/RelWithDebInfo/test-controller/xinput1_4.dll' -Destination $baselineDir
Write-Output "Isolated scripted Xenia baseline prepared at $baselineDir"
