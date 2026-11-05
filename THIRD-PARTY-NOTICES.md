# Third-party components

The MIT license applies to FoxBridge's original code and artwork. Third-party
components retain their own licenses.

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
