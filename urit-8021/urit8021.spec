# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['urit8021.py'],
    pathex=['..'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'shared.file_cleanup',
        'shared.config_loader',
        'shared.base_analisador',
        'shared.health_server',
        'serial',
        'requests',
        'json',
    ],
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
    name='urit8021',
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
)
