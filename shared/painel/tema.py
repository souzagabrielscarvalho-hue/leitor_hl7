"""
Tema da janela do painel: cores, folha de estilo (QSS) e ícones de linha.

Visual de console técnico: cinzas neutros, um tom de destaque (o petróleo do
ícone), cantos quase retos, números em fonte monoespaçada.
"""

from typing import Dict

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

CLARO: Dict[str, str] = {
    "fundo": "#e9edf1", "painel": "#ffffff", "painel2": "#f5f7f9", "painel3": "#eef2f5",
    "linha": "#d4dbe2", "linha_forte": "#b8c2cc",
    "texto": "#16202a", "texto2": "#475562", "texto3": "#7a8692",
    "destaque": "#0f766e", "destaque_hover": "#0c5f58", "destaque_suave": "#e1f1ef", "destaque_texto": "#ffffff",
    "ok": "#15803d", "aviso": "#b45309", "erro": "#b91c1c", "ocioso": "#8a96a3",
    "ok_fundo": "#edf7f0", "aviso_fundo": "#fff7e8", "erro_fundo": "#fdf0f0",
    "console": "#0c1217", "console_texto": "#c9d4dd", "console_fraco": "#6f808d",
    "console_ok": "#86e3b0", "console_aviso": "#f3c969", "console_erro": "#ff8f87",
}

ESCURO: Dict[str, str] = {
    "fundo": "#0b1015", "painel": "#121a21", "painel2": "#161f27", "painel3": "#1a252e",
    "linha": "#23303b", "linha_forte": "#2f3f4c",
    "texto": "#dbe3ea", "texto2": "#9fadb9", "texto3": "#6c7a86",
    "destaque": "#2dd4bf", "destaque_hover": "#5eead4", "destaque_suave": "#0f2f2d", "destaque_texto": "#06201d",
    "ok": "#4ade80", "aviso": "#fbbf24", "erro": "#f87171", "ocioso": "#6c7a86",
    "ok_fundo": "#10261a", "aviso_fundo": "#2a2210", "erro_fundo": "#2c1517",
    "console": "#080c10", "console_texto": "#c9d4dd", "console_fraco": "#6f808d",
    "console_ok": "#86e3b0", "console_aviso": "#f3c969", "console_erro": "#ff8f87",
}

FONTE = '"Segoe UI Variable Text", "Segoe UI", "Noto Sans", sans-serif'
MONO = '"Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace'

# Ícones 16 px (traço), os mesmos desenhos em qualquer cor.
CAMINHOS = {
    "iniciar": ["M5 3.5v9l7.5-4.5z"],
    "parar": ["M4.5 4.5h7v7h-7z"],
    "reiniciar": ["M13 8a5 5 0 1 1-1.46-3.54", "M13 2.8v2.9h-2.9"],
    "salvar": ["M3 3h8.2L13 4.8V13H3z", "M5.5 3v3h4.5V3", "M5 13V9.5h6V13"],
    "desfazer": ["M4 6.5h6a3 3 0 0 1 0 6H6.5", "M6.5 4 4 6.5 6.5 9"],
    "porta": ["M3 5.5h10v5H3z", "M5.5 8h.01", "M8 8h.01", "M10.5 8h.01", "M5 10.5v2", "M11 10.5v2"],
    "log": ["M3 3h10v10H3z", "M5.5 6l1.5 1.5L5.5 9", "M8.5 9.5H11"],
    "status": ["M2.5 9h2.5l1.5-4 2.5 7 1.5-3h3"],
    "config": ["M2.5 4.5h6", "M11 4.5h2.5", "M2.5 11.5h2", "M7 11.5h6.5", "M9.5 3v3", "M5.5 10v3"],
    "alerta": ["M8 2.5 14 13H2z", "M8 6.5v3", "M8 11.3h.01"],
    "ok": ["M3.5 8.5 6.5 11.5 12.5 4.5"],
    "info": ["M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z", "M8 7.5V11", "M8 5.2h.01"],
    "pasta": ["M2.5 4h4l1.3 1.5h5.7v7h-11z"],
    "verificar": ["M7 12.5a5.5 5.5 0 1 1 0-11 5.5 5.5 0 0 1 0 11z", "M11 11l3 3"],
    "atualizar": ["M13 8a5 5 0 1 1-1.46-3.54", "M13 2.8v2.9h-2.9"],
    "mais": ["M8 3.5v9", "M3.5 8h9"],
    "remover": ["M3 4.5h10", "M6.5 4.5V3h3v1.5", "M4.5 4.5l.7 8.5h5.6l.7-8.5"],
    "cadeado": ["M4 7.5h8v6H4z", "M5.5 7.5V5.5a2.5 2.5 0 0 1 5 0v2", "M8 10v1.2"],
    "cadeado_aberto": ["M4 7.5h8v6H4z", "M5.5 7.5V5.5a2.5 2.5 0 0 1 4.9-.7", "M8 10v1.2"],
    "sair": ["M6.5 3H3v10h3.5", "M10 5l3 3-3 3", "M13 8H6.5"],
    "tema": ["M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12z", "M8 2v12", "M8 5.5h3.5", "M8 8h4.5", "M8 10.5h3.5"],
    "olho": ["M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z", "M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z"],
}
PREENCHIDOS = {"iniciar", "parar"}


def escuro_no_sistema() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


# "sistema", "claro" ou "escuro" (menu Configurações > Tema; fica em leitor_painel.json).
PREFERENCIA = "sistema"


def definir_preferencia(preferencia: str) -> None:
    global PREFERENCIA
    PREFERENCIA = preferencia if preferencia in ("sistema", "claro", "escuro") else "sistema"


def cores() -> Dict[str, str]:
    if PREFERENCIA == "claro":
        return CLARO
    if PREFERENCIA == "escuro":
        return ESCURO
    return ESCURO if escuro_no_sistema() else CLARO


def icone(nome: str, cor: str, tamanho: int = 16) -> QIcon:
    preenche = cor if nome in PREENCHIDOS else "none"
    caminhos = "".join(f'<path d="{d}" fill="{preenche}"/>' for d in CAMINHOS.get(nome, []))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none" stroke="{cor}" '
        f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{caminhos}</svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    icon = QIcon()
    for escala in (1, 2):
        pix = QPixmap(tamanho * escala, tamanho * escala)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        renderer.render(p, QRectF(0, 0, tamanho * escala, tamanho * escala))
        p.end()
        pix.setDevicePixelRatio(escala)
        icon.addPixmap(pix)
    return icon


def led(cor: str, tamanho: int = 10) -> QPixmap:
    """LED de estado: círculo com halo suave."""
    escala = 2
    pix = QPixmap(tamanho * escala, tamanho * escala)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    halo = QColor(cor)
    halo.setAlpha(55)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(halo)
    p.drawEllipse(QRectF(0, 0, tamanho * escala, tamanho * escala))
    p.setBrush(QColor(cor))
    m = tamanho * escala * 0.2
    p.drawEllipse(QRectF(m, m, tamanho * escala - 2 * m, tamanho * escala - 2 * m))
    p.end()
    pix.setDevicePixelRatio(escala)
    return pix


def aplicar_paleta(app, c: Dict[str, str]) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["fundo"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["texto"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["painel"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["painel2"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["texto"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["painel"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["texto"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["destaque"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["destaque_texto"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["painel"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["texto"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["texto3"]))
    app.setPalette(pal)
    app.setStyleSheet(folha_de_estilo(c))


def folha_de_estilo(c: Dict[str, str]) -> str:
    return f"""
* {{ font-family: {FONTE}; font-size: 9.5pt; }}
QMainWindow, #Fundo {{ background: {c['fundo']}; }}
QToolTip {{ background: {c['painel']}; color: {c['texto']}; border: 1px solid {c['linha_forte']}; padding: 4px 6px; }}

#Titulo {{ background: {c['painel']}; border-bottom: 1px solid {c['linha']}; }}
#TituloNome {{ font-size: 10.5pt; font-weight: 600; color: {c['texto']}; }}
#TituloSub {{ color: {c['texto2']}; }}
#Separador {{ background: {c['linha_forte']}; }}
#Resumo {{ color: {c['texto2']}; }}

#Nav {{ background: {c['painel']}; border: none; border-right: 1px solid {c['linha']}; outline: none; }}
#Nav::item {{ border: none; border-left: 3px solid transparent; }}
#Nav::item:hover {{ background: {c['painel2']}; }}
#Nav::item:selected {{ background: {c['destaque_suave']}; border-left: 3px solid {c['destaque']}; }}
#NavGrupo {{ color: {c['texto3']}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
#NavNome {{ font-weight: 600; color: {c['texto']}; }}
#NavSub {{ font-family: {MONO}; font-size: 8pt; color: {c['texto3']}; }}
#NavEstado {{ font-size: 8.5pt; color: {c['texto3']}; }}
#NavEstado[estado="ok"] {{ color: {c['ok']}; }}
#NavEstado[estado="aviso"] {{ color: {c['aviso']}; }}
#NavEstado[estado="erro"] {{ color: {c['erro']}; }}
#NavPend {{ font-size: 8pt; color: {c['aviso']}; }}

#Cabeca {{ background: {c['painel']}; border-bottom: 1px solid {c['linha']}; }}
#CabecaNome {{ font-size: 13pt; font-weight: 600; color: {c['texto']}; }}
#CabecaInfo {{ color: {c['texto2']}; }}
#Tag {{ font-size: 7.5pt; font-weight: 700; letter-spacing: 1px; padding: 2px 7px; border-radius: 2px;
        color: {c['texto2']}; background: {c['painel2']}; border: 1px solid {c['linha']}; }}
#Tag[estado="ok"] {{ color: {c['ok']}; background: {c['ok_fundo']}; border-color: {c['ok']}; }}
#Tag[estado="aviso"] {{ color: {c['aviso']}; background: {c['aviso_fundo']}; border-color: {c['aviso']}; }}
#Tag[estado="erro"] {{ color: {c['erro']}; background: {c['erro_fundo']}; border-color: {c['erro']}; }}

QPushButton, QToolButton#Botao {{
    min-height: 26px; padding: 0 12px; border: 1px solid {c['linha_forte']}; border-radius: 3px;
    background: {c['painel']}; color: {c['texto']}; font-weight: 500;
}}
QPushButton:hover:!disabled {{ background: {c['painel2']}; border-color: {c['texto3']}; }}
QPushButton:pressed {{ background: {c['painel3']}; }}
QPushButton:disabled {{ color: {c['texto3']}; border-color: {c['linha']}; }}
QPushButton[papel="primario"] {{ background: {c['destaque']}; border-color: {c['destaque']}; color: {c['destaque_texto']}; }}
QPushButton[papel="primario"]:hover:!disabled {{ background: {c['destaque_hover']}; border-color: {c['destaque_hover']}; }}
QPushButton[papel="primario"]:disabled {{ background: {c['linha']}; border-color: {c['linha']}; color: {c['texto3']}; }}
QPushButton[papel="perigo"] {{ color: {c['erro']}; }}
QPushButton[papel="perigo"]:disabled {{ color: {c['texto3']}; }}
QPushButton:focus {{ outline: none; border-color: {c['destaque']}; }}

QTabWidget::pane {{ border: none; }}
QTabBar {{ background: {c['painel']}; qproperty-drawBase: 0; border-bottom: 1px solid {c['linha']}; }}
QTabBar::tab {{ background: transparent; color: {c['texto2']}; padding: 9px 14px; border: none;
                border-bottom: 2px solid transparent; font-weight: 500; }}
QTabBar::tab:hover {{ color: {c['texto']}; }}
QTabBar::tab:selected {{ color: {c['texto']}; border-bottom: 2px solid {c['destaque']}; }}
#BarraAbas {{ background: {c['painel']}; border-bottom: 1px solid {c['linha']}; }}

#Secao {{ background: {c['painel']}; border: 1px solid {c['linha']}; border-radius: 3px; }}
#SecaoTitulo {{ background: {c['painel2']}; border-bottom: 1px solid {c['linha']}; color: {c['texto2']};
                font-size: 8pt; font-weight: 600; letter-spacing: 1px; padding: 0 12px; min-height: 28px; max-height: 28px;
                border-top-left-radius: 3px; border-top-right-radius: 3px; }}
#Rotulo {{ background: {c['painel2']}; border-right: 1px solid {c['linha']}; border-bottom: 1px solid {c['linha']}; padding: 7px 12px; }}
#RotuloNome {{ color: {c['texto']}; font-weight: 500; }}
#RotuloChave {{ font-family: {MONO}; font-size: 8pt; color: {c['texto3']}; }}
#Valor {{ border-bottom: 1px solid {c['linha']}; padding: 7px 12px; }}
#Ajuda {{ font-size: 8.5pt; color: {c['texto3']}; }}
#Erro {{ font-size: 8.5pt; color: {c['erro']}; }}
#InfoPorta {{ font-size: 8.5pt; color: {c['texto2']}; }}
#Mono {{ font-family: {MONO}; font-size: 9pt; }}

QLineEdit, QComboBox {{
    min-height: 26px; padding: 0 8px; border: 1px solid {c['linha_forte']}; border-radius: 3px;
    background: {c['painel']}; color: {c['texto']}; selection-background-color: {c['destaque']};
}}
QLineEdit[mono="true"], QComboBox[mono="true"] {{ font-family: {MONO}; font-size: 9pt; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {c['destaque']}; }}
QLineEdit[invalido="true"], QComboBox[invalido="true"] {{ border-color: {c['erro']}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {c['painel2']}; color: {c['texto2']}; border-color: {c['linha']}; }}
#RotuloNome:disabled {{ color: {c['texto2']}; }}
QCheckBox:disabled {{ color: {c['texto3']}; }}
QComboBox QAbstractItemView {{ background: {c['painel']}; border: 1px solid {c['linha_forte']}; selection-background-color: {c['destaque_suave']}; selection-color: {c['texto']}; }}
QCheckBox {{ color: {c['texto2']}; spacing: 6px; }}

#Leituras {{ background: {c['painel']}; border: 1px solid {c['linha']}; border-radius: 3px; }}
QFrame#Leitura {{ border: none; border-right: 1px solid {c['linha']}; }}
QFrame#LeituraUltima {{ border: none; }}
#LeituraRot {{ color: {c['texto3']}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; }}
#LeituraVal {{ font-family: {MONO}; font-size: 15pt; font-weight: 600; color: {c['texto']}; }}
#LeituraVal[estado="ok"] {{ color: {c['ok']}; }}
#LeituraVal[estado="aviso"] {{ color: {c['aviso']}; }}
#LeituraVal[estado="erro"] {{ color: {c['erro']}; }}
#LeituraSub {{ color: {c['texto3']}; font-size: 8.5pt; }}

#Faixa {{ border: 1px solid {c['linha']}; border-left: 3px solid {c['linha_forte']}; border-radius: 2px; background: {c['painel']}; }}
#Faixa[estado="ok"] {{ border-left-color: {c['ok']}; background: {c['ok_fundo']}; }}
#Faixa[estado="aviso"] {{ border-left-color: {c['aviso']}; background: {c['aviso_fundo']}; }}
#Faixa[estado="erro"] {{ border-left-color: {c['erro']}; background: {c['erro_fundo']}; }}
#FaixaTexto {{ color: {c['texto']}; }}

#BarraAcoes {{ background: {c['painel']}; border: 1px solid {c['linha']}; border-radius: 3px; }}
#Alterado {{ color: {c['aviso']}; font-size: 8.5pt; }}
#Arquivo {{ font-family: {MONO}; font-size: 8pt; color: {c['texto3']}; }}

QPlainTextEdit#Console {{
    background: {c['console']}; color: {c['console_texto']}; border: 1px solid {c['linha_forte']};
    border-radius: 3px; font-family: {MONO}; font-size: 9pt; padding: 4px;
    selection-background-color: #1f4f4a;
}}

QTableWidget {{ background: {c['painel']}; border: 1px solid {c['linha']}; border-radius: 3px; gridline-color: {c['linha']};
                selection-background-color: {c['destaque_suave']}; selection-color: {c['texto']}; }}
QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {c['linha']}; }}
QHeaderView::section {{ background: {c['painel2']}; color: {c['texto2']}; font-size: 8pt; font-weight: 600; letter-spacing: 1px;
                        border: none; border-bottom: 1px solid {c['linha']}; padding: 0 10px; min-height: 28px; }}

QStatusBar {{ background: {c['painel2']}; border-top: 1px solid {c['linha']}; color: {c['texto2']}; }}
QStatusBar::item {{ border: none; }}
#StatusBotao {{ border: none; border-radius: 0; background: transparent; color: {c['texto2']}; font-size: 8.5pt;
                padding: 0 10px; min-height: 20px; }}
#TituloBotao {{ border: 1px solid transparent; border-radius: 4px; background: transparent; color: {c['texto2']};
    padding: 4px 10px; }}
#TituloBotao:hover {{ background: {c['destaque_suave']}; color: {c['destaque']}; }}
#TituloBotao[admin="true"] {{ color: {c['destaque']}; border-color: {c['destaque']}; }}
#StatusBotao:hover {{ background: {c['destaque_suave']}; color: {c['destaque']}; }}
#StatusItem {{ color: {c['texto2']}; font-size: 8.5pt; padding: 0 10px; border-right: 1px solid {c['linha']}; }}
#StatusMono {{ color: {c['texto2']}; font-family: {MONO}; font-size: 8pt; padding: 0 10px; border-right: 1px solid {c['linha']}; }}

QLabel[link="true"]:hover {{ color: {c['destaque']}; text-decoration: underline; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['linha_forte']}; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {c['linha_forte']}; border-radius: 4px; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {c['linha']}; }}
QMessageBox {{ background: {c['painel']}; }}
"""
