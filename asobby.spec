# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

datas = [('client/assets/logo-source.png', 'assets')]
binaries = []
hiddenimports = ['pystray._win32']

try:
    tmp = collect_all('windows_toasts')
    datas += tmp[0]
    binaries += tmp[1]
    hiddenimports += tmp[2]
except Exception:
    pass

a = Analysis(
    ['client\\src\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    # pystray はバックエンドを動的 import するため明示する
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='asobby',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='client\\version_info.txt',
    icon='server\\app\\static\\favicon.ico',
)
