# Build in development and replace the one application folder only after publish succeeds.
$ErrorActionPreference = 'Stop'
$foxRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$foxStage = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '.publish-stage'))
$foxRelease = [IO.Path]::GetFullPath((Join-Path $foxRoot 'FoxBridge-WinUI'))
$foxProject = Join-Path $PSScriptRoot 'FoxBridge.WinUI\FoxBridge.WinUI.csproj'
if ((Split-Path -Parent $foxStage) -ne $PSScriptRoot -or (Split-Path -Leaf $foxStage) -ne '.publish-stage' -or
    (Split-Path -Parent $foxRelease) -ne $foxRoot -or (Split-Path -Leaf $foxRelease) -ne 'FoxBridge-WinUI' -or
    -not (Test-Path -LiteralPath $foxProject)) { throw 'Unexpected application directories' }

if (Test-Path -LiteralPath $foxStage) { Remove-Item -LiteralPath $foxStage -Recurse -Force }
dotnet publish $foxProject -c Release -p:Platform=x64 --self-contained false -o $foxStage
if ($LASTEXITCODE -ne 0) { throw 'Release publish failed; current application was not replaced' }
foreach ($foxRequired in @('FoxBridge.WinUI.exe', 'FoxBridge.WinUI.dll', 'FoxBridge.WinUI.deps.json',
    'FoxBridge.WinUI.runtimeconfig.json', 'FoxBridge.WinUI.pri', 'App.xbf', 'MainPage.xbf', 'MainWindow.xbf',
    'Assets\AppIcon.ico', 'Assets\AppIcon.svg', 'Backend\worker.py', 'Backend\bridge.py', 'Backend\cacert.pem')) {
    if (-not (Test-Path -LiteralPath (Join-Path $foxStage $foxRequired) -PathType Leaf)) {
        throw "Incomplete publish: $foxRequired. Current application was not replaced."
    }
}
if (@(Get-ChildItem -LiteralPath $foxStage -Filter *.pdb -File -Recurse).Count -gt 0) {
    throw 'Debug symbols found in Release publish'
}

$foxProcesses = @(Get-Process FoxBridge.WinUI -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.Equals((Join-Path $foxRelease 'FoxBridge.WinUI.exe'), [StringComparison]::OrdinalIgnoreCase)
})
foreach ($foxProcess in $foxProcesses) {
    if (-not $foxProcess.CloseMainWindow()) { throw 'Cannot close FoxBridge gracefully' }
    if (-not $foxProcess.WaitForExit(35000)) { throw 'FoxBridge is still shutting down; application was not replaced' }
}
New-Item -ItemType Directory -Path $foxRelease -Force | Out-Null
# Keep personal settings in place. Everything else comes from the clean publish.
Get-ChildItem -LiteralPath $foxRelease -Force | Where-Object { $_.Name -ne 'foxbridge-ui-settings.json' } |
    ForEach-Object {
        $foxItemPath = [IO.Path]::GetFullPath($_.FullName)
        if ((Split-Path -Parent $foxItemPath) -ne $foxRelease) { throw 'Unexpected application file path' }
        Remove-Item -LiteralPath $foxItemPath -Recurse -Force
    }
Get-ChildItem -LiteralPath $foxStage -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $foxRelease -Recurse -Force
}
$foxShell = New-Object -ComObject WScript.Shell
$foxShortcut = $foxShell.CreateShortcut((Join-Path $foxRoot 'FoxBridge.lnk'))
$foxShortcut.TargetPath = Join-Path $foxRelease 'FoxBridge.WinUI.exe'
$foxShortcut.WorkingDirectory = $foxRelease
$foxShortcut.IconLocation = (Join-Path $foxRelease 'Assets\AppIcon.ico') + ',0'
$foxShortcut.Description = 'FoxBridge'
$foxShortcut.Save()
Remove-Item -LiteralPath $foxStage -Recurse -Force
$foxBytes = (Get-ChildItem -LiteralPath $foxRelease -File -Recurse | Measure-Object Length -Sum).Sum
Write-Output ('Release updated: {0:N2} MiB' -f ($foxBytes / 1MB))
