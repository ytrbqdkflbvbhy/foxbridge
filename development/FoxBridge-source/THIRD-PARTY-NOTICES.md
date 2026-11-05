# Third-party components

FoxBridge's Windows folder bundles unmodified, dynamically loaded components:

- Python 3.14.4 — Python Software Foundation License. https://www.python.org/downloads/release/python-3144/ and https://docs.python.org/3/license.html
- Qt / PySide6 / Shiboken6 6.12.0 — the open-source license options are stated in the installed package metadata as LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only. This application uses the LGPL option for the dynamically loaded Qt libraries. Source and build instructions for Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/ ; Qt sources: https://download.qt.io/official_releases/qt/ . No modifications to those libraries were made. The source code of FoxBridge is supplied separately and the shared libraries can be replaced in the application folder.
- PyInstaller 6.22.3 bootloader — GPL with the PyInstaller exception permitting distribution of bundled applications. https://pyinstaller.org/en/stable/license.html and https://github.com/pyinstaller/pyinstaller
- Mozilla CA certificate collection, converted by curl — MPL 2.0. Source, conversion and current versions: https://curl.se/docs/caextract.html . The bundle retains its own notices.
- Windows runtime libraries included by the packager retain their upstream terms.

License texts accompany the Windows distribution in `licenses/`. The Python dependencies are pinned in `requirements.txt`; the network implementation uses the Python standard library. Firefox source files used for protocol research are not bundled with this application.
