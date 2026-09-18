param(
    [ValidateSet('Debug', 'Release', 'RelWithDebInfo')]
    [string]$Configuration = 'RelWithDebInfo',
    [int]$Jobs = 4,
    [string]$Target,
    [switch]$SpatialUpscaling,
    [switch]$SkipCodegen,
    [switch]$ConfigureOnly
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
& python tools/identify.py assets/default.xex --expect config/retail-usa.identity.json
if ($LASTEXITCODE -ne 0) { throw 'The game executable does not match this port configuration.' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsInstall = & $vswhere -all -latest -products '*' -version '[17.0,18.0)' -property installationPath
if (-not $vsInstall) { throw 'Visual Studio C++ tools were not found.' }
$devShell = Join-Path $vsInstall 'Common7\Tools\Launch-VsDevShell.ps1'
# VsDevCmd uses cmd.exe internally; an inherited developer PATH can exceed its
# command-line limit. Keep the required tool locations in this child shell only.
$buildToolPaths = @('python','cmake','ninja','git','7z') | ForEach-Object {
    $command = Get-Command $_ -ErrorAction SilentlyContinue
    if ($command) { Split-Path $command.Source -Parent }
}
$buildToolPaths += @("$env:SystemRoot\System32", "$env:SystemRoot", "$env:SystemRoot\System32\WindowsPowerShell\v1.0", "$env:SystemRoot\System32\Wbem")
$env:PATH = ($buildToolPaths | Select-Object -Unique) -join ';'
& $devShell -Arch amd64 -HostArch amd64 -SkipAutomaticLocation
$llvm = Join-Path $projectRoot '.tools\llvm\bin'
$sdk = Join-Path $projectRoot '.tools\rexglue\win-amd64'
if (-not (Test-Path -LiteralPath "$llvm\clang++.exe")) { throw 'Local LLVM 20.1.8 is missing.' }
$env:PATH = "$llvm;$sdk\bin;$env:PATH"
if ($SkipCodegen) {
    if (-not (Test-Path -LiteralPath 'generated/default/sources.cmake')) { throw 'SkipCodegen requires a previously generated local build.' }
} else {
    & "$sdk\bin\rexglue.exe" codegen army_of_two_manifest.toml
    if ($LASTEXITCODE -ne 0) { throw "Code generation failed: $LASTEXITCODE" }
}
$buildDir = "out/build/$Configuration"
$configureArgs = @('-S', '.', '-B', $buildDir, '-G', 'Ninja', "-DCMAKE_BUILD_TYPE=$Configuration", "-DCMAKE_CXX_COMPILER=$llvm/clang++.exe", "-DCMAKE_PREFIX_PATH=$sdk", '-DREXSDK_VERSION=0.10.0')
if ($SpatialUpscaling) { $configureArgs += '-DAOT_BUILD_SPATIAL_UPSCALER=ON' }
& cmake @configureArgs
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed: $LASTEXITCODE" }
if (-not $ConfigureOnly) {
    $buildArgs = @('--build', $buildDir, '--parallel', $Jobs)
    if ($Target) { $buildArgs += @('--target', $Target) }
    & cmake @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $LASTEXITCODE" }
}
