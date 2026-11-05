param(
    [string]$EdgePath = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$assetFolder = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\FoxBridge.WinUI\Assets'))
$renderFolder = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'icon-render-temp'))
if ((Split-Path -Parent $renderFolder) -ne $PSScriptRoot) { throw 'Unexpected temporary directory' }
New-Item -ItemType Directory -Path $renderFolder -Force | Out-Null
$svg = [IO.File]::ReadAllText((Join-Path $assetFolder 'AppIcon.svg'))
$html = '<!doctype html><html><meta charset="utf-8"><style>html,body{margin:0;width:512px;height:512px;background:transparent;overflow:hidden}svg{display:block;width:512px;height:512px}</style>' + $svg + '</html>'
$htmlPath = Join-Path $renderFolder 'icon.html'
$pngPath = Join-Path $renderFolder 'icon.png'
[IO.File]::WriteAllText($htmlPath, $html)
$edgeArguments = @('--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-extensions', '--hide-scrollbars',
    '--default-background-color=00000000', '--force-device-scale-factor=1', '--window-size=512,512',
    "--screenshot=$pngPath", "--user-data-dir=$(Join-Path $renderFolder 'browser')", ([Uri]$htmlPath).AbsoluteUri)
$browser = Start-Process -FilePath $EdgePath -ArgumentList $edgeArguments -WindowStyle Hidden -PassThru
if (-not $browser.WaitForExit(25000)) { throw 'Icon renderer did not finish; temporary files retained' }
$source = [Drawing.Bitmap]::FromFile($pngPath)
try {
    if ($source.Width -ne 512 -or $source.Height -ne 512) { throw 'Unexpected rendered dimensions' }
    function New-IconBitmap([int]$Width, [int]$Height) {
        $bitmap = [Drawing.Bitmap]::new($Width, $Height, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([Drawing.Color]::Transparent)
            $graphics.CompositingQuality = [Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $size = [Math]::Min($Width, $Height)
            $graphics.DrawImage($source, [Drawing.Rectangle]::new(($Width - $size) / 2, ($Height - $size) / 2, $size, $size))
        } finally { $graphics.Dispose() }
        return $bitmap
    }
    $pngAssets = @{
        'AppIcon.png' = @(256,256)
        'LockScreenLogo.scale-200.png' = @(48,48)
        'SplashScreen.scale-200.png' = @(1240,600)
        'Square150x150Logo.scale-200.png' = @(300,300)
        'Square44x44Logo.scale-200.png' = @(88,88)
        'Square44x44Logo.targetsize-24_altform-unplated.png' = @(24,24)
        'Square44x44Logo.targetsize-48_altform-lightunplated.png' = @(48,48)
        'StoreLogo.png' = @(50,50)
        'Wide310x150Logo.scale-200.png' = @(620,300)
    }
    foreach ($name in $pngAssets.Keys) {
        $bitmap = New-IconBitmap $pngAssets[$name][0] $pngAssets[$name][1]
        try { $bitmap.Save((Join-Path $assetFolder $name), [Drawing.Imaging.ImageFormat]::Png) }
        finally { $bitmap.Dispose() }
    }
    $entries = foreach ($size in @(16,20,24,32,40,48,64,128,256)) {
        $bitmap = New-IconBitmap $size $size
        $memory = [IO.MemoryStream]::new()
        try {
            $bitmap.Save($memory, [Drawing.Imaging.ImageFormat]::Png)
            @{ Size=$size; Bytes=$memory.ToArray() }
        } finally { $bitmap.Dispose(); $memory.Dispose() }
    }
    $output = [IO.File]::Create((Join-Path $assetFolder 'AppIcon.ico'))
    $writer = [IO.BinaryWriter]::new($output)
    try {
        $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$entries.Count)
        $offset = 6 + 16 * $entries.Count
        foreach ($entry in $entries) {
            $dimension = if ($entry.Size -eq 256) { 0 } else { $entry.Size }
            $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
            $writer.Write([byte]0); $writer.Write([byte]0)
            $writer.Write([uint16]1); $writer.Write([uint16]32)
            $writer.Write([uint32]$entry.Bytes.Length); $writer.Write([uint32]$offset)
            $offset += $entry.Bytes.Length
        }
        foreach ($entry in $entries) { $writer.Write([byte[]]$entry.Bytes) }
    } finally { $writer.Dispose() }
} finally { $source.Dispose() }
Remove-Item -LiteralPath $renderFolder -Recurse -Force
Write-Output 'Updated SVG based PNG assets and ICO with 9 sizes.'
