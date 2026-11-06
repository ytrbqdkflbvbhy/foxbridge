FoxBridge 0.8.0 for Windows x64

Extract the entire archive and run FoxBridge.WinUI.exe.
Keep Assets, Backend and the other files beside the executable.

Requirements:
Windows 11 x64 (tested).
.NET Runtime 10 x64.
Windows App Runtime 2.6.4 x64.
Python 3.12 or newer available in PATH.
A Firefox profile signed in to an account with access to Firefox VPN.

Downloads:
https://dotnet.microsoft.com/download/dotnet/10.0
https://learn.microsoft.com/windows/apps/windows-app-sdk/downloads
https://www.python.org/downloads/windows/

Select your Firefox profile, load it, choose a server and enable the proxy.
Wait for the IP check and timezone preparation, then open the protected terminal.
Run your CLI tools from that PowerShell window.
The Separate applications mode opens normal Firefox with the selected VPN profile
and the local HTTP/HTTPS proxy. Close all windows of that profile before launch.
Saved logins, cookies and previous tabs are retained using Firefox session restore.
Temporary proxy preferences are restored after Firefox exits. Crash recovery uses
foxbridge-preferences.json inside the profile. Do not delete that recovery file.
Firefox runs without WebDriver, RemoteAgent or automatic locale/timezone overrides.
Browser timezone/language extensions are optional and must be installed manually
in the selected profile. They are not an official requirement for Claude access.
The terminal still receives the TZ environment variable from Net.Coffee data.
FoxBridge requests normal closing of its browser windows when the app exits.
Current instructions and FAQ are in Help/README.md and in-app Readme.
The interface and its built-in Readme are currently in Russian.

The default CLI mode applies to applications that support the supplied environment
variables. For PC-wide TCP routing, run FoxBridge as administrator and select TUN.
Without administrator privileges the TUN button is disabled and a warning appears.
Before starting TUN, FoxBridge checks the outgoing IP and obtains a valid Net.Coffee
score through the local proxy. If this check fails, TUN is not started.
TUN sends DNS over HTTPS through the proxy and rejects other UDP routed into TUN.
Local network and explicit routes/interface bindings may bypass TUN.
Disconnecting or a TUN crash restores normal Windows networking. No kill switch.
Keep the Tools folder with the app. It contains the standalone sing-box engine
and its GPL license and source references. All tunneled traffic uses Mozilla quota.
TZ is passed to the terminal; support depends on the program using it.
The Net.Coffee score is a third-party assessment and does not guarantee access.

Do not share your Firefox profile, tokens or foxbridge-ui-settings.json.
Those personal files are not included in this archive.

Source and releases: https://github.com/ytrbqdkflbvbhy/foxbridge
FoxBridge is an unofficial client and is not affiliated with Mozilla or Anthropic.
Original code and artwork: MIT. See LICENSE and THIRD-PARTY-NOTICES.md.
