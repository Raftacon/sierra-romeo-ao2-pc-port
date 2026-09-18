param(
    [ValidateSet('Debug', 'Release', 'RelWithDebInfo')]
    [string]$Configuration = 'RelWithDebInfo',
    [switch]$Controller,
    [ValidateSet('sdl', 'xinput')][string]$InputBackend = 'xinput',
    [ValidateRange(1, 3)][int]$Scale = 1,
    [ValidateSet('fsr', 'bilinear')][string]$Upscaler = 'fsr',
    [ValidateSet('off', 'fxaa', 'strong')][string]$AntiAliasing = 'fxaa',
    [ValidateSet(30, 60)][int]$Fps = 60,
    [ValidateSet('full', 'fast')][string]$Readback = 'fast',
    [switch]$Fullscreen,
    [switch]$NoVSync,
    [switch]$NoVblankWake,
    [switch]$Sharper,
    [switch]$ProjectionPrecision,
    [switch]$NoProjectionPrecision,
    [switch]$NoMsaaAlignment
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$setupPython = Join-Path $projectRoot '.tools/setup-python/Scripts/python.exe'
if (Test-Path -LiteralPath $setupPython) {
    $env:PATH = (Split-Path $setupPython -Parent) + ';' + $env:PATH
}
$preferencesFile = Join-Path $projectRoot 'launch-preferences.json'
if (Test-Path -LiteralPath $preferencesFile) {
    $launchPreferences = Get-Content -LiteralPath $preferencesFile -Raw | ConvertFrom-Json
    if (-not $PSBoundParameters.ContainsKey('Controller')) { $Controller = [bool]$launchPreferences.controller }
    if (-not $PSBoundParameters.ContainsKey('Fullscreen') -and -not (Test-Path 'userdata/player/pc-settings.toml')) { $Fullscreen = [bool]$launchPreferences.fullscreen }
}
& python tools/identify.py assets/default.xex --expect config/retail-usa.identity.json
if ($LASTEXITCODE -ne 0) { throw 'Executable revision mismatch.' }
$exe = Join-Path $projectRoot "out/build/$Configuration/army_of_two.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw 'Build the project with tools/build.ps1 first.' }
$pluginSuffix = switch ($Configuration) { 'Debug' { 'd' } 'RelWithDebInfo' { 'rd' } default { '' } }
$spatialAvailable = Test-Path -LiteralPath (Join-Path (Split-Path $exe) "rexgpu-spatial$pluginSuffix.dll")
$gpuPlugin = if ($spatialAvailable) { 'spatial' } else { 'xenos' }
if ($ProjectionPrecision -and -not $spatialAvailable) { throw 'Projection precision requires the spatial GPU build.' }
if ($ProjectionPrecision -and $NoProjectionPrecision) { throw 'Choose either ProjectionPrecision or NoProjectionPrecision.' }
& python tools/local_profile.py --destination userdata/player --source artifacts/player
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the local save profile.' }
$diagnosticRoot = Join-Path $projectRoot 'userdata/diagnostics'
New-Item -ItemType Directory -Path $diagnosticRoot -Force | Out-Null
$runArgs = @(
    "--game_data_root=$projectRoot/assets",
    "--user_data_root=$projectRoot/userdata/player",
    "--cache_root=$projectRoot/artifacts/native-cache",
    "--gpu_plugin=$gpuPlugin", '--mnk_mode=false', "--input_backend=$InputBackend",
    "--log_file=$diagnosticRoot/runtime.log",
    '--log_level=info', '--log_flush_interval=1', '--log_max_files=2',
    "--aot_keyboard_mouse=$((!$Controller.IsPresent).ToString().ToLowerInvariant())",
    "--readback_resolve=$Readback",
    '--anisotropic_override=5',
    '--readback_resolve_half_pixel_offset=true',
    "--aot_disable_blur=$($Sharper.IsPresent.ToString().ToLowerInvariant())"
)
if ($spatialAvailable) {
    if ($NoVblankWake) { $runArgs += '--aot_gpu_vblank_wake=false' }
    $projectionEnabled = -not $NoProjectionPrecision.IsPresent
    if ($PSBoundParameters.ContainsKey('ProjectionPrecision')) { $projectionEnabled = $ProjectionPrecision.IsPresent }
    $runArgs += "--aot_projection_precision=$($projectionEnabled.ToString().ToLowerInvariant())"
    $runArgs += "--aot_msaa_alignment=$((!$NoMsaaAlignment.IsPresent).ToString().ToLowerInvariant())"
}
$hasPreferences = Test-Path -LiteralPath (Join-Path $projectRoot 'userdata/player/pc-settings.toml')
if ($PSBoundParameters.ContainsKey('AntiAliasing') -or -not $hasPreferences -or
    -not (Select-String -LiteralPath (Join-Path $projectRoot 'userdata/player/pc-settings.toml') -Pattern '^\s*swap_post_effect\s*=' -Quiet)) {
    $postEffect = switch ($AntiAliasing) { 'off' { 'none' } 'strong' { 'fxaa_extreme' } default { 'fxaa' } }
    $runArgs += "--swap_post_effect=$postEffect"
}
if (-not $hasPreferences) { $runArgs += '--window_width=1920', '--window_height=1080' }
if (-not $hasPreferences -or $PSBoundParameters.ContainsKey('Fullscreen')) {
    $runArgs += "--fullscreen=$($Fullscreen.IsPresent.ToString().ToLowerInvariant())"
}
if (-not $hasPreferences -or $PSBoundParameters.ContainsKey('Scale')) { $runArgs += "--resolution_scale=$Scale" }
if ($spatialAvailable -and ((-not $hasPreferences) -or $PSBoundParameters.ContainsKey('Upscaler'))) {
    $runArgs += "--aot_spatial_upscale=$(($Upscaler -eq 'fsr').ToString().ToLowerInvariant())"
}
if (-not $hasPreferences -or $PSBoundParameters.ContainsKey('Fps')) { $runArgs += "--aot_fps=$Fps" }
if (-not $hasPreferences -or $PSBoundParameters.ContainsKey('NoVSync')) {
    $runArgs += "--vsync=$((!$NoVSync.IsPresent).ToString().ToLowerInvariant())"
}
# Interactive play always uses real input, even in a shell previously used
# for automation. Keep the diagnostic environment local to this child launch.
$testNames = @('AOT_INPUT_SCRIPT', 'AOT_INPUT_STATE', 'AOT_FRAME_LOG', 'AOT_DUMP_IMAGE', 'AOT_OPEN_CONSOLE', 'AOT_TRACE_COMMANDS', 'AOT_GAME_COMMANDS', 'AOT_TEST_KBM', 'AOT_TRACE_MOUSE', 'AOT_TRACE_FONT', 'AOT_TRACE_TEXT', 'AOT_TRACE_READBACK_RETIREMENT', 'AOT_TIMER_RESOLUTION_MS', 'AOT_TRACE_HUD', 'AOT_PROFILE', 'AOT_FRAME_PHASE_LOG', 'AOT_WAIT_LOG', 'AOT_RENDER_WAIT_LOG', 'AOT_GPU_WAIT_LOG', 'AOT_GPU_COPY_LOG', 'AOT_GPU_VBLANK_LOG', 'AOT_GPU_VBLANK_WATCH', 'AOT_RHI_RESOLVE_LOG', 'AOT_RHI_RESOLVE_TRIGGER')
$testNames += 'AOT_FRAME_COLOR_PROBE'
$testNames += 'AOT_PERF_NO_PC_OVERLAYS'
$testNames += 'AOT_NETWORK_LOG', 'AOT_PC_COOP_DIAGNOSTIC', 'AOT_PC_COOP_UI_DIAGNOSTIC'
$testNames += 'AOT_PC_COOP_SERVICE', 'AOT_PC_COOP_BIND', 'AOT_PC_COOP_JOIN', 'AOT_PC_COOP_PORT', 'AOT_PC_COOP_NAME', 'AOT_PC_COOP_INVITE_FILE'
$testNames += 'AOT_PC_COOP_LOAD_PROBE'
$previousTestValues = @{}
foreach ($name in $testNames) {
    $previousTestValues[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
try {
& $exe @runArgs
$gameExit = $LASTEXITCODE
} finally {
    foreach ($name in $testNames) {
        [Environment]::SetEnvironmentVariable($name, $previousTestValues[$name], 'Process')
    }
}
exit $gameExit
