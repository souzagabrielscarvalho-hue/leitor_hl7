# -*- mode: python ; coding: utf-8 -*-
# Leitor universal: um .exe com o painel e todas as máquinas.
# Painel em Qt (PySide6-Essentials); o ícone vai junto para a janela usar.

import os

RAIZ = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    ['leitor.py'],
    pathex=[RAIZ, os.path.join(RAIZ, 'urit-8021')],
    binaries=[],
    # Ícone (a janela usa) e CHANGELOG (o "Sobre esta versão" mostra o trecho da versão).
    datas=[(os.path.join(SPECPATH, 'icone'), 'icone'), (os.path.join(RAIZ, 'CHANGELOG.md'), '.')],
    hiddenimports=[
        # Máquinas: os mesmos nomes de shared/maquinas.py (importadas por nome).
        'bh5100.bh5100',
        'ca150.ca150',
        'Coagmaster.coagmaster',
        'mek7300.mek7300',
        'pkl.pkl',
        'urit8021',
        'vidas1600.vidas1600',
        'shared.file_cleanup',
        'shared.config_loader',
        'shared.base_analisador',
        'shared.health_server',
        'shared.exam_images_uploader',
        'shared.maquinas',
        'shared.painel.config',
        'shared.painel.portas',
        'shared.painel.processos',
        'shared.painel.nucleo',
        'shared.painel.janela',
        'shared.painel.tema',
        'shared.painel.atualizacao',
        'shared.painel.janela_versao',
        'shared.painel.acesso',
        'shared.painel.raiz',
        'shared.painel.inicializacao',
        'shared.conexao_tcp',
        'serial',
        'serial.tools.list_ports',
        'requests',
        'PIL',
        # Janela do painel (Qt Widgets).
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtSvg',
        'json',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Módulos do Qt que o painel não usa (o .exe fica menor).
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick',
              'PySide6.QtPdf', 'PySide6.QtMultimedia', 'PySide6.QtOpenGL', 'PySide6.QtSql', 'PySide6.QtTest',
              'PySide6.QtDBus', 'PySide6.QtConcurrent', 'PySide6.QtDesigner', 'tkinter'],
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
    name='leitor',
    # Ícone próprio (fonte: universal/icone/leitor.svg).
    icon=os.path.join(SPECPATH, 'icone', 'leitor.ico'),
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
