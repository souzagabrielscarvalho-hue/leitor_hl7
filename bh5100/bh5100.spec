# -*- mode: python ; coding: utf-8 -*-
#
# Spec compativel com PyInstaller 3.6 (imagem Docker/Wine) e com PyInstaller 6.x
# (venv do Windows). O bh5100 roda como aplicativo de bandeja/GUI, sem console.

import os

_raiz = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    ['bh5100.py'],
    pathex=[_raiz],
    binaries=[],
    datas=[],
    hiddenimports=[
        'shared.file_cleanup',
        'shared.config_loader',
        'shared.base_analisador',
        'shared.exam_images_uploader',
        'shared.health_server',
        'serial',
        'serial.tools',
        'requests',
        'PIL',
        'PIL.Image',
        'PIL.ImageFile',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Nao empacotar as DLLs de sistema do Wine (api-ms-win-* e ucrtbase.dll).
# No Windows 10/11 elas ja existem no sistema e as copias do Wine podem
# quebrar o executavel. VCRUNTIME140* continua no pacote.
_excluir_dlls = ('api-ms-win-', 'ucrtbase.dll')
a.binaries = [
    b for b in a.binaries
    if not str(b[0]).replace('\\', '/').rsplit('/', 1)[-1].lower().startswith(_excluir_dlls)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='bh5100',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
