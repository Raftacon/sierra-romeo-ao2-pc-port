param([Parameter(Mandatory=$true)][string]$SourceZip,
      [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$zip=(Resolve-Path -LiteralPath $SourceZip).Path
$target=[IO.Path]::GetFullPath($Output)
if(Test-Path -LiteralPath $target) { throw 'Output already exists. Choose a new release name.' }
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if(-not (Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework C# compiler is required to package the GUI.' }
New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
$header=(Resolve-Path (Join-Path $root 'resources/branding/sierra_romeo.png')).Path
$version=(Resolve-Path (Join-Path $root 'release/version.json')).Path
$source=(Resolve-Path (Join-Path $root 'release/gui/Installer.cs')).Path
$manifest=(Resolve-Path (Join-Path $root 'release/gui/app.manifest')).Path
$icon=(Resolve-Path (Join-Path $root 'resources/branding/sr_installer_icon.ico')).Path
$releaseVersion=(Get-Content -LiteralPath $version -Raw | ConvertFrom-Json).version
$assemblyVersion=[regex]::Match([IO.File]::ReadAllText($source), 'AssemblyInformationalVersion\("([^\"]+)"\)').Groups[1].Value
if($releaseVersion -ne $assemblyVersion) { throw 'Installer assembly version does not match release/version.json.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive=[IO.Compression.ZipFile]::OpenRead($zip)
try {
    foreach($name in @('release/gui/Installer.cs','release/gui/app.manifest','release/version.json')) {
        $entry=$archive.GetEntry($name)
        if(-not $entry) { throw "Source archive is missing $name." }
        $reader=New-Object IO.StreamReader($entry.Open())
        try { $embedded=$reader.ReadToEnd().Replace("`r`n","`n") } finally { $reader.Dispose() }
        $current=[IO.File]::ReadAllText((Join-Path $root $name)).Replace("`r`n","`n")
        if($embedded -cne $current) { throw "Source archive is stale: $name differs. Re-export source before building the installer." }
    }
} finally { $archive.Dispose() }
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$target" "/win32manifest:$manifest" "/win32icon:$icon" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.IO.Compression.dll /reference:System.Web.Extensions.dll "/resource:$zip,source.zip" "/resource:$header,header.png" "/resource:$version,version.json" $source
if($LASTEXITCODE -ne 0) { throw 'GUI installer compilation failed.' }
Get-FileHash -LiteralPath $target -Algorithm SHA256
