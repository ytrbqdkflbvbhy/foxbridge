param([string]$Version = '0.8.1')
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Use a semantic version such as 0.8.0' }
$foxRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$foxApplication = Join-Path $foxRoot 'FoxBridge-WinUI'
$foxOutput = Join-Path $PSScriptRoot 'release-artifacts'
$foxExe = Join-Path $foxApplication 'FoxBridge.WinUI.exe'
if (-not (Test-Path -LiteralPath $foxExe)) { throw 'Run Publish-Release.ps1 first' }
$foxFileVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($foxExe).FileVersion
if ($foxFileVersion -ne ($Version + '.0')) { throw "Expected app version $Version.0, found $foxFileVersion" }
New-Item -ItemType Directory -Path $foxOutput -Force | Out-Null
$foxZip = Join-Path $foxOutput "FoxBridge-v$Version-win-x64.zip"
Add-Type -AssemblyName System.IO.Compression
$foxStream = [IO.File]::Open($foxZip, [IO.FileMode]::Create)
$foxArchive = [IO.Compression.ZipArchive]::new($foxStream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($foxFile in Get-ChildItem -LiteralPath $foxApplication -File -Recurse) {
        $foxRelative = [IO.Path]::GetRelativePath($foxApplication, $foxFile.FullName).Replace('\', '/')
        if ($foxRelative -match '(^|/)(__pycache__|\.vs)(/|$)' -or
            $foxFile.Name -like 'foxbridge-ui-settings.json*' -or
            $foxFile.Extension -in @('.pdb', '.pyc', '.pyo', '.log', '.tmp', '.lnk')) { continue }
        [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($foxArchive, $foxFile.FullName, $foxRelative,
            [IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally { $foxArchive.Dispose(); $foxStream.Dispose() }
$foxCheck = [IO.Compression.ZipFile]::OpenRead($foxZip)
try {
    $foxNames = @($foxCheck.Entries.FullName)
    foreach ($foxRequired in @('FoxBridge.WinUI.exe', 'FoxBridge.WinUI.pri', 'MainPage.xbf',
        'Backend/worker.py', 'Backend/cacert.pem', 'Assets/AppIcon.ico', 'README.txt', 'Help/README.md', 'LICENSE',
        'THIRD-PARTY-NOTICES.md', 'licenses/MPL-2.0.txt')) {
        if ($foxNames -notcontains $foxRequired) { throw "Incomplete release archive: $foxRequired" }
    }
    if ($foxNames -match 'foxbridge-ui-settings|__pycache__|\.pdb$|\.pyc$') { throw 'Private settings or build artifacts in release archive' }
} finally { $foxCheck.Dispose() }
$foxHash = (Get-FileHash -LiteralPath $foxZip -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($foxZip + '.sha256', $foxHash + '  ' + [IO.Path]::GetFileName($foxZip) + "`n")
Write-Output ('Packaged: {0} ({1:N2} MiB)' -f [IO.Path]::GetFileName($foxZip), ((Get-Item -LiteralPath $foxZip).Length / 1MB))
