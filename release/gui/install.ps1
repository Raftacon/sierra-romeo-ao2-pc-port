param([Parameter(Mandatory=$true)][string]$OptionsFile, [switch]$CheckOnly, [switch]$VerifyDownloads)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Set-Location -LiteralPath $root
$options = Get-Content -LiteralPath $OptionsFile -Raw | ConvertFrom-Json
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'prerequisites.json') -Raw | ConvertFrom-Json
$cache = Join-Path $root '.tools/installer-downloads'
$scratch = Join-Path $root 'artifacts/setup-temp'
New-Item -ItemType Directory -Path $scratch -Force | Out-Null
$env:TEMP=$scratch; $env:TMP=$scratch
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Refresh-Path {
    $paths = (@("$root\.tools\ninja", "$env:ProgramFiles\7-Zip", "$env:ProgramFiles\CMake\bin", "$env:LOCALAPPDATA\Programs\Git\cmd", "$env:ProgramFiles\Git\cmd", [Environment]::GetEnvironmentVariable('Path','Machine'), [Environment]::GetEnvironmentVariable('Path','User'), $env:PATH) -join ';') -split ';'
    $env:PATH = (($paths | ForEach-Object {$_.Trim()} | Where-Object {$_}) | Select-Object -Unique) -join ';'
}
function Check-Cancel {
    if(Test-Path -LiteralPath (Join-Path $root 'artifacts/setup.cancel')) { throw 'Setup cancelled at a safe step boundary. The partial installation was retained.' }
}
function Validate-Game {
    $interpreter=Find-Python
    Write-Output 'AOT_STAGE|Verifying your game image'
    & $interpreter -u tools/setup.py --iso $options.iso --check-only
    if($LASTEXITCODE -ne 0) { throw 'The image is not the supported Army of Two USA retail revision. No game build was started.' }
}
function Find-Python {
    $found=Get-Command python.exe -ErrorAction SilentlyContinue
    $candidates=@()
    if($found -and $found.Source -notlike '*\WindowsApps\*') { $candidates+=$found.Source }
    $candidates+=@(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe", "$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName)
    foreach($candidate in ($candidates | Select-Object -Unique)) {
        & $candidate -c "import sys,struct,tkinter,venv; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) and struct.calcsize('P') == 8 else 1)" 2>$null
        if($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}
function Find-VS {
    $vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if(Test-Path -LiteralPath $vswhere) {
        $install = (& $vswhere -all -latest -products '*' -version '[17.0,18.0)' -property installationPath | Select-Object -First 1)
        if ($install -and (Test-Path "$install/Common7/Tools/Launch-VsDevShell.ps1") -and @(Get-ChildItem "$install/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe" -ErrorAction SilentlyContinue).Count) { return $install }
    }
    return $null
}
function Present([string]$name) {
    if($name -eq 'vcredist') {
        $runtime=Get-ItemProperty 'HKLM:/SOFTWARE/Microsoft/VisualStudio/14.0/VC/Runtimes/x64' -ErrorAction SilentlyContinue
        return [bool]($runtime -and $runtime.Installed -eq 1 -and [version]($runtime.Version.TrimStart('v')) -ge [version]'14.40.0.0')
    }
    if($name -eq 'python') { return [bool](Find-Python) }
    if($name -eq 'vs') {
        $kits=(Get-ItemProperty 'HKLM:/SOFTWARE/Microsoft/Windows Kits/Installed Roots' -ErrorAction SilentlyContinue).KitsRoot10
        $sdk=@(Get-ChildItem "$kits\bin\*\x64\rc.exe" -ErrorAction SilentlyContinue)
        return [bool]((Find-VS) -and $sdk.Count)
    }
    $command=Get-Command $name -ErrorAction SilentlyContinue
    if(-not $command) { return $false }
    if($name -eq 'cmake') {
        $version=(& $command.Source --version | Select-Object -First 1)
        return ($version -match 'cmake version (\d+\.\d+\.\d+)' -and [version]$Matches[1] -ge [version]'3.25.0')
    }
    return $true
}
function Download-Verified($entry) {
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $path=Join-Path $cache ([IO.Path]::GetFileName(([uri]$entry.url).AbsolutePath))
    if(-not (Test-Path -LiteralPath $path)) {
        Write-Host "AOT_STAGE|Downloading $($entry.name) $($entry.version)"
        $client=New-Object Net.WebClient
        try { $client.DownloadFile($entry.url, "$path.partial") } finally { $client.Dispose() }
        if((Get-FileHash -LiteralPath "$path.partial" -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Download hash mismatch: $($entry.name). Nothing was executed." }
        Move-Item -LiteralPath "$path.partial" -Destination $path
    }
    if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Cached download hash mismatch: $($entry.name)." }
    return $path
}
try {
    if(-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This release supports Windows x64 only.' }
    if(-not (Test-Path -LiteralPath $options.iso -PathType Leaf)) { throw 'The selected game image is no longer available.' }
    Refresh-Path
    if($VerifyDownloads) {
        foreach($entry in $lock.windows_x64) {
            $verified=Download-Verified $entry
            Write-Output "Verified $($entry.name) $($entry.version): $verified"
        }
        exit 0
    }
    Write-Output 'AOT_STAGE|Checking build prerequisites'
    $missing=@($lock.windows_x64 | Where-Object { -not (Present $_.name) })
    if($CheckOnly) {
        $missing | ForEach-Object { Write-Output "Missing: $($_.name) $($_.version)" }
        if($missing.Count) { exit 2 } else { Write-Output 'Prerequisites available.'; exit 0 }
    }
    if($missing.Count -and -not $options.installDependencies) { throw 'Required tools are missing. Enable automatic dependency installation in setup.' }
    if(Find-Python) { Validate-Game }
    foreach($entry in $missing) {
        Check-Cancel
        $file=Download-Verified $entry
        Write-Output "AOT_STAGE|Installing $($entry.name) $($entry.version)"
        if($entry.name -eq 'ninja') {
            Expand-Archive -LiteralPath $file -DestinationPath (Join-Path $root '.tools/ninja') -Force
        } else {
            $program=$file; $elevate=$false
            switch($entry.name) {
                'python' { $arguments='/quiet InstallAllUsers=0 PrependPath=0 Include_test=0 Include_launcher=0 Include_tcltk=1' }
                'git' { $arguments='/SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER' }
                'cmake' { $program='msiexec.exe'; $arguments='/i "'+$file+'" /qn /norestart ADD_CMAKE_TO_PATH=System'; $elevate=$true }
                '7z' { $arguments='/S'; $elevate=$true }
                'vcredist' { $arguments='/install /quiet /norestart'; $elevate=$true }
                'vs' { $arguments='--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'; $elevate=$true }
                default { throw 'Unknown prerequisite installer.' }
            }
            if($elevate) { $p=Start-Process -FilePath $program -ArgumentList $arguments -Verb RunAs -Wait -PassThru }
            else { $p=Start-Process -FilePath $program -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru }
            if($p.ExitCode -eq 3010 -or $p.ExitCode -eq 1641) { throw 'A dependency requires a restart. Restart Windows, then retry setup.' }
            if($p.ExitCode -ne 0) { throw "Dependency installation failed: $($entry.name), exit $($p.ExitCode)." }
        }
        Refresh-Path
        if(-not (Present $entry.name)) { throw "The $($entry.name) prerequisite is still missing. For existing Visual Studio installations, add Desktop development with C++ and Windows SDK in Visual Studio Installer." }
        if($entry.name -eq 'python') { Validate-Game }
    }
    $python=Find-Python
    Check-Cancel
    $env:PATH=(Split-Path $python -Parent)+';'+$env:PATH
    Write-Output "Using Python: $python"
    Write-Output "Using Visual Studio: $(Find-VS)"
    & $python -u tools/setup.py --iso $options.iso
    if($LASTEXITCODE -ne 0) { throw "Game build failed (exit $LASTEXITCODE)." }
    Check-Cancel
    Write-Output 'AOT_STAGE|Creating shortcuts and launch preferences'
    @{controller=$false; fullscreen=[bool]$options.fullscreen} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'launch-preferences.json') -Encoding UTF8
    $shell=New-Object -ComObject WScript.Shell
    $shortcutFolders=@()
    if($options.desktop) { $shortcutFolders += [Environment]::GetFolderPath('DesktopDirectory') }
    if($options.startMenu) { $shortcutFolders += [Environment]::GetFolderPath('Programs') }
    foreach($folder in $shortcutFolders) {
      try {
        $shortcutPath=Join-Path $folder 'Sierra Romeo.lnk'
        if(Test-Path -LiteralPath $shortcutPath) { Write-Output "Existing shortcut retained: $shortcutPath"; continue }
        $link=$shell.CreateShortcut($shortcutPath)
        $link.TargetPath="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $link.Arguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+(Join-Path $root 'tools/run.ps1')+'"'
        $link.WorkingDirectory=$root
        $link.IconLocation=(Join-Path $root 'out/build/RelWithDebInfo/army_of_two.exe')+',0'
        $link.Save()
      } catch {
        Write-Output "AOT_WARNING|Could not create a shortcut in $folder. The game is installed; launch it from its folder. $($_.Exception.Message)"
      }
    }
    @{version=(Get-Content release/version.json -Raw | ConvertFrom-Json).version; installed=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content artifacts/installation-complete.json -Encoding UTF8
    Write-Output 'AOT_STAGE|Installation complete'
    exit 0
} catch { Write-Output "ERROR: $($_.Exception.Message)"; exit 1 }
