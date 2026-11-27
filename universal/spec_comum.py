"""
Partes comuns dos specs do leitor universal (Windows: leitor.spec; Linux:
leitor_linux.spec): o que entra, o que fica de fora e os arquivos embutidos.
"""

import os

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, '..'))

PATHEX = [RAIZ, os.path.join(RAIZ, 'urit-8021')]

# Ícone (a janela usa) e CHANGELOG (o "Sobre esta versão" mostra o trecho da versão).
DATAS = [(os.path.join(AQUI, 'icone'), 'icone'), (os.path.join(RAIZ, 'CHANGELOG.md'), '.')]

HIDDENIMPORTS = [
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
]

# Módulos do Qt que o painel não usa (o executável fica menor).
EXCLUDES = ['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick',
              'PySide6.QtPdf', 'PySide6.QtMultimedia', 'PySide6.QtOpenGL', 'PySide6.QtSql', 'PySide6.QtTest',
              'PySide6.QtDBus', 'PySide6.QtConcurrent', 'PySide6.QtDesigner', 'tkinter']
