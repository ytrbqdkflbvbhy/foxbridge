# Third-party components

The MIT license applies to FoxBridge's original code and artwork. Third-party
components retain their own licenses.

TUN mode runs the unmodified standalone sing-box 1.14.4 Windows amd64 executable
as a separate process. It is licensed under GPL-3.0-or-later with the upstream
name/association restriction. Its notices and GPL text accompany the executable
in `Tools/`. FoxBridge does not link to sing-box. Corresponding source for this
exact upstream release: https://github.com/SagerNet/sing-box/tree/v1.14.4
and https://github.com/SagerNet/sing-box/archive/refs/tags/v1.14.4.tar.gz .

The current WinUI Windows distribution includes managed Windows SDK, WinUI,
Windows App SDK, C#/WinRT and WebView2 components from Microsoft NuGet packages.
Their upstream license files accompany the distribution in `licenses/`.

The Mozilla CA certificate collection, converted by curl, is included as
`Backend/cacert.pem` under MPL 2.0. Its embedded notices are retained and the
license text is included in `licenses/MPL-2.0.txt`.
Source and updates: https://curl.se/docs/caextract.html

.NET Runtime, Windows App Runtime and Python are installed separately and are
not bundled with this distribution. Their own license terms apply.

The retained Qt prototype under `development/FoxBridge-source` has additional
optional dependencies described in its own third-party notices. Those Qt and
PyInstaller runtime components are not part of the current WinUI release.

Upstream projects:

* Windows App SDK: https://github.com/microsoft/WindowsAppSDK
* WinUI: https://github.com/microsoft/microsoft-ui-xaml
* C#/WinRT: https://github.com/microsoft/CsWinRT
* WebView2: https://www.nuget.org/packages/Microsoft.Web.WebView2
* Windows SDK .NET projections: https://www.nuget.org/packages/Microsoft.Windows.SDK.NET.Ref
