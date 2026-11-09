# PyInstaller spec - one-folder, windowed build.   pyinstaller --noconfirm serial_monitor.spec
import os, sys
from PyInstaller.utils.hooks import collect_submodules

win = sys.platform == "win32"
sys.path.insert(0, os.path.join(SPECPATH, "tools"))
import make_version_info

hidden = collect_submodules("serial.tools")
if win:
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms"]
version_file = None
if win:                                   # version resource: helps Defender/AppLocker reputation + IT allow-listing
    version_file = os.path.join(SPECPATH, "version_info.txt")
    make_version_info.write(version_file, open(os.path.join(SPECPATH, "VERSION")).read().strip())

a = Analysis(["app.py"], pathex=["."],
             datas=[("static", "static"), ("assets/icon.ico", "assets"), ("assets/icon.png", "assets")],
             hiddenimports=hidden,
             excludes=["matplotlib", "numpy", "scipy", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SerialMonitor", console=False, upx=False,
          icon="assets/icon.ico" if win else None, version=version_file)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="SerialMonitor")
