# PyInstaller spec: ykrecover.exe (console) and ykrecover-gui.exe (windowed) from one analysis, as a one-dir
# build: both executables share one _internal folder in dist\ykrecover-windows-x64, so nothing is unpacked to
# a temporary folder on every start (the GUI runs ykrecover.exe --ykman for every ykman call).
# Build with build-windows.ps1. SPECPATH is defined by PyInstaller.
import os

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

recovery = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[recovery],
    hiddenimports=collect_submodules("ykrecover") + collect_submodules("ykman") + collect_submodules("yubikit"),
    datas=copy_metadata("yubikey-manager"),
    excludes=["pytest"],
)
pyz = PYZ(a.pure)

console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ykrecover", console=True, upx=False)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ykrecover-gui", console=False, upx=False)
coll = COLLECT(console, gui, a.binaries, a.datas, name="ykrecover-windows-x64", upx=False)
