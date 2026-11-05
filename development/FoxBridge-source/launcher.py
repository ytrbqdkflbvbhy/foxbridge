"""Per-terminal proxy configuration and Codex shared-server isolation."""
import base64
import os
from timezones import validate_timezone


def proxy_environment(port, source=None, timezone=None):
    env = dict(os.environ if source is None else source)
    for key in list(env):
        if key.upper() in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'WS_PROXY', 'WSS_PROXY'):
            del env[key]
    url = f'http://127.0.0.1:{int(port)}'
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'WS_PROXY', 'WSS_PROXY'):
        env[key] = url
    env['NO_PROXY'] = 'localhost,127.0.0.1,::1'
    if timezone is not None:
        timezone = validate_timezone(timezone)
        for key in list(env):
            if key.upper() == 'TZ':
                del env[key]
        env['TZ'] = timezone
    return env


def powershell_bootstrap(port, timezone=None):
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('Invalid local port')
    tz_setup = ''
    if timezone is not None:
        timezone = validate_timezone(timezone)
        tz_setup = f"$env:TZ = '{timezone}'\nWrite-Host 'FoxBridge TZ environment: {timezone}' -ForegroundColor Cyan\n"
    return tz_setup + f'''$env:HTTP_PROXY = 'http://127.0.0.1:{port}'
$env:HTTPS_PROXY = 'http://127.0.0.1:{port}'
$env:WS_PROXY = 'http://127.0.0.1:{port}'
$env:WSS_PROXY = 'http://127.0.0.1:{port}'
$env:NO_PROXY = 'localhost,127.0.0.1,::1'
Remove-Item Env:ALL_PROXY -ErrorAction SilentlyContinue

$foxCodexCommand = Get-Command codex -All -ErrorAction SilentlyContinue |
    Where-Object {{ $_.CommandType -eq 'Application' -or $_.CommandType -eq 'ExternalScript' }} |
    Select-Object -First 1
if ($foxCodexCommand) {{
    $foxCodexHelp = (& $foxCodexCommand.Source --help 2>$null | Out-String)
    if ($foxCodexHelp -match '--no-daemon') {{
        $global:FoxBridgeCodexExecutable = $foxCodexCommand.Source
        Remove-Item Alias:codex -ErrorAction SilentlyContinue
        function global:codex {{
            $foxArgs = @($args)
            $foxManagement = @('agents', 'app-server', 'remote-control', 'app', 'login', 'logout',
                'update', 'help', 'completion', 'debug', 'features', 'mcp', 'plugin', 'sandbox',
                'apply', 'a')
            $foxRemote = @($foxArgs | Where-Object {{ $_ -eq '--remote' -or $_ -like '--remote=*' }}).Count -gt 0
            if (($foxArgs -contains '--no-daemon') -or $foxRemote -or
                ($foxArgs.Count -gt 0 -and $foxArgs[0] -in $foxManagement)) {{
                & $global:FoxBridgeCodexExecutable @foxArgs
            }} else {{
                & $global:FoxBridgeCodexExecutable --no-daemon @foxArgs
            }}
        }}
        Write-Host 'FoxBridge: Codex runs separately with --no-daemon in this terminal.' -ForegroundColor Cyan
    }} else {{
        Write-Warning 'FoxBridge: this Codex version has no --no-daemon. Isolation from an existing server is not verified.'
    }}
}}
Write-Host 'FoxBridge proxy: http://127.0.0.1:{port}' -ForegroundColor Cyan
Write-Host 'Proxy settings are inherited by child processes. Programs that ignore them are not blocked.' -ForegroundColor DarkGray
'''


def powershell_arguments(port, timezone=None):
    encoded = base64.b64encode(powershell_bootstrap(port, timezone).encode('utf-16-le')).decode('ascii')
    return ['powershell.exe', '-NoLogo', '-NoExit', '-EncodedCommand', encoded]
