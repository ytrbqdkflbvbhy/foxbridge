FoxBridge 0.7.0 for Windows x64

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
The interface and its built-in Readme are currently in Russian.

The proxy settings apply to applications that support the supplied environment
variables. There is no system-wide VPN, sandbox or network kill switch.
TZ is passed to the terminal; support depends on the program using it.
The Net.Coffee score is a third-party assessment and does not guarantee access.

Do not share your Firefox profile, tokens or foxbridge-ui-settings.json.
Those personal files are not included in this archive.

Source and releases: https://github.com/ytrbqdkflbvbhy/foxbridge
FoxBridge is an unofficial client and is not affiliated with Mozilla or Anthropic.
Original code and artwork: MIT. See LICENSE and THIRD-PARTY-NOTICES.md.
