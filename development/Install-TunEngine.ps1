$ErrorActionPreference = 'Stop'
$foxTools = Join-Path $PSScriptRoot 'FoxBridge.WinUI\Tools'
$foxEngine = Join-Path $foxTools 'sing-box.exe'
if ((Test-Path -LiteralPath $foxEngine) -and
    (Get-FileHash -LiteralPath $foxEngine -Algorithm SHA256).Hash.ToLowerInvariant() -eq '8f30682ee198855440d2a7fe070d5d3653793b2d01bb8c85eea88f93bbe7c8b9' -and
    (Test-Path -LiteralPath (Join-Path $foxTools 'SOURCE.txt')) -and
    (Test-Path -LiteralPath (Join-Path $foxTools 'GPL-3.0.txt'))) { return }
$foxHash = '99185d75d314a07dbba4a130c8ad986cbeb629a07927975636b341ca61f991df'
$foxTemp = Join-Path ([IO.Path]::GetTempPath()) ('foxbridge-tun-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $foxTemp -Force | Out-Null
try {
    $foxZip = Join-Path $foxTemp 'engine.zip'
    Invoke-WebRequest 'https://github.com/SagerNet/sing-box/releases/download/v1.14.4/sing-box-1.14.4-windows-amd64.zip' -OutFile $foxZip
    if ((Get-FileHash -LiteralPath $foxZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $foxHash) { throw 'TUN archive checksum mismatch' }
    Expand-Archive -LiteralPath $foxZip -DestinationPath (Join-Path $foxTemp 'unpacked')
    New-Item -ItemType Directory -Path $foxTools -Force | Out-Null
    $foxBinary = Get-ChildItem -LiteralPath (Join-Path $foxTemp 'unpacked') -Recurse -Filter sing-box.exe -File
    if (@($foxBinary).Count -ne 1) { throw 'Unexpected TUN archive layout' }
    Copy-Item -LiteralPath $foxBinary.FullName -Destination $foxEngine -Force
    # Ship the unmodified standalone program and its upstream license/source reference.
    Invoke-WebRequest 'https://raw.githubusercontent.com/SagerNet/sing-box/v1.14.4/LICENSE' -OutFile (Join-Path $foxTools 'sing-box-LICENSE.txt')
    Invoke-WebRequest 'https://raw.githubusercontent.com/gcc-mirror/gcc/master/COPYING3' -OutFile (Join-Path $foxTools 'GPL-3.0.txt')
    Set-Content -LiteralPath (Join-Path $foxTools 'SOURCE.txt') -Encoding utf8 -Value 'sing-box 1.14.4, unmodified official Windows amd64 binary. Corresponding source: https://github.com/SagerNet/sing-box/tree/v1.14.4 ; https://github.com/SagerNet/sing-box/archive/refs/tags/v1.14.4.tar.gz'
    Get-FileHash -LiteralPath $foxEngine -Algorithm SHA256
}
finally {
    $foxResolved = [IO.Path]::GetFullPath($foxTemp)
    if ((Split-Path -Parent $foxResolved) -eq [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') -and (Split-Path -Leaf $foxResolved).StartsWith('foxbridge-tun-')) {
        Remove-Item -LiteralPath $foxResolved -Recurse -Force
    }
}
