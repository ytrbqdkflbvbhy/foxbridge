param(
    [Parameter(Mandatory)][string]$Repository,
    [Parameter(Mandatory)][string]$Tag
)
$ErrorActionPreference = 'Stop'
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or $Tag -notmatch '^v\d+\.\d+\.\d+$') {
    throw 'Invalid repository or version tag'
}
if (-not $env:GITHUB_TOKEN) { throw 'A repository-scoped GITHUB_TOKEN is required' }
$foxHeaders = @{
    Authorization = 'Bearer ' + $env:GITHUB_TOKEN
    Accept = 'application/vnd.github+json'
    'User-Agent' = 'FoxBridge-release'
    'X-GitHub-Api-Version' = '2026-03-10'
}
$foxApi = 'https://api.github.com/repos/' + $Repository
$foxAssets = @(
    Join-Path $PSScriptRoot "release-artifacts/FoxBridge-$Tag-win-x64.zip"
    Join-Path $PSScriptRoot "release-artifacts/FoxBridge-$Tag-win-x64.zip.sha256"
)
foreach ($foxAsset in $foxAssets) {
    if (-not (Test-Path -LiteralPath $foxAsset)) { throw "Missing release asset: $foxAsset" }
}
$foxRelease = $null
try { $foxRelease = Invoke-RestMethod -Uri "$foxApi/releases/tags/$Tag" -Headers $foxHeaders }
catch { if ([int]$_.Exception.Response.StatusCode -ne 404) { throw } }
if ($foxRelease) {
    if (-not $foxRelease.draft) { throw 'This release is already published; existing assets were not changed' }
} else {
    $foxBody = @{
        tag_name = $Tag
        name = 'FoxBridge ' + $Tag.TrimStart('v')
        body = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'distribution/RELEASE-NOTES.md'))
        draft = $true
        prerelease = $false
    } | ConvertTo-Json
    $foxRelease = Invoke-RestMethod -Uri "$foxApi/releases" -Method Post -Headers $foxHeaders -ContentType 'application/json' -Body $foxBody
}
foreach ($foxAsset in $foxAssets) {
    $foxName = [IO.Path]::GetFileName($foxAsset)
    if (@($foxRelease.assets | Where-Object name -eq $foxName).Count -gt 0) { throw "Asset already exists in draft: $foxName" }
    $foxContentType = if ($foxName.EndsWith('.zip')) { 'application/zip' } else { 'text/plain' }
    $foxUpload = $foxRelease.upload_url.Split('{')[0] + '?name=' + [Uri]::EscapeDataString($foxName)
    Invoke-RestMethod -Uri $foxUpload -Method Post -Headers $foxHeaders -ContentType $foxContentType -InFile $foxAsset | Out-Null
}
$foxPublished = Invoke-RestMethod -Uri "$foxApi/releases/$($foxRelease.id)" -Method Patch -Headers $foxHeaders `
    -ContentType 'application/json' -Body (@{draft=$false} | ConvertTo-Json)
Write-Output ('Published: ' + $foxPublished.html_url)
