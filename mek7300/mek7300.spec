# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file para gerar o executável do MEK7300 Service.
"""

import sys
from pathlib import Path

block_cipher = None

BASE_DIR = Path('.')

a = Analysis(
    ['mek7300.py'],
    pathex=['..'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        'shared',
        'shared.file_cleanup',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MEK7300service',
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
    icon=None,
)