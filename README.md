This application was entirely vibe-coded with GPT.

# FoxBridge

<img src="development/FoxBridge.WinUI/Assets/AppIcon.png" width="80" alt="FoxBridge icon">

FoxBridge is a Windows app that routes CLI traffic through Firefox VPN using a preconfigured terminal.

[Download the latest release](https://github.com/ytrbqdkflbvbhy/foxbridge/releases/latest)

The interface and its built-in Readme are currently in Russian.

## Features

* Local HTTP CONNECT proxy using access from your Firefox VPN profile.
* Manual server selection and saved profile, server and port preferences.
* IP information, trust score and risk indicators from Net.Coffee.
* PowerShell configured with proxy variables and an IP-based TZ value.
* Traffic quota display, session activity, connection recovery and an event log.
* Dependency checks, dark and light themes, and built-in usage instructions.

## Run

1. Download `FoxBridge-v0.7.0-win-x64.zip` from Releases.
2. Extract the whole archive into a writable folder.
3. Run `FoxBridge.WinUI.exe`. Keep the other files, `Assets` and `Backend` beside it.
4. Select your Firefox profile and load it.
5. Choose a server, enable the proxy and wait for the IP check.
6. Open the protected terminal and run your CLI tools there.

Requirements:

* Windows 11 x64, the tested platform.
* [.NET Runtime 10 x64](https://dotnet.microsoft.com/download/dotnet/10.0).
* [Windows App Runtime 2.6.4 x64](https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads).
* [Python 3.12 or newer](https://www.python.org/downloads/windows/) available in PATH.
* A Firefox profile signed in to an account with available Firefox VPN access.

The runtimes are installed separately. Python packages such as PySide6 are not
required by the current WinUI application. Missing .NET or Windows App Runtime
can produce an installation prompt before the window opens. Python and backend
file checks are shown in Settings.

## How it works

The C# WinUI 3 interface starts a Python backend through private stdin/stdout pipes.
The backend listens on `127.0.0.1` and forwards supported traffic to the selected
Firefox VPN proxy using account access from the selected Firefox profile.

Opening the protected terminal sets `HTTP_PROXY`, `HTTPS_PROXY`, `WS_PROXY`,
`WSS_PROXY`, `NO_PROXY` and `TZ` for that terminal and its child processes.
The timezone comes from Net.Coffee, with a country-based estimate when needed.
The Windows system timezone and system proxy settings are unchanged.

## Limitations and privacy

* Applications must support the supplied proxy and TZ environment variables.
* The protected terminal does not provide a sandbox, system-wide VPN or network kill switch.
* Existing applications keep their original environment. Programs that ignore proxy settings can connect directly.
* Net.Coffee receives the IP being checked. Its score is not an Anthropic rating or an access guarantee.
* Mozilla's traffic quota belongs to the account and can include other Firefox VPN usage.
* Local programs that can reach the listening port can use the proxy and consume quota.
* Keep Firefox profiles and authorization data private. Local preferences are stored in `foxbridge-ui-settings.json` beside the app and must not be shared.

FoxBridge is an unofficial client and is not affiliated with Mozilla or Anthropic.
Use it according to the terms of the services you access.

## Build

Development requires the .NET 10 SDK, Windows SDK and Windows build tools suitable
for WinUI 3. The project targets Windows SDK 10.0.26100.0 and pins Windows App SDK
components to 2.6.4. Python 3.12+ is required to run the backend and its checks.

From the repository root in PowerShell:

```powershell
# Debug builds stay inside development and do not replace the ready application.
dotnet build development/FoxBridge.WinUI/FoxBridge.WinUI.csproj -c Debug -p:Platform=x64

# Publish the ready Release application to FoxBridge-WinUI.
& ./development/Publish-Release.ps1

# Package a distributable ZIP and its SHA256 checksum without personal settings.
& ./development/Package-Release.ps1 -Version 0.7.0
```

Release archives are written to `development/release-artifacts` and are excluded
from Git. The publication script preserves your local settings and updates the
local `FoxBridge.lnk` shortcut.

## Checks

```powershell
dotnet run --project development/FoxBridge.WinUI.Checks/FoxBridge.WinUI.Checks.csproj
python development/FoxBridge.WinUI/Backend/test_worker.py -v
Push-Location development/FoxBridge-source
python -m unittest test_bridge test_netcoffee test_reliability test_launcher -v
Pop-Location
```

These checks use synthetic data and local test servers. They do not sign in to a
Firefox account or make a real VPN connection.

## Repository layout

| Path | Purpose |
| --- | --- |
| `development/FoxBridge.WinUI` | Current C# and XAML application and stdio adapter |
| `development/FoxBridge-source` | Shared Python engine, tests and retained Qt prototype |
| `development/FoxBridge.WinUI.Checks` | Formatting, preferences and dependency checks |
| `development/distribution` | Release instructions and third-party license texts |
| `development/design` | Reproducible icon asset generation |
| `FoxBridge-WinUI` | Local ready Release output, excluded from Git |

GitHub Actions builds and checks changes. Pushing a version tag such as `v0.7.0`
builds a Windows x64 ZIP, adds a checksum and publishes a GitHub Release.

## License

Original code and artwork are licensed under [MIT](LICENSE).
See [third-party notices](THIRD-PARTY-NOTICES.md) for dependency terms.
