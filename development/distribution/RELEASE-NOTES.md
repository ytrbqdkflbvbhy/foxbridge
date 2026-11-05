First WinUI 3 release of FoxBridge for Windows x64.

* Local HTTP CONNECT proxy using Firefox VPN account access.
* Manual server selection, saved profile and port preferences.
* IP diagnostics and risk information from Net.Coffee.
* PowerShell configured with proxy variables and an IP-based timezone.
* Runtime dependency checks, themes, activity statistics and an in-app Readme.

Download `FoxBridge-v0.7.0-win-x64.zip`, extract every file and launch
`FoxBridge.WinUI.exe`. The `.sha256` asset contains the archive checksum.

Requires .NET Runtime 10 x64, Windows App Runtime 2.6.4 x64 and Python 3.12+
available in PATH. These runtimes are installed separately. The interface is in Russian.

The proxy is limited to applications that honor the configured environment.
It does not provide system-wide routing or a network kill switch.
Live account access and VPN connectivity depend on your Firefox account and network.

Original code and artwork are licensed under MIT. Third-party terms are included.
