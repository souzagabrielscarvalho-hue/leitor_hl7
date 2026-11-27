"""
Janela do painel (Qt Widgets): equipamentos à esquerda, detalhes à direita,
barra de status embaixo. Tudo nativo, sem navegador.

Consultas que podem demorar (estado com /health, iniciar, parar, verificar
portas) rodam numa thread à parte; o resultado volta para a janela por sinal.

Configuração só com administrador: sem login os campos ficam bloqueados e
remover/trocar porta pedem a senha (o núcleo recusa de qualquer forma).
"""

import logging
import os
import re
import subprocess
import sys
import threading
import traceback
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QActionGroup, QColor, QDesktopServices, QIcon, QIntValidator, QPixmap, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QInputDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from shared.painel import tema
from shared.painel.nucleo import Painel

INTERVALO_ESTADO_MS = 3000
INTERVALO_LOG_MS = 3000

ROTULO_ESTADO = {
    "rodando": "Em execução",
    "iniciando": "Iniciando",
    "sem_resposta": "Sem resposta",
    "parada": "Parada",
}


def classe_estado(m: Dict[str, Any]) -> str:
    """ok / aviso / erro / neutro, para LED, etiqueta e cores."""
    if m.get("estado") == "rodando":
        return "aviso" if (m.get("health") or {}).get("port_open") is False else "ok"
    if m.get("estado") in ("iniciando", "sem_resposta"):
        return "aviso"
    if m.get("codigo_saida") not in (None, 0):
        return "erro"
    return "neutro"


def definir_prop(widget: QWidget, nome: str, valor: Any) -> None:
    """Muda uma propriedade usada no QSS e reaplica o estilo do widget."""
    if widget.property(nome) == valor:
        return
    widget.setProperty(nome, valor)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def rotulo(texto: str = "", nome: str = "", selecionavel: bool = False) -> QLabel:
    lbl = QLabel(texto)
    if nome:
        lbl.setObjectName(nome)
    if selecionavel:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def abrir_no_explorador(caminho: str) -> bool:
    """
    Abre a pasta no Explorer. Se o caminho for um arquivo, abre a pasta dele
    com o arquivo selecionado; se ainda não existir (ex.: log de máquina que
    nunca rodou), abre a pasta existente mais próxima.
    """
    if not caminho:
        return False
    caminho = os.path.normpath(caminho)
    try:
        if os.path.isfile(caminho) and sys.platform.startswith("win"):
            subprocess.Popen(["explorer", f"/select,{caminho}"])
            logging.info(f"✓ Pasta aberta no Explorer: {caminho}")
            return True
        pasta = caminho if os.path.isdir(caminho) else os.path.dirname(caminho)
        while pasta and not os.path.isdir(pasta) and os.path.dirname(pasta) != pasta:
            pasta = os.path.dirname(pasta)
        if pasta and QDesktopServices.openUrl(QUrl.fromLocalFile(pasta)):
            logging.info(f"✓ Pasta aberta: {pasta}")
            return True
    except Exception as e:
        logging.error(f"✗ Não foi possível abrir {caminho}: {e}")
        return False
    logging.error(f"✗ Não foi possível abrir {caminho}.")
    return False


class Link(QLabel):
    """Caminho clicável: abre a pasta no Explorer."""

    def __init__(self, nome: str) -> None:
        super().__init__()
        self.setObjectName(nome)
        self.setProperty("link", True)
        self.alvo = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Clique para abrir a pasta")

    def definir(self, texto: str, alvo: str) -> None:
        self.setText(texto)
        self.alvo = alvo
        self.setToolTip(f"Abrir no Explorer: {alvo}")

    def mousePressEvent(self, evento) -> None:  # noqa: N802 (nome do Qt)
        if evento.button() == Qt.MouseButton.LeftButton:
            abrir_no_explorador(self.alvo)
        super().mousePressEvent(evento)


def tempo(segundos: Any) -> str:
    if segundos is None or not isinstance(segundos, (int, float)) or segundos < 0:
        return "—"
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos} s"
    if segundos < 3600:
        return f"{segundos // 60} min"
    horas = segundos // 3600
    return f"{horas} h {(segundos % 3600) // 60} min" if horas < 48 else f"{horas // 24} d"


# ═══════════════════════════════════════════════════════════
# TAREFAS EM SEGUNDO PLANO
# ═══════════════════════════════════════════════════════════

class Ponte(QObject):
    """Leva o resultado da thread de trabalho para a thread da janela."""
    pronto = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.pronto.connect(self._entregar)

    @staticmethod
    def _entregar(callback: Callable[[Any], None], resultado: Any) -> None:
        try:
            callback(resultado)
        except RuntimeError as e:
            # A janela fechou antes da tarefa terminar: não há onde mostrar.
            if "already deleted" not in str(e):
                raise

    def executar(self, funcao: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        def alvo() -> None:
            try:
                resultado = funcao()
            except Exception as e:
                logging.error(f"✗ Erro no painel: {e}\n{traceback.format_exc()}")
                resultado = {"ok": False, "mensagem": str(e), "_erro": True}
            self.pronto.emit(callback, resultado)
        threading.Thread(target=alvo, daemon=True, name="painel-tarefa").start()


# ═══════════════════════════════════════════════════════════
# PEÇAS
# ═══════════════════════════════════════════════════════════

class Faixa(QFrame):
    """Aviso em faixa (borda colorida à esquerda), com botão opcional."""

    def __init__(self, c: Dict[str, str]) -> None:
        super().__init__()
        self.c = c
        self.setObjectName("Faixa")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(8)
        self.ico = QLabel()
        self.ico.setFixedSize(16, 16)
        self.texto = rotulo(nome="FaixaTexto", selecionavel=True)
        self.texto.setWordWrap(True)
        self.botao = QPushButton()
        self.botao.hide()
        self._ao_clicar: Optional[Callable] = None
        lay.addWidget(self.ico, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.texto, 1)
        lay.addWidget(self.botao, 0)
        self.hide()

    def mostrar(self, estado: str, texto: str, botao: str = "", ao_clicar: Optional[Callable] = None) -> None:
        cor = {"ok": self.c["ok"], "aviso": self.c["aviso"], "erro": self.c["erro"]}.get(estado, self.c["texto2"])
        nome = {"ok": "ok", "erro": "alerta", "aviso": "alerta"}.get(estado, "info")
        self.ico.setPixmap(tema.icone(nome, cor).pixmap(16, 16))
        definir_prop(self, "estado", estado)
        self.texto.setText(texto)
        if self._ao_clicar is not None:
            self.botao.clicked.disconnect(self._ao_clicar)
            self._ao_clicar = None
        if botao and ao_clicar:
            self._ao_clicar = ao_clicar
            self.botao.setText(botao)
            self.botao.clicked.connect(ao_clicar)
            self.botao.show()
        else:
            self.botao.hide()
        self.show()


class Secao(QFrame):
    """Quadro com título em caixa alta, como os grupos de um software de configuração."""

    def __init__(self, titulo: str) -> None:
        super().__init__()
        self.setObjectName("Secao")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        t = rotulo(titulo.upper(), "SecaoTitulo")
        lay.addWidget(t)
        self.corpo = QWidget()
        lay.addWidget(self.corpo)


class Leitura(QFrame):
    def __init__(self, titulo: str, ultima: bool = False) -> None:
        super().__init__()
        self.setObjectName("LeituraUltima" if ultima else "Leitura")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        lay.addWidget(rotulo(titulo.upper(), "LeituraRot"))
        self.valor = rotulo("—", "LeituraVal")
        self.sub = rotulo("", "LeituraSub")
        lay.addWidget(self.valor)
        lay.addWidget(self.sub)

    def definir(self, valor: Any, sub: str = "", estado: str = "") -> None:
        self.valor.setText(str(valor))
        self.sub.setText(sub)
        definir_prop(self.valor, "estado", estado)


class RealceLog(QSyntaxHighlighter):
    """Cores do console: ✗/ERROR, ⚠/WARNING, ✓ e a data/hora apagada."""

    PADRAO = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+) (\[\w+\])")

    def __init__(self, documento, c: Dict[str, str]) -> None:
        super().__init__(documento)
        self.formatos = {}
        for nome, cor in (("fraco", c["console_fraco"]), ("ok", c["console_ok"]),
                          ("aviso", c["console_aviso"]), ("erro", c["console_erro"])):
            f = QTextCharFormat()
            f.setForeground(QColor(cor))
            self.formatos[nome] = f

    def highlightBlock(self, texto: str) -> None:
        if "✗" in texto or "[ERROR]" in texto or "[CRITICAL]" in texto:
            self.setFormat(0, len(texto), self.formatos["erro"])
        elif "⚠" in texto or "[WARNING]" in texto:
            self.setFormat(0, len(texto), self.formatos["aviso"])
        elif "✓" in texto:
            self.setFormat(0, len(texto), self.formatos["ok"])
        m = self.PADRAO.match(texto)
        if m:
            self.setFormat(0, m.end(), self.formatos["fraco"])


# ═══════════════════════════════════════════════════════════
# NAVEGADOR DE EQUIPAMENTOS
# ═══════════════════════════════════════════════════════════

class ItemMaquina(QWidget):
    def __init__(self, c: Dict[str, str]) -> None:
        super().__init__()
        self.c = c
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 6, 12, 6)
        lay.setSpacing(10)
        self.led = QLabel()
        self.led.setFixedSize(10, 10)
        textos = QVBoxLayout()
        textos.setSpacing(0)
        self.nome = rotulo(nome="NavNome")
        self.sub = rotulo(nome="NavSub")
        textos.addWidget(self.nome)
        textos.addWidget(self.sub)
        lado = QVBoxLayout()
        lado.setSpacing(0)
        self.estado = rotulo(nome="NavEstado")
        self.estado.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.pend = rotulo(nome="NavPend")
        self.pend.setAlignment(Qt.AlignmentFlag.AlignRight)
        lado.addWidget(self.estado)
        lado.addWidget(self.pend)
        lay.addWidget(self.led)
        lay.addLayout(textos, 1)
        lay.addLayout(lado)
        for w in (self, self.nome, self.sub, self.estado, self.pend, self.led):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def definir(self, m: Dict[str, Any]) -> None:
        classe = classe_estado(m)
        cor = {"ok": self.c["ok"], "aviso": self.c["aviso"], "erro": self.c["erro"]}.get(classe, self.c["ocioso"])
        self.led.setPixmap(tema.led(cor))
        self.nome.setText(m["titulo"])
        self.sub.setText(m["conexao"] if m["configurada"] else "sem configuração")
        self.estado.setText(ROTULO_ESTADO.get(m["estado"], m["estado"]))
        definir_prop(self.estado, "estado", classe)
        self.pend.setText(f"{m['pendentes']} pend." if m["pendentes"] else "")
        self.pend.setVisible(bool(m["pendentes"]))


class ItemFerramenta(QWidget):
    def __init__(self, c: Dict[str, str], icone: str, texto: str) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 8, 12, 8)
        lay.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(tema.icone(icone, c["texto2"]).pixmap(16, 16))
        lay.addWidget(ico)
        lay.addWidget(rotulo(texto, "NavNome"), 1)
        for w in (self, ico):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


# ═══════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════════════════════

class FormConfig(QWidget):
    """Grade de propriedades do config_<maquina>.json, montada pelos campos do núcleo."""

    salvo = Signal()
    adicionado = Signal(str)

    def __init__(self, painel: Painel, ponte: Ponte, c: Dict[str, str], adicionar: bool = False) -> None:
        super().__init__()
        self.painel, self.ponte, self.c = painel, ponte, c
        # Trocado pela janela: pede a senha e depois roda a ação.
        self.exigir_admin: Callable[[Callable[[], None]], None] = lambda acao: acao()
        self.admin = painel.admin()
        self.erro_leitura = False
        # adicionar=True: usado em "Adicionar equipamento" (cria o config ou usa o existente).
        self.modo_adicionar = adicionar
        self.pasta: Optional[str] = None
        self.existente = False
        self.maquina_id = ""
        self.original: Dict[str, Any] = {}
        self.editores: Dict[str, Dict[str, Any]] = {}
        self.portas: List[Dict[str, Any]] = []

        externo = QVBoxLayout(self)
        externo.setContentsMargins(0, 0, 0, 0)
        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QFrame.Shape.NoFrame)
        interno = QWidget()
        interno.setObjectName("Fundo")
        self.lay = QVBoxLayout(interno)
        self.lay.setContentsMargins(18, 14, 18, 14)
        self.lay.setSpacing(12)
        rolagem.setWidget(interno)
        externo.addWidget(rolagem, 1)

        self.bloqueio = Faixa(c)
        self.lay.addWidget(self.bloqueio)
        self.aviso = Faixa(c)
        self.lay.addWidget(self.aviso)
        self.secoes = QVBoxLayout()
        self.secoes.setSpacing(12)
        self.lay.addLayout(self.secoes)
        self.retorno = Faixa(c)
        self.lay.addWidget(self.retorno)
        self.lay.addStretch(1)

        # Barra de ações fixa embaixo
        barra = QFrame()
        barra.setObjectName("BarraAcoes")
        bl = QHBoxLayout(barra)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(8)
        pasta = QLabel()
        pasta.setPixmap(tema.icone("pasta", c["texto3"]).pixmap(14, 14))
        self.arquivo = Link("Arquivo")
        self.alterado = rotulo("Alterações não salvas", "Alterado")
        self.alterado.hide()
        self.bt_descartar = QPushButton(tema.icone("desfazer", c["texto"]), "Descartar")
        self.bt_salvar = QPushButton(tema.icone("salvar", c["destaque_texto"]), "Salvar configuração")
        definir_prop(self.bt_salvar, "papel", "primario")
        self.bt_descartar.clicked.connect(self.carregar)
        self.bt_salvar.clicked.connect(self.salvar)
        if adicionar:
            self.bt_descartar.hide()
            self.bt_salvar.setIcon(tema.icone("mais", c["destaque_texto"]))
        bl.addWidget(pasta)
        bl.addWidget(self.arquivo, 1)
        bl.addWidget(self.alterado)
        bl.addWidget(self.bt_descartar)
        bl.addWidget(self.bt_salvar)
        barra_area = QWidget()
        bal = QVBoxLayout(barra_area)
        bal.setContentsMargins(18, 0, 18, 14)
        bal.addWidget(barra)
        externo.addWidget(barra_area)

    # ── montagem ────────────────────────────────────────────

    def definir_maquina(self, maquina_id: str, pasta: Optional[str] = None) -> None:
        self.maquina_id = maquina_id
        self.pasta = pasta
        self.carregar()

    def _limpar(self) -> None:
        while self.secoes.count():
            item = self.secoes.takeAt(0)
            w = item.widget()
            if w:
                # Esconde e solta já: o deleteLater só apaga depois, e a seção
                # antiga apareceria por baixo da nova.
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.editores = {}

    def carregar(self) -> None:
        self.retorno.hide()
        dados = self.painel.ler_config(self.maquina_id, self.pasta)
        self.existente = bool(dados["existe"])
        try:
            self.portas = self.painel.listar_portas(testar=False)
        except Exception as e:
            logging.error(f"✗ Portas seriais indisponíveis: {e}")
            self.portas = []
        self._limpar()

        if dados.get("erro_leitura"):
            self.aviso.mostrar("erro", f"O config atual não pôde ser lido: {dados['erro_leitura']}")
        elif self.modo_adicionar and dados["existe"]:
            self.aviso.mostrar("", f"Já existe <b>config_{self.maquina_id}.json</b> nesta pasta. Ele será usado como "
                                   "está, sem nenhuma alteração; os valores abaixo são os dele.")
        elif self.modo_adicionar:
            self.aviso.mostrar("", "Não há config desta máquina nesta pasta. Preencha os parâmetros: o arquivo será "
                                   "criado ao adicionar.")
        elif not dados["existe"]:
            self.aviso.mostrar("aviso", "Equipamento sem configuração neste computador. Revise os parâmetros e salve para criar o arquivo.")
        else:
            self.aviso.hide()

        grupos = [("maquina", "Conexão com o equipamento"), ("apoio", "Integração com o lab-apoio")]
        for grupo, titulo in grupos:
            campos = [c for c in dados["campos"] if c["grupo"] == grupo]
            if not campos:
                continue
            secao = Secao(titulo)
            grade = QGridLayout(secao.corpo)
            grade.setContentsMargins(0, 0, 0, 0)
            grade.setSpacing(0)
            grade.setColumnMinimumWidth(0, 240)
            grade.setColumnStretch(1, 1)
            for linha, campo in enumerate(campos):
                self._linha(grade, linha, campo, dados["valores"].get(campo["chave"]))
            self.secoes.addWidget(secao)

        self.arquivo.definir(dados["caminho"], dados["caminho"])
        self._aplicar_modo()
        erros = dados["erros"] if dados["existe"] else {}
        self._mostrar_erros(erros)
        self.original = self.valores()
        self._atualizar_alterado()
        self.erro_leitura = bool(dados.get("erro_leitura"))
        if self.modo_adicionar:
            self.bt_salvar.setText("Adicionar usando este arquivo" if self.existente else "Adicionar equipamento")
            self.alterado.hide()
        self._aplicar_acesso()

    # ── acesso ──────────────────────────────────────────────

    def definir_acesso(self, admin: bool) -> None:
        if admin != self.admin:
            self.admin = admin
            self._aplicar_acesso()

    def _aplicar_acesso(self) -> None:
        # Arquivo existente ao adicionar: só leitura (nunca é sobrescrito).
        livre = self.admin and not (self.modo_adicionar and self.existente)
        for e in self.editores.values():
            for w in e["widgets"]:
                w.setEnabled(livre)
        self.bt_salvar.setEnabled(self.admin and not (self.modo_adicionar and self.erro_leitura))
        if self.admin:
            self.bloqueio.hide()
        else:
            self.bloqueio.mostrar(
                "", "<b>Configuração protegida.</b> Para alterar, entre como administrador.",
                "Entrar…", lambda: self.exigir_admin(lambda: None),
            )

    def _linha(self, grade: QGridLayout, linha: int, campo: Dict[str, Any], valor: Any) -> None:
        chave, tipo = campo["chave"], campo["tipo"]
        rot = QFrame()
        rot.setObjectName("Rotulo")
        rl = QVBoxLayout(rot)
        rl.setContentsMargins(12, 7, 12, 7)
        rl.setSpacing(0)
        rl.addStretch(1)
        rl.addWidget(rotulo(campo["rotulo"], "RotuloNome"))
        rl.addWidget(rotulo(chave, "RotuloChave"))
        rl.addStretch(1)

        val = QFrame()
        val.setObjectName("Valor")
        vl = QVBoxLayout(val)
        vl.setContentsMargins(12, 7, 12, 7)
        vl.setSpacing(4)

        info = None
        if tipo == "opcoes":
            ed = QComboBox()
            for v, texto in campo["opcoes"]:
                ed.addItem(texto, v)
            i = ed.findData(valor)
            ed.setCurrentIndex(i if i >= 0 else 0)
            ed.setMaximumWidth(240)
            ed.currentIndexChanged.connect(self._mudou)
            if chave == "modo":
                ed.currentIndexChanged.connect(self._aplicar_modo)
            vl.addWidget(ed)
        elif tipo == "porta":
            ed = QComboBox()
            ed.setEditable(True)
            ed.setProperty("mono", True)
            ed.setMaximumWidth(360)
            for p in self.portas:
                ed.addItem(f"{p['porta']} — {p['descricao']}" if p["descricao"] else p["porta"], p["porta"])
            ed.setEditText(str(valor or ""))
            ed.currentTextChanged.connect(self._mudou)
            info = rotulo(nome="InfoPorta")
            info.setWordWrap(True)
            vl.addWidget(ed)
            vl.addWidget(info)
        elif tipo == "segredo":
            caixa = QWidget()
            hl = QHBoxLayout(caixa)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            ed = QLineEdit(str(valor or ""))
            ed.setEchoMode(QLineEdit.EchoMode.Password)
            ed.setProperty("mono", True)
            ver = QPushButton(tema.icone("olho", self.c["texto2"]), "")
            ver.setToolTip("Mostrar/ocultar")
            ver.setCheckable(True)
            ver.toggled.connect(lambda on, e=ed: e.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
            ed.textChanged.connect(self._mudou)
            hl.addWidget(ed, 1)
            hl.addWidget(ver)
            vl.addWidget(caixa)
        else:
            ed = QLineEdit("" if valor is None else str(valor))
            ed.setCursorPosition(0)  # URL longa: mostra o começo
            if tipo == "numero":
                ed.setValidator(QIntValidator(0, 999999))
                ed.setMaximumWidth(140)
            if tipo in ("url", "numero") or "id" in chave:
                ed.setProperty("mono", True)
            ed.textChanged.connect(self._mudou)
            vl.addWidget(ed)

        erro = rotulo(nome="Erro")
        erro.setWordWrap(True)
        ajuda = rotulo(campo.get("ajuda", ""), "Ajuda")
        ajuda.setWordWrap(True)
        # Primeiro no layout, depois visível: um widget sem pai que recebe
        # show/setVisible(True) vira janela própria e pisca na tela.
        vl.addWidget(erro)
        vl.addWidget(ajuda)
        erro.hide()
        ajuda.setVisible(bool(campo.get("ajuda")))

        grade.addWidget(rot, linha, 0)
        grade.addWidget(val, linha, 1)
        self.editores[chave] = {
            "campo": campo, "editor": ed, "erro": erro, "ajuda": ajuda, "info": info, "widgets": (rot, val),
        }

    # ── valores ─────────────────────────────────────────────

    def valores(self) -> Dict[str, Any]:
        v: Dict[str, Any] = {}
        for chave, e in self.editores.items():
            ed, tipo = e["editor"], e["campo"]["tipo"]
            if tipo == "opcoes":
                v[chave] = ed.currentData()
            elif tipo == "porta":
                v[chave] = ed.currentText().split(" — ")[0].strip()
            elif tipo == "numero":
                texto = ed.text().strip()
                v[chave] = int(texto) if texto.isdigit() else texto
            else:
                v[chave] = ed.text()
        return v

    def _aplicar_modo(self) -> None:
        modo = self.editores["modo"]["editor"].currentData() if "modo" in self.editores else "serial"
        for e in self.editores.values():
            quando = e["campo"].get("quando")
            visivel = not quando or quando == modo
            for w in e["widgets"]:
                w.setVisible(visivel)

    def _mudou(self, *_: Any) -> None:
        self._atualizar_alterado()
        self._atualizar_info_porta()

    def _atualizar_alterado(self) -> None:
        alterado = self.valores() != self.original and not self.modo_adicionar
        self.alterado.setVisible(alterado)
        self.bt_descartar.setEnabled(alterado and self.admin)
        self._atualizar_info_porta()

    def _atualizar_info_porta(self) -> None:
        e = self.editores.get("com_port")
        if not e or not e["info"]:
            return
        nome = e["editor"].currentText().split(" — ")[0].strip()
        p = next((x for x in self.portas if x["porta"] == nome), None)
        partes = []
        if p:
            partes.append(f"<span style='color:{self.c['ok']}'>●</span> {p['descricao'] or 'Porta serial'}")
            if p["serie"]:
                partes.append(f"série {p['serie']}")
            outras = [m for m in p["maquinas"] if m != self.maquina_id]
            if outras:
                partes.append(f"<span style='color:{self.c['aviso']}'>também configurada em {', '.join(outras)}</span>")
        elif nome:
            partes.append(f"<span style='color:{self.c['aviso']}'>● {nome} não está presente neste computador.</span>")
        detectadas = ", ".join(p["porta"] for p in self.portas) if self.portas else "nenhuma"
        partes.append(f"<span style='color:{self.c['texto3']}'>Detectadas: {detectadas}</span>")
        e["info"].setText(" · ".join(partes))

    def _mostrar_erros(self, erros: Dict[str, str]) -> None:
        for chave, e in self.editores.items():
            msg = erros.get(chave, "")
            definir_prop(e["editor"], "invalido", bool(msg))
            e["erro"].setText(f"⚠ {msg}" if msg else "")
            e["erro"].setVisible(bool(msg))
            e["ajuda"].setVisible(not msg and bool(e["campo"].get("ajuda")))

    def salvar(self) -> None:
        valores = self.valores()
        self.bt_salvar.setEnabled(False)

        def pronto(r: Dict[str, Any]) -> None:
            self.bt_salvar.setEnabled(True)
            if r.get("_erro"):
                self.retorno.mostrar("erro", r["mensagem"])
                return
            self._mostrar_erros(r.get("erros", {}))
            if r["ok"]:
                self.original = valores
                self._atualizar_alterado()
                self.aviso.hide()
                extra = " A máquina está em execução: reinicie para aplicar." if r.get("rodando") else ""
                self.retorno.mostrar("ok", "Configuração salva." + extra)
                self.salvo.emit()
            else:
                if r.get("login"):
                    self.definir_acesso(False)
                self.retorno.mostrar("erro", r["erros"].get("_arquivo") or "Há campos com erro. Corrija os itens marcados.")

        if self.modo_adicionar:
            self._adicionar(valores)
            return
        self.ponte.executar(lambda: self.painel.salvar_config(self.maquina_id, valores), pronto)

    def _adicionar(self, valores: Dict[str, Any]) -> None:
        mid, pasta = self.maquina_id, self.pasta
        usar = None if self.existente else valores

        def pronto(r: Dict[str, Any]) -> None:
            self.bt_salvar.setEnabled(True)
            if r.get("_erro"):
                self.retorno.mostrar("erro", r["mensagem"])
                return
            self._mostrar_erros(r.get("erros", {}))
            if r["ok"]:
                self.adicionado.emit(mid)
            else:
                if r.get("login"):
                    self.definir_acesso(False)
                self.retorno.mostrar("erro", r["erros"].get("_arquivo") or "Há campos com erro. Corrija os itens marcados.")

        self.ponte.executar(lambda: self.painel.adicionar(mid, pasta or "", usar), pronto)


# ═══════════════════════════════════════════════════════════
# ADICIONAR EQUIPAMENTO
# ═══════════════════════════════════════════════════════════

class PaginaAdicionar(QWidget):
    """Escolhe o modelo e a pasta do config; depois o formulário (ou o config que já existe)."""

    adicionado = Signal(str)

    def __init__(self, painel: Painel, ponte: Ponte, c: Dict[str, str]) -> None:
        super().__init__()
        self.painel, self.ponte, self.c = painel, ponte, c
        self.disponiveis: List[Dict[str, Any]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        cabeca = QFrame()
        cabeca.setObjectName("Cabeca")
        cl = QVBoxLayout(cabeca)
        cl.setContentsMargins(18, 12, 18, 12)
        cl.setSpacing(3)
        cl.addWidget(rotulo("Adicionar equipamento", "CabecaNome"))
        cl.addWidget(rotulo("Escolha o modelo, a pasta onde fica o arquivo de configuração e os parâmetros.", "CabecaInfo"))
        lay.addWidget(cabeca)

        escolha = QWidget()
        escolha.setObjectName("Fundo")
        el = QVBoxLayout(escolha)
        el.setContentsMargins(18, 14, 18, 0)
        el.setSpacing(12)
        secao = Secao("Modelo e pasta")
        grade = QGridLayout(secao.corpo)
        grade.setContentsMargins(0, 0, 0, 0)
        grade.setSpacing(0)
        grade.setColumnMinimumWidth(0, 240)
        grade.setColumnStretch(1, 1)

        self.modelo = QComboBox()
        self.modelo.setMinimumWidth(360)
        self.modelo.currentIndexChanged.connect(self._mudou)
        self.descricao = rotulo(nome="Ajuda")
        self.descricao.setWordWrap(True)
        self._linha_grade(grade, 0, "Modelo", "", [self.modelo, self.descricao])

        caixa = QWidget()
        hl = QHBoxLayout(caixa)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.pasta = QLineEdit(painel.pasta_config)
        self.pasta.setProperty("mono", True)
        self.pasta.editingFinished.connect(self._mudou)
        procurar = QPushButton(tema.icone("pasta", c["texto"], 14), "Procurar…")
        procurar.clicked.connect(self._procurar)
        hl.addWidget(self.pasta, 1)
        hl.addWidget(procurar)
        ajuda = rotulo("Onde fica o config_<modelo>.json. Se já houver um arquivo da máquina nesta pasta, ele é usado "
                       "como está.", "Ajuda")
        ajuda.setWordWrap(True)
        self._linha_grade(grade, 1, "Pasta do arquivo de configuração", "", [caixa, ajuda])
        el.addWidget(secao)
        self.vazio = Faixa(c)
        el.addWidget(self.vazio)
        lay.addWidget(escolha)

        self.form = FormConfig(painel, ponte, c, adicionar=True)
        self.form.adicionado.connect(self.adicionado.emit)
        lay.addWidget(self.form, 1)

    def _linha_grade(self, grade: QGridLayout, linha: int, titulo: str, chave: str, widgets: List[QWidget]) -> None:
        rot = QFrame()
        rot.setObjectName("Rotulo")
        rl = QVBoxLayout(rot)
        rl.setContentsMargins(12, 7, 12, 7)
        rl.addStretch(1)
        rl.addWidget(rotulo(titulo, "RotuloNome"))
        rl.addStretch(1)
        val = QFrame()
        val.setObjectName("Valor")
        vl = QVBoxLayout(val)
        vl.setContentsMargins(12, 7, 12, 7)
        vl.setSpacing(4)
        for w in widgets:
            vl.addWidget(w)
        grade.addWidget(rot, linha, 0)
        grade.addWidget(val, linha, 1)

    def definir_disponiveis(self, disponiveis: List[Dict[str, Any]]) -> None:
        """Modelos que ainda não estão na lista (cada modelo uma vez por computador)."""
        ids = [d["id"] for d in disponiveis]
        if ids == [d["id"] for d in self.disponiveis]:
            return
        atual = self.modelo.currentData()
        self.disponiveis = disponiveis
        self.modelo.blockSignals(True)
        self.modelo.clear()
        for d in disponiveis:
            self.modelo.addItem(f"{d['titulo']}  ·  {d['tipo']}", d["id"])
        i = self.modelo.findData(atual)
        self.modelo.setCurrentIndex(i if i >= 0 else 0)
        self.modelo.blockSignals(False)
        self._mudou()

    def preparar(self) -> None:
        """Ao abrir a página: volta para a pasta padrão e recarrega o formulário."""
        self.pasta.setText(self.painel.pasta_config)
        self._mudou()

    def _procurar(self) -> None:
        escolhida = QFileDialog.getExistingDirectory(self, "Pasta do arquivo de configuração", self.pasta.text())
        if escolhida:
            self.pasta.setText(os.path.normpath(escolhida))
            self._mudou()

    def _mudou(self, *_: Any) -> None:
        mid = self.modelo.currentData()
        tem = bool(mid)
        self.form.setVisible(tem)
        if not tem:
            self.descricao.setText("")
            self.vazio.mostrar("", "Todos os modelos já estão na lista. Para trocar a pasta de um deles, remova-o da "
                                   "lista e adicione de novo.")
            return
        self.vazio.hide()
        d = next(x for x in self.disponiveis if x["id"] == mid)
        self.descricao.setText(f"{d['protocolo']} · {d['sentido']}")
        self.form.definir_maquina(mid, self.pasta.text().strip() or self.painel.pasta_config)


# ═══════════════════════════════════════════════════════════
# LOG
# ═══════════════════════════════════════════════════════════

class VisaoLog(QWidget):
    def __init__(self, painel: Painel, c: Dict[str, str]) -> None:
        super().__init__()
        self.painel, self.c = painel, c
        self.maquina_id = ""
        self.linhas: List[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)

        barra = QHBoxLayout()
        barra.setSpacing(16)
        self.filtro = QLineEdit()
        self.filtro.setPlaceholderText("Filtrar por etiqueta, erro, texto…")
        self.filtro.addAction(tema.icone("verificar", c["texto3"]), QLineEdit.ActionPosition.LeadingPosition)
        self.filtro.setMaximumWidth(340)
        self.so_falhas = QCheckBox("Somente falhas e avisos")
        self.seguir = QCheckBox("Acompanhar")
        self.seguir.setChecked(True)
        self.contagem = rotulo(nome="Ajuda")
        barra.addWidget(self.filtro, 1)
        barra.addWidget(self.so_falhas)
        barra.addWidget(self.seguir)
        barra.addStretch(1)
        barra.addWidget(self.contagem)
        lay.addLayout(barra)

        self.console = QPlainTextEdit()
        self.console.setObjectName("Console")
        self.console.setReadOnly(True)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.console.setMaximumBlockCount(5000)
        self.realce = RealceLog(self.console.document(), c)
        lay.addWidget(self.console, 1)

        rodape = QHBoxLayout()
        ico = QLabel()
        ico.setPixmap(tema.icone("pasta", c["texto3"]).pixmap(14, 14))
        self.caminho = Link("Arquivo")
        rodape.addWidget(ico)
        rodape.addWidget(self.caminho, 1)
        lay.addLayout(rodape)

        self.saida = QPlainTextEdit()
        self.saida.setObjectName("Console")
        self.saida.setReadOnly(True)
        self.saida.setMaximumHeight(110)
        self.saida.hide()
        lay.addWidget(self.saida)

        self.filtro.textChanged.connect(self._render)
        self.so_falhas.toggled.connect(self._render)

    def definir_maquina(self, maquina_id: str) -> None:
        self.maquina_id = maquina_id
        self.linhas = []
        self.carregar()

    def carregar(self) -> None:
        if not self.maquina_id:
            return
        r = self.painel.log(self.maquina_id, 1000)
        self.caminho.definir(r["caminho"], r["caminho"])
        if r["saida"]:
            self.saida.setPlainText("Saída do processo (erros antes do log existir):\n" + "\n".join(r["saida"]))
        self.saida.setVisible(bool(r["saida"]))
        if r["linhas"] != self.linhas:
            self.linhas = r["linhas"]
            self._render()

    def _render(self) -> None:
        termo = self.filtro.text().strip().lower()
        so_falhas = self.so_falhas.isChecked()
        visiveis = [
            l for l in self.linhas
            if (not so_falhas or re.search(r"✗|⚠|\[(ERROR|WARNING|CRITICAL)\]", l))
            and (not termo or termo in l.lower())
        ]
        barra = self.console.verticalScrollBar()
        posicao = barra.value()
        self.console.setPlainText("\n".join(visiveis) if self.linhas else
                                  "Sem registros. O log é criado quando a máquina é iniciada.")
        if self.seguir.isChecked():
            barra.setValue(barra.maximum())
        else:
            barra.setValue(posicao)
        self.contagem.setText(f"{len(visiveis)} / {len(self.linhas)} linhas")


# ═══════════════════════════════════════════════════════════
# PÁGINA DA MÁQUINA
# ═══════════════════════════════════════════════════════════

class PaginaMaquina(QWidget):
    acao_concluida = Signal()
    removida = Signal(str)

    def __init__(self, painel: Painel, ponte: Ponte, c: Dict[str, str]) -> None:
        super().__init__()
        self.painel, self.ponte, self.c = painel, ponte, c
        self.m: Dict[str, Any] = {}
        self.ocupado = False
        self.exigir_admin: Callable[[Callable[[], None]], None] = lambda acao: acao()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Cabeçalho do equipamento
        cabeca = QFrame()
        cabeca.setObjectName("Cabeca")
        cl = QHBoxLayout(cabeca)
        cl.setContentsMargins(18, 12, 18, 12)
        ident = QVBoxLayout()
        ident.setSpacing(3)
        l1 = QHBoxLayout()
        l1.setSpacing(10)
        self.nome = rotulo(nome="CabecaNome")
        self.tag = rotulo(nome="Tag")
        l1.addWidget(self.nome)
        l1.addWidget(self.tag)
        self.info = rotulo(nome="CabecaInfo", selecionavel=True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        ident.addLayout(l1)
        ident.addWidget(self.info)
        cl.addLayout(ident, 1)

        self.bt_iniciar = QPushButton(tema.icone("iniciar", c["destaque_texto"], 14), "Iniciar")
        definir_prop(self.bt_iniciar, "papel", "primario")
        self.bt_reiniciar = QPushButton(tema.icone("reiniciar", c["texto"], 14), "Reiniciar")
        self.bt_parar = QPushButton(tema.icone("parar", c["erro"], 14), "Parar")
        definir_prop(self.bt_parar, "papel", "perigo")
        self.bt_remover = QPushButton(tema.icone("remover", c["texto2"], 14), "")
        self.bt_remover.setToolTip("Remover da lista (o arquivo de configuração fica na pasta)")
        self.ck_ao_ligar = QCheckBox("Iniciar ao ligar")
        self.ck_ao_ligar.setToolTip("Com \"Iniciar com o Windows\" ativo (Configurações), esta máquina inicia sozinha "
                                    "quando o computador liga.")
        self.ck_ao_ligar.toggled.connect(self._ao_ligar_mudou)
        l1.addSpacing(6)
        l1.addWidget(self.ck_ao_ligar)
        l1.addStretch(1)
        for b in (self.bt_iniciar, self.bt_reiniciar, self.bt_parar, self.bt_remover):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            cl.addWidget(b)
        self.bt_remover.clicked.connect(lambda: self.exigir_admin(self._remover))
        self.bt_iniciar.clicked.connect(lambda: self._acao("iniciar"))
        self.bt_reiniciar.clicked.connect(lambda: self._acao("reiniciar"))
        self.bt_parar.clicked.connect(lambda: self._acao("parar"))
        lay.addWidget(cabeca)

        # Abas
        self.abas = QTabWidget()
        self.abas.setDocumentMode(True)
        lay.addWidget(self.abas, 1)

        # Faixas ficam no topo da aba de status
        status = QWidget()
        status.setObjectName("Fundo")
        sl = QVBoxLayout(status)
        sl.setContentsMargins(18, 14, 18, 14)
        sl.setSpacing(12)
        self.f_retorno = Faixa(c)
        self.f_mensagem = Faixa(c)
        self.f_sugestao = Faixa(c)
        self.f_motivo = Faixa(c)
        for f in (self.f_retorno, self.f_mensagem, self.f_sugestao, self.f_motivo):
            sl.addWidget(f)

        leituras = QFrame()
        leituras.setObjectName("Leituras")
        ll = QHBoxLayout(leituras)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        self.l_conexao = Leitura("Conexão")
        self.l_enviados = Leitura("Enviados hoje")
        self.l_pendentes = Leitura("Aguardando envio")
        self.l_erros = Leitura("Erros seguidos")
        self.l_tempo = Leitura("Tempo ligado", ultima=True)
        for l in (self.l_conexao, self.l_enviados, self.l_pendentes, self.l_erros, self.l_tempo):
            ll.addWidget(l, 1)
        sl.addWidget(leituras)

        detalhes = Secao("Detalhes da execução")
        dg = QGridLayout(detalhes.corpo)
        dg.setContentsMargins(0, 0, 0, 0)
        dg.setSpacing(0)
        dg.setColumnMinimumWidth(0, 220)
        dg.setColumnStretch(1, 1)
        self.detalhes: Dict[str, QLabel] = {}
        for i, (chave, titulo) in enumerate([
            ("versao", "Versão em execução"), ("webhook", "Envio ao lab-apoio"),
            ("conexao", "Conexão configurada"), ("log", "Arquivo de log"),
        ]):
            r = QFrame()
            r.setObjectName("Rotulo")
            rl = QVBoxLayout(r)
            rl.setContentsMargins(12, 7, 12, 7)
            rl.addWidget(rotulo(titulo, "RotuloNome"))
            v = QFrame()
            v.setObjectName("Valor")
            vl = QVBoxLayout(v)
            vl.setContentsMargins(12, 7, 12, 7)
            valor = rotulo(nome="Mono", selecionavel=True)
            valor.setWordWrap(True)
            vl.addWidget(valor)
            dg.addWidget(r, i, 0)
            dg.addWidget(v, i, 1)
            self.detalhes[chave] = valor
        sl.addWidget(detalhes)
        sl.addStretch(1)

        self.form = FormConfig(painel, ponte, c)
        self.form.salvo.connect(self.acao_concluida.emit)
        self.log = VisaoLog(painel, c)

        self.abas.addTab(status, tema.icone("status", c["texto2"]), "Status")
        self.abas.addTab(self.form, tema.icone("config", c["texto2"]), "Configuração")
        self.abas.addTab(self.log, tema.icone("log", c["texto2"]), "Log")

        self.timer_log = QTimer(self)
        self.timer_log.timeout.connect(self._tick_log)
        self.timer_log.start(INTERVALO_LOG_MS)
        self.abas.currentChanged.connect(lambda _: self._tick_log())

    def definir_acesso(self, admin: bool) -> None:
        self.form.definir_acesso(admin)
        self.ck_ao_ligar.setEnabled(admin)
        dica = "Remover da lista (o arquivo de configuração fica na pasta)"
        self.bt_remover.setToolTip(dica if admin else dica + ". Pede a senha de administrador.")

    def _tick_log(self) -> None:
        if self.abas.currentWidget() is self.log and self.isVisible():
            self.log.carregar()

    def definir(self, m: Dict[str, Any]) -> None:
        nova = m["id"] != self.m.get("id")
        self.m = m
        if nova:
            self.f_retorno.hide()
            self.form.definir_maquina(m["id"])
            self.log.definir_maquina(m["id"])
            self.abas.setCurrentIndex(0 if m["configurada"] else 1)
        self._atualizar()

    def _atualizar(self) -> None:
        m, c = self.m, self.c
        h = m.get("health") or {}
        classe = classe_estado(m)
        rodando = m["estado"] == "rodando"
        aberta = m["estado"] in ("rodando", "iniciando", "sem_resposta")

        self.nome.setText(m["titulo"])
        self.tag.setText(ROTULO_ESTADO.get(m["estado"], m["estado"]).upper())
        definir_prop(self.tag, "estado", classe)
        fraco = f"color:{c['texto3']}; font-size:8pt; letter-spacing:1px"
        partes = [
            f"<span style='{fraco}'>ID</span> <span style='font-family:Consolas,monospace'>{m['id']}</span>",
            f"<span style='{fraco}'>SETOR</span> {m['tipo']}",
            f"<span style='{fraco}'>PROTOCOLO</span> {m['protocolo']}",
            f"<span style='{fraco}'>FLUXO</span> {m['sentido']}",
        ]
        if m.get("pid"):
            partes.append(f"<span style='{fraco}'>PID</span> <span style='font-family:Consolas,monospace'>{m['pid']}</span>")
        self.info.setText("&nbsp;&nbsp;&nbsp;".join(partes))

        self.bt_iniciar.setEnabled(not self.ocupado and not aberta and m["pronta"])
        self.bt_iniciar.setToolTip("" if m["pronta"] else m["motivo"])
        self.bt_reiniciar.setEnabled(not self.ocupado and aberta)
        self.bt_parar.setEnabled(not self.ocupado and aberta)
        self.bt_remover.setEnabled(not self.ocupado and not aberta)
        self.ck_ao_ligar.blockSignals(True)
        self.ck_ao_ligar.setChecked(bool(m.get("ao_ligar", True)))
        self.ck_ao_ligar.blockSignals(False)

        if m.get("mensagem"):
            self.f_mensagem.mostrar("aviso", m["mensagem"])
        else:
            self.f_mensagem.hide()
        s = m.get("sugestao_porta")
        if s:
            self.f_sugestao.mostrar(
                "aviso",
                f"{s['porta_antiga']} não existe mais, mas o adaptador desta máquina "
                f"({s['descricao'] or 'USB'}) está em <b>{s['porta_nova']}</b>.",
                f"Usar {s['porta_nova']}", lambda: self.exigir_admin(self._usar_porta_sugerida),
            )
        else:
            self.f_sugestao.hide()
        if not m["pronta"] and not aberta and m["configurada"]:
            self.f_motivo.mostrar("aviso", m["motivo"])
        else:
            self.f_motivo.hide()

        if not rodando:
            self.l_conexao.definir("—", m["conexao"])
        elif h.get("port_open"):
            self.l_conexao.definir("ONLINE", m["conexao"], "ok")
        else:
            self.l_conexao.definir("AGUARDANDO", m["conexao"], "aviso")
        ultimo = h.get("last_webhook_status", -1)
        self.l_enviados.definir(h.get("files_sent_today", 0) if rodando else "—",
                                f"último HTTP {ultimo if ultimo and ultimo > 0 else '—'}")
        self.l_pendentes.definir(m["pendentes"], "arquivos em gerados/", "aviso" if m["pendentes"] else "")
        erros = h.get("errors_consecutive", 0)
        self.l_erros.definir(erros if rodando else "—", "no envio ao lab-apoio", "erro" if rodando and erros else "")
        self.l_tempo.definir(tempo(h.get("uptime_seconds")) if rodando else "—",
                             f"atividade há {tempo(h.get('last_activity_seconds_ago')) if rodando else '—'}")

        self.detalhes["versao"].setText(h.get("version") or "—")
        self.detalhes["webhook"].setText(h.get("webhook_url") or "—")
        self.detalhes["conexao"].setText(m["conexao"])
        self.detalhes["log"].setText(m["log"])

    def _acao(self, nome: str) -> None:
        m = self.m
        if nome == "parar":
            caixa = QMessageBox(self)
            caixa.setIcon(QMessageBox.Icon.Question)
            caixa.setWindowTitle("Parar máquina")
            caixa.setText(f"Parar a {m['titulo']}?")
            caixa.setInformativeText("Ela deixa de receber resultados até ser iniciada de novo. "
                                     "O que estiver em gerados/ é enviado quando ela abrir.")
            sim = caixa.addButton("Parar", QMessageBox.ButtonRole.DestructiveRole)
            caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
            caixa.exec()
            if caixa.clickedButton() is not sim:
                return
        self.ocupado = True
        self._atualizar()
        funcao = {"iniciar": self.painel.iniciar, "parar": self.painel.parar, "reiniciar": self.painel.reiniciar}[nome]
        textos = {"iniciar": "Máquina iniciada.", "parar": "Máquina parada.", "reiniciar": "Máquina reiniciada."}

        def pronto(r: Dict[str, Any]) -> None:
            self.ocupado = False
            if r.get("ok"):
                self.f_retorno.mostrar("ok", textos[nome])
            else:
                self.f_retorno.mostrar("erro", r.get("mensagem", "Falhou."))
            self.abas.setCurrentIndex(0)
            self.acao_concluida.emit()

        mid = m["id"]
        self.ponte.executar(lambda: funcao(mid), pronto)

    def _ao_ligar_mudou(self, marcado: bool) -> None:
        mid = self.m.get("id")
        if not mid:
            return

        def pronto(r: Dict[str, Any]) -> None:
            if not r.get("ok"):
                self.f_retorno.mostrar("erro", r.get("mensagem", "Falhou."))
                self.ck_ao_ligar.blockSignals(True)
                self.ck_ao_ligar.setChecked(not marcado)
                self.ck_ao_ligar.blockSignals(False)
            self.acao_concluida.emit()

        self.ponte.executar(lambda: self.painel.definir_ao_ligar(mid, marcado), pronto)

    def _remover(self) -> None:
        m = self.m
        caixa = QMessageBox(self)
        caixa.setIcon(QMessageBox.Icon.Question)
        caixa.setWindowTitle("Remover da lista")
        caixa.setText(f"Remover a {m['titulo']} da lista?")
        caixa.setInformativeText(f"O arquivo de configuração não é apagado e continua em:\n{m.get('pasta_config', '')}\n\n"
                                 "Para voltar a usá-lo, adicione a máquina de novo apontando para essa pasta.")
        sim = caixa.addButton("Remover", QMessageBox.ButtonRole.DestructiveRole)
        caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        caixa.exec()
        if caixa.clickedButton() is not sim:
            return
        mid = m["id"]

        def pronto(r: Dict[str, Any]) -> None:
            if r.get("ok"):
                self.removida.emit(mid)
            else:
                self.f_retorno.mostrar("erro", r.get("mensagem", "Falhou."))

        self.ponte.executar(lambda: self.painel.remover(mid), pronto)

    def _usar_porta_sugerida(self) -> None:
        s, mid = self.m["sugestao_porta"], self.m["id"]

        def trocar() -> Dict[str, Any]:
            valores = self.painel.ler_config(mid)["valores"]
            valores["com_port"] = s["porta_nova"]
            return self.painel.salvar_config(mid, valores)

        def pronto(r: Dict[str, Any]) -> None:
            if r.get("ok"):
                extra = " Reinicie para aplicar." if r.get("rodando") else ""
                self.f_retorno.mostrar("ok", f"Porta alterada para {s['porta_nova']}.{extra}")
                self.form.carregar()
            else:
                self.f_retorno.mostrar("erro", " ".join((r.get("erros") or {}).values()) or r.get("mensagem", ""))
            self.acao_concluida.emit()

        self.ponte.executar(trocar, pronto)


# ═══════════════════════════════════════════════════════════
# PÁGINA DE PORTAS
# ═══════════════════════════════════════════════════════════

class PaginaPortas(QWidget):
    selecionar = Signal(str)

    def __init__(self, painel: Painel, ponte: Ponte, c: Dict[str, str]) -> None:
        super().__init__()
        self.painel, self.ponte, self.c = painel, ponte, c
        self.titulos: Dict[str, str] = {}
        self.testadas = False
        self.carregando = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        cabeca = QFrame()
        cabeca.setObjectName("Cabeca")
        cl = QHBoxLayout(cabeca)
        cl.setContentsMargins(18, 12, 18, 12)
        ident = QVBoxLayout()
        ident.setSpacing(3)
        ident.addWidget(rotulo("Portas seriais", "CabecaNome"))
        ident.addWidget(rotulo("Portas COM detectadas neste computador. A lista acompanha a conexão e a remoção de cabos USB.", "CabecaInfo"))
        cl.addLayout(ident, 1)
        self.bt_acompanhar = QPushButton(tema.icone("atualizar", c["texto"], 14), "Acompanhar")
        self.bt_acompanhar.hide()
        self.bt_verificar = QPushButton(tema.icone("verificar", c["texto"], 14), "Verificar disponibilidade")
        self.bt_acompanhar.clicked.connect(lambda: self.carregar(False))
        self.bt_verificar.clicked.connect(lambda: self.carregar(True))
        cl.addWidget(self.bt_acompanhar)
        cl.addWidget(self.bt_verificar)
        lay.addWidget(cabeca)

        corpo = QWidget()
        corpo.setObjectName("Fundo")
        bl = QVBoxLayout(corpo)
        bl.setContentsMargins(18, 14, 18, 14)
        bl.setSpacing(12)
        info = Faixa(c)
        bl.addWidget(info)  # antes do mostrar: sem pai, ele viraria janela própria
        info.mostrar("", "\"Verificar disponibilidade\" abre e fecha cada porta que nenhuma máquina em execução usa, "
                         "para detectar outro programa ocupando a porta. Portas de máquinas em execução não são tocadas.")
        self.erro = Faixa(c)
        bl.addWidget(self.erro)

        self.tabela = QTableWidget(0, 5)
        self.tabela.setHorizontalHeaderLabels(["PORTA", "DISPOSITIVO", "IDENTIFICAÇÃO", "EQUIPAMENTOS", "SITUAÇÃO"])
        self.tabela.verticalHeader().hide()
        self.tabela.setShowGrid(False)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        cab = self.tabela.horizontalHeader()
        cab.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cab.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabela.cellDoubleClicked.connect(self._abrir_maquina)
        bl.addWidget(self.tabela, 1)
        lay.addWidget(corpo, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: (not self.testadas and self.isVisible()) and self.carregar(False))
        self.timer.start(INTERVALO_ESTADO_MS)

    def definir_titulos(self, maquinas: List[Dict[str, Any]]) -> None:
        self.titulos = {m["id"]: m["titulo"] for m in maquinas}

    def carregar(self, testar: bool) -> None:
        if self.carregando:
            return
        self.carregando = True
        self.bt_verificar.setEnabled(False)
        if testar:
            self.bt_verificar.setText("Verificando…")

        def pronto(r: Any) -> None:
            self.carregando = False
            self.bt_verificar.setEnabled(True)
            self.bt_verificar.setText("Verificar disponibilidade")
            if isinstance(r, dict) and r.get("_erro"):
                self.erro.mostrar("erro", r["mensagem"])
                return
            self.erro.hide()
            self.testadas = testar
            self.bt_acompanhar.setVisible(testar)
            self._preencher(r)

        self.ponte.executar(lambda: self.painel.listar_portas(testar), pronto)

    def _preencher(self, portas: List[Dict[str, Any]]) -> None:
        c = self.c
        self.tabela.setRowCount(0)
        if not portas:
            self.tabela.setRowCount(1)
            item = QTableWidgetItem("Nenhuma porta serial detectada. Verifique o cabo ou o adaptador USB.")
            item.setForeground(QColor(c["texto3"]))
            self.tabela.setItem(0, 0, item)
            self.tabela.setSpan(0, 0, 1, 5)
            return
        self.tabela.clearSpans()
        self.tabela.setRowCount(len(portas))
        mono = self.tabela.font()
        mono.setFamilies(["Cascadia Mono", "Consolas", "DejaVu Sans Mono"])
        for i, p in enumerate(portas):
            porta = QTableWidgetItem(p["porta"])
            f = mono
            f.setBold(True)
            porta.setFont(f)
            disp = QTableWidgetItem(p["descricao"] + (f"\n{p['fabricante']}" if p["fabricante"] else "") or "—")
            if p["usb"]:
                ident = f"USB {p['vid']:04X}:{p['pid']:04X}"
                if p["serie"]:
                    ident += f"\nS/N {p['serie']}"
                if p["local"]:
                    ident += f"\nPosição {p['local']}"
            elif p.get("virtual"):
                ident = "Porta virtual"
            else:
                ident = "Porta nativa"
            ident_item = QTableWidgetItem(ident)
            ident_item.setForeground(QColor(c["texto2"]))
            equip = QTableWidgetItem("\n".join(self.titulos.get(m, m) for m in p["maquinas"]) or "—")
            equip.setData(Qt.ItemDataRole.UserRole, p["maquinas"][0] if p["maquinas"] else "")
            if p["maquinas"]:
                equip.setForeground(QColor(c["destaque"]))
                equip.setToolTip("Clique duas vezes para abrir o equipamento")
            situacao = {
                "em_uso": (f"● Em uso · {self.titulos.get(p['em_uso_por'], p['em_uso_por'])}", c["ok"]),
                "livre": ("Livre", c["texto2"]),
                "ocupada": ("● Ocupada por outro programa", c["erro"]),
            }.get(p["situacao"], ("Não verificada", c["texto3"]))
            sit = QTableWidgetItem(situacao[0])
            sit.setForeground(QColor(situacao[1]))
            if p.get("erro"):
                sit.setToolTip(p["erro"])
            for j, item in enumerate((porta, disp, ident_item, equip, sit)):
                self.tabela.setItem(i, j, item)
        self.tabela.resizeRowsToContents()

    def _abrir_maquina(self, linha: int, coluna: int) -> None:
        item = self.tabela.item(linha, 3)
        mid = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if mid:
            self.selecionar.emit(mid)


# ═══════════════════════════════════════════════════════════
# JANELA PRINCIPAL
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# LOGIN DE ADMINISTRADOR
# ═══════════════════════════════════════════════════════════

class DialogoLogin(QDialog):
    """Pede a senha mestra. A conferência (PBKDF2, ~0,5 s) roda fora da thread da janela."""

    def __init__(self, parent: QWidget, painel: Painel, ponte: Ponte, c: Dict[str, str]) -> None:
        super().__init__(parent)
        self.painel, self.ponte, self.c = painel, ponte, c
        self.setWindowTitle("Entrar como administrador")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        topo = QHBoxLayout()
        ico = QLabel()
        ico.setPixmap(tema.icone("cadeado", c["destaque"], 20).pixmap(20, 20))
        topo.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)
        titulo = rotulo("Administrador", "CabecaNome")
        topo.addWidget(titulo, 1)
        lay.addLayout(topo)

        from shared.painel import acesso
        minutos = acesso.TEMPO_SESSAO // 60
        texto = rotulo(f"A configuração dos equipamentos só muda com a senha de administrador. "
                       f"A sessão termina após {minutos} min sem alterações ou ao fechar o painel.", "Ajuda")
        texto.setWordWrap(True)
        lay.addWidget(texto)

        self.senha = QLineEdit()
        self.senha.setEchoMode(QLineEdit.EchoMode.Password)
        self.senha.setPlaceholderText("Senha")
        lay.addWidget(self.senha)
        self.erro = rotulo(nome="Erro")
        self.erro.setWordWrap(True)
        lay.addWidget(self.erro)
        self.erro.hide()

        botoes = QHBoxLayout()
        botoes.addStretch(1)
        cancelar = QPushButton("Cancelar")
        cancelar.clicked.connect(self.reject)
        self.bt_entrar = QPushButton(tema.icone("cadeado_aberto", c["destaque_texto"], 14), "Entrar")
        definir_prop(self.bt_entrar, "papel", "primario")
        self.bt_entrar.setDefault(True)
        self.bt_entrar.clicked.connect(self.entrar)
        self.senha.returnPressed.connect(self.entrar)
        botoes.addWidget(cancelar)
        botoes.addWidget(self.bt_entrar)
        lay.addLayout(botoes)

        if not painel.sessao.definida:
            self._mostrar_erro("Este leitor.exe foi gerado sem senha mestra: a edição fica bloqueada. "
                               "Defina a senha (python3 -m shared.painel.acesso) e gere o .exe de novo.")
            self.senha.setEnabled(False)
            self.bt_entrar.setEnabled(False)

    def _mostrar_erro(self, texto: str) -> None:
        self.erro.setText(f"⚠ {texto}")
        self.erro.show()

    def entrar(self) -> None:
        if not self.bt_entrar.isEnabled():
            return
        senha = self.senha.text()
        if not senha:
            self._mostrar_erro("Digite a senha.")
            return
        self.bt_entrar.setEnabled(False)
        self.senha.setEnabled(False)

        def pronto(r: Dict[str, Any]) -> None:
            self.bt_entrar.setEnabled(True)
            self.senha.setEnabled(True)
            if r.get("ok"):
                self.accept()
                return
            self._mostrar_erro(r.get("mensagem") or "Não foi possível entrar.")
            self.senha.selectAll()
            self.senha.setFocus()

        self.ponte.executar(lambda: self.painel.entrar(senha), pronto)


class DialogoRaiz(QDialog):
    """Configurações > Pasta raiz do programa."""

    def __init__(self, parent: QWidget, painel: Painel, ponte: Ponte, c: Dict[str, str]) -> None:
        super().__init__(parent)
        self.painel, self.ponte, self.c = painel, ponte, c
        self.setWindowTitle("Pasta raiz do programa")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(rotulo("Pasta raiz do programa", "CabecaNome"))
        texto = rotulo(
            "Uma pasta para tudo o que o leitor grava: os config_&lt;maquina&gt;.json, a lista de equipamentos "
            "(leitor_painel.json), as pastas Analisador&lt;MAQUINA&gt; (gerados, enviados, logs) e o log do painel. "
            "Vazio = padrão: configs ao lado do leitor.exe e dados na Área de Trabalho.", "Ajuda")
        texto.setWordWrap(True)
        texto.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(texto)

        caixa = QHBoxLayout()
        self.pasta = QLineEdit(painel.raiz())
        self.pasta.setProperty("mono", True)
        self.pasta.setPlaceholderText("Padrão (ao lado do leitor.exe e Área de Trabalho)")
        procurar = QPushButton(tema.icone("pasta", c["texto"], 14), "Procurar…")
        procurar.clicked.connect(self._procurar)
        padrao = QPushButton("Usar padrão")
        padrao.clicked.connect(lambda: self.pasta.setText(""))
        caixa.addWidget(self.pasta, 1)
        caixa.addWidget(procurar)
        caixa.addWidget(padrao)
        lay.addLayout(caixa)

        nota = rotulo("Ao aplicar: os configs e a lista vão para a pasta nova (o que já existir lá é usado como está) e "
                      "os exames ainda não enviados de gerados/ são levados junto. O histórico (enviados, logs) fica "
                      "na pasta antiga. As máquinas precisam estar paradas.", "Ajuda")
        nota.setWordWrap(True)
        lay.addWidget(nota)
        self.retorno = Faixa(c)
        lay.addWidget(self.retorno)

        botoes = QHBoxLayout()
        botoes.addStretch(1)
        fechar = QPushButton("Fechar")
        fechar.clicked.connect(self.reject)
        self.bt_aplicar = QPushButton(tema.icone("salvar", c["destaque_texto"], 14), "Aplicar")
        definir_prop(self.bt_aplicar, "papel", "primario")
        self.bt_aplicar.clicked.connect(self.aplicar)
        botoes.addWidget(fechar)
        botoes.addWidget(self.bt_aplicar)
        lay.addLayout(botoes)

    def _procurar(self) -> None:
        inicio = self.pasta.text() or self.painel.pasta_config
        escolhida = QFileDialog.getExistingDirectory(self, "Pasta raiz do programa", inicio)
        if escolhida:
            self.pasta.setText(os.path.normpath(escolhida))

    def aplicar(self) -> None:
        nova = self.pasta.text().strip()
        self.bt_aplicar.setEnabled(False)

        def pronto(r: Dict[str, Any]) -> None:
            self.bt_aplicar.setEnabled(True)
            if r.get("ok"):
                self.retorno.mostrar("ok", r.get("mensagem", "Pasta raiz alterada.") +
                                     " O log do painel passa para a pasta nova ao reabrir o painel.")
            else:
                self.retorno.mostrar("erro", r.get("mensagem", "Falhou."))

        self.ponte.executar(lambda: self.painel.trocar_raiz(nova), pronto)


def raiz_sugerida() -> str:
    """Sugestão do primeiro uso: C:\\LeitorHL7 no Windows, ~/LeitorHL7 fora dele."""
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("SystemDrive", "C:") + os.sep, "LeitorHL7")
    return os.path.join(os.path.expanduser("~"), "LeitorHL7")


class DialogoPrimeiroUso(QDialog):
    """
    Primeira abertura do painel neste computador (sem leitor_raiz.json):
    1. senha mestra (abre a sessão de administrador);
    2. pasta raiz do programa, com "Criar nova pasta…".
    """

    def __init__(self, parent: Optional[QWidget], painel: Painel, ponte: Ponte, c: Dict[str, str]) -> None:
        super().__init__(parent)
        self.painel, self.ponte, self.c = painel, ponte, c
        self.setWindowTitle("Leitor HL7 · primeira configuração")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        self.passo = rotulo(nome="LeituraRot")
        lay.addWidget(self.passo)
        self.paginas = QStackedWidget()
        lay.addWidget(self.paginas)

        # Passo 1: senha
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(10)
        l1.addWidget(rotulo("Entrar como administrador", "CabecaNome"))
        t1 = rotulo("Primeira vez que o Leitor HL7 abre neste computador. Para configurá-lo, digite a senha de "
                    "administrador.", "Ajuda")
        t1.setWordWrap(True)
        l1.addWidget(t1)
        self.senha = QLineEdit()
        self.senha.setEchoMode(QLineEdit.EchoMode.Password)
        self.senha.setPlaceholderText("Senha")
        self.senha.returnPressed.connect(self._avancar)
        l1.addWidget(self.senha)
        l1.addStretch(1)
        self.paginas.addWidget(p1)

        # Passo 2: pasta raiz
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.setSpacing(10)
        l2.addWidget(rotulo("Pasta raiz do programa", "CabecaNome"))
        t2 = rotulo("Onde o leitor guarda tudo: configs dos equipamentos, a lista de equipamentos, as pastas "
                    "Analisador&lt;MAQUINA&gt; (gerados, enviados, logs) e o log do painel. Dá para trocar depois em "
                    "Configurações &gt; Pasta raiz do programa.", "Ajuda")
        t2.setWordWrap(True)
        t2.setTextFormat(Qt.TextFormat.RichText)
        l2.addWidget(t2)
        caixa = QHBoxLayout()
        self.pasta = QLineEdit(raiz_sugerida())
        self.pasta.setProperty("mono", True)
        procurar = QPushButton(tema.icone("pasta", c["texto"], 14), "Procurar…")
        procurar.clicked.connect(self._procurar)
        self.bt_criar = QPushButton(tema.icone("mais", c["texto"], 14), "Criar nova pasta…")
        self.bt_criar.clicked.connect(self._criar_pasta)
        caixa.addWidget(self.pasta, 1)
        caixa.addWidget(procurar)
        caixa.addWidget(self.bt_criar)
        l2.addLayout(caixa)
        self.padrao = QCheckBox("Não usar pasta raiz (configs ao lado do leitor.exe, dados na Área de Trabalho)")
        self.padrao.toggled.connect(lambda on: self.pasta.setEnabled(not on))
        l2.addWidget(self.padrao)
        l2.addStretch(1)
        self.paginas.addWidget(p2)

        self.retorno = Faixa(c)
        lay.addWidget(self.retorno)
        botoes = QHBoxLayout()
        botoes.addStretch(1)
        self.bt_sair = QPushButton("Fechar")
        self.bt_sair.clicked.connect(self.reject)
        self.bt_avancar = QPushButton()
        definir_prop(self.bt_avancar, "papel", "primario")
        self.bt_avancar.setDefault(True)
        self.bt_avancar.clicked.connect(self._avancar)
        botoes.addWidget(self.bt_sair)
        botoes.addWidget(self.bt_avancar)
        lay.addLayout(botoes)
        self._mostrar_passo(1 if not painel.admin() else 2)

    def _mostrar_passo(self, n: int) -> None:
        self.paginas.setCurrentIndex(n - 1)
        self.passo.setText(f"PASSO {n} DE 2")
        self.retorno.hide()
        if n == 1:
            self.bt_avancar.setIcon(tema.icone("cadeado_aberto", self.c["destaque_texto"], 14))
            self.bt_avancar.setText("Entrar")
            self.senha.setFocus()
        else:
            self.bt_avancar.setIcon(tema.icone("ok", self.c["destaque_texto"], 14))
            self.bt_avancar.setText("Concluir")

    def _avancar(self) -> None:
        if not self.bt_avancar.isEnabled():
            return
        if self.paginas.currentIndex() == 0:
            self._entrar()
        else:
            self._concluir()

    def _entrar(self) -> None:
        senha = self.senha.text()
        if not senha:
            self.retorno.mostrar("erro", "Digite a senha.")
            return
        self.bt_avancar.setEnabled(False)

        def pronto(r: Dict[str, Any]) -> None:
            self.bt_avancar.setEnabled(True)
            if r.get("ok"):
                self._mostrar_passo(2)
            else:
                self.retorno.mostrar("erro", r.get("mensagem") or "Não foi possível entrar.")
                self.senha.selectAll()
                self.senha.setFocus()

        self.ponte.executar(lambda: self.painel.entrar(senha), pronto)

    def _procurar(self) -> None:
        atual = self.pasta.text().strip()
        inicio = atual if os.path.isdir(atual) else os.path.dirname(atual) or os.path.expanduser("~")
        escolhida = QFileDialog.getExistingDirectory(self, "Pasta raiz do programa", inicio)
        if escolhida:
            self.pasta.setText(os.path.normpath(escolhida))

    def _criar_pasta(self) -> None:
        """Cria uma pasta nova dentro da que está no campo (ou ao lado, se ela ainda não existir)."""
        atual = self.pasta.text().strip() or raiz_sugerida()
        base = atual if os.path.isdir(atual) else (os.path.dirname(atual) or os.path.expanduser("~"))
        nome, ok = QInputDialog.getText(self, "Criar nova pasta", f"Nome da nova pasta dentro de {base}:",
                                        QLineEdit.EchoMode.Normal, "LeitorHL7")
        nome = (nome or "").strip()
        if not ok or not nome:
            return
        if any(ch in nome for ch in '\\/:*?"<>|'):
            self.retorno.mostrar("erro", "O nome não pode ter \\ / : * ? \" < > |")
            return
        nova = os.path.join(base, nome)
        try:
            os.makedirs(nova, exist_ok=False)
        except FileExistsError:
            self.retorno.mostrar("aviso", f"A pasta {nova} já existia; ela foi selecionada.")
            self.pasta.setText(nova)
            return
        except OSError as e:
            logging.error(f"✗ Pasta {nova} não criada: {e}")
            self.retorno.mostrar("erro", f"Não foi possível criar a pasta: {e}")
            return
        logging.info(f"✓ Pasta {nova} criada no primeiro uso.")
        self.pasta.setText(nova)
        self.retorno.mostrar("ok", f"Pasta criada: {nova}")

    def _concluir(self) -> None:
        nova = "" if self.padrao.isChecked() else self.pasta.text().strip()
        if not self.padrao.isChecked() and not nova:
            self.retorno.mostrar("erro", "Escolha a pasta raiz ou marque para não usar.")
            return
        self.bt_avancar.setEnabled(False)

        def pronto(r: Dict[str, Any]) -> None:
            self.bt_avancar.setEnabled(True)
            if r.get("ok"):
                self.accept()
            else:
                self.retorno.mostrar("erro", r.get("mensagem", "Falhou."))

        self.ponte.executar(lambda: self.painel.trocar_raiz(nova), pronto)


class JanelaPrincipal(QMainWindow):
    def __init__(
        self,
        painel: Painel,
        icone_app: Optional[QIcon] = None,
        logo: Optional[QPixmap] = None,
        caminho_changelog: str = "",
        ao_reiniciar: Optional[Callable[[], None]] = None,
        ao_trocar_tema: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()
        self.painel = painel
        self.ao_trocar_tema = ao_trocar_tema
        self.admin_atual: Optional[bool] = None
        self.c = tema.cores()
        self.ponte = Ponte()
        self.estado: Dict[str, Any] = {}
        self.atualizando = False
        self.selecionada = ""
        self.caminho_changelog = caminho_changelog
        self.ao_reiniciar = ao_reiniciar
        self.ids_nav: List[str] = []
        self.dialogo_versao = None

        self.setWindowTitle(f"Leitor HL7 {painel_versao()}")
        if icone_app:
            self.setWindowIcon(icone_app)
        self.resize(1280, 820)
        self.setMinimumSize(980, 620)

        raiz = QWidget()
        raiz.setObjectName("Fundo")
        rl = QVBoxLayout(raiz)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._titulo(logo))

        corpo = QHBoxLayout()
        corpo.setContentsMargins(0, 0, 0, 0)
        corpo.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFixedWidth(272)
        self.nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.nav.currentItemChanged.connect(self._nav_mudou)
        corpo.addWidget(self.nav)

        self.paginas = QStackedWidget()
        self.pagina_maquina = PaginaMaquina(painel, self.ponte, self.c)
        self.pagina_maquina.acao_concluida.connect(self.atualizar)
        self.pagina_maquina.removida.connect(self._removida)
        self.pagina_portas = PaginaPortas(painel, self.ponte, self.c)
        self.pagina_portas.selecionar.connect(self.selecionar_maquina)
        self.pagina_adicionar = PaginaAdicionar(painel, self.ponte, self.c)
        self.pagina_adicionar.adicionado.connect(self._adicionada)
        self.paginas.addWidget(self.pagina_maquina)
        self.paginas.addWidget(self.pagina_portas)
        self.paginas.addWidget(self.pagina_adicionar)
        for alvo in (self.pagina_maquina, self.pagina_maquina.form, self.pagina_adicionar.form):
            alvo.exigir_admin = self.exigir_admin
        corpo.addWidget(self.paginas, 1)
        rl.addLayout(corpo, 1)
        self.setCentralWidget(raiz)

        self._status()
        self._aplicar_acesso(painel.admin())
        self.itens: Dict[str, QListWidgetItem] = {}
        self.widgets_nav: Dict[str, ItemMaquina] = {}

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.atualizar)
        self.timer.start(INTERVALO_ESTADO_MS)
        self.atualizar()

    # ── montagem ────────────────────────────────────────────

    def _titulo(self, logo: Optional[QPixmap]) -> QWidget:
        t = QFrame()
        t.setObjectName("Titulo")
        t.setFixedHeight(46)
        l = QHBoxLayout(t)
        l.setContentsMargins(14, 0, 16, 0)
        l.setSpacing(10)
        if logo:
            lg = QLabel()
            lg.setPixmap(logo)
            l.addWidget(lg)
        l.addWidget(rotulo("Leitor HL7", "TituloNome"))
        sep = QFrame()
        sep.setObjectName("Separador")
        sep.setFixedSize(1, 16)
        l.addWidget(sep)
        l.addWidget(rotulo("Console de integração de analisadores", "TituloSub"))
        l.addStretch(1)
        self.resumo = rotulo(nome="Resumo")
        self.resumo.setTextFormat(Qt.TextFormat.RichText)
        l.addWidget(self.resumo)
        sep2 = QFrame()
        sep2.setObjectName("Separador")
        sep2.setFixedSize(1, 16)
        l.addSpacing(6)
        l.addWidget(sep2)
        self.bt_acesso = QPushButton()
        self.bt_acesso.setObjectName("TituloBotao")
        self.bt_acesso.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bt_acesso.clicked.connect(self._menu_acesso)
        l.addWidget(self.bt_acesso)
        self.bt_config = QPushButton(tema.icone("config", self.c["texto2"], 14), "Configurações")
        self.bt_config.setObjectName("TituloBotao")
        self.bt_config.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bt_config.clicked.connect(self._menu_config)
        l.addWidget(self.bt_config)
        return t

    # ── acesso e configurações ──────────────────────────────

    def _aplicar_acesso(self, admin: bool) -> None:
        """Libera ou bloqueia a edição em todas as páginas (login, saída ou sessão expirada)."""
        if admin == self.admin_atual:
            return
        self.admin_atual = admin
        if admin:
            self.bt_acesso.setIcon(tema.icone("cadeado_aberto", self.c["destaque"], 14))
            self.bt_acesso.setText("Administrador")
            self.bt_acesso.setToolTip("Configuração liberada para edição. Clique para sair.")
        else:
            self.bt_acesso.setIcon(tema.icone("cadeado", self.c["texto2"], 14))
            self.bt_acesso.setText("Somente leitura")
            self.bt_acesso.setToolTip("Entre como administrador para alterar a configuração.")
        definir_prop(self.bt_acesso, "admin", admin)
        self.pagina_maquina.definir_acesso(admin)
        self.pagina_adicionar.form.definir_acesso(admin)

    def exigir_admin(self, acao: Callable[[], None]) -> None:
        """Roda `acao` como administrador: pede a senha antes, se preciso."""
        if self.painel.admin():
            self._aplicar_acesso(True)
            acao()
            return
        self._aplicar_acesso(False)
        if self.entrar():
            acao()

    def entrar(self) -> bool:
        dialogo = DialogoLogin(self, self.painel, self.ponte, self.c)
        ok = dialogo.exec() == QDialog.DialogCode.Accepted
        dialogo.deleteLater()
        self._aplicar_acesso(self.painel.admin())
        return ok

    def sair_admin(self) -> None:
        self.painel.sair()
        self._aplicar_acesso(False)

    def _menu_acesso(self) -> None:
        if not self.painel.admin():
            self.exigir_admin(lambda: None)
            return
        menu = QMenu(self)
        sair = menu.addAction(tema.icone("sair", self.c["texto2"]), "Sair do modo administrador")
        if menu.exec(self.bt_acesso.mapToGlobal(self.bt_acesso.rect().bottomLeft())) is sair:
            self.sair_admin()

    def _menu_config(self) -> None:
        menu = QMenu(self)
        temas = menu.addMenu(tema.icone("tema", self.c["texto2"]), "Tema")
        grupo = QActionGroup(menu)
        atual = self.painel.preferencia("tema")
        for valor, texto in (("sistema", "Seguir o sistema"), ("claro", "Claro"), ("escuro", "Escuro")):
            acao = temas.addAction(texto)
            acao.setCheckable(True)
            acao.setChecked(valor == atual)
            acao.setData(valor)
            grupo.addAction(acao)
        menu.addSeparator()
        if self.painel.admin():
            login = menu.addAction(tema.icone("sair", self.c["texto2"]), "Sair do modo administrador")
        else:
            login = menu.addAction(tema.icone("cadeado", self.c["texto2"]), "Entrar como administrador…")
        menu.addSeparator()
        raiz_acao = menu.addAction(tema.icone("pasta", self.c["texto2"]), "Pasta raiz do programa…")
        inicio = self.painel.inicio_automatico()
        com_windows = menu.addAction("Iniciar com o Windows")
        com_windows.setCheckable(True)
        com_windows.setChecked(inicio["ativo"])
        com_windows.setEnabled(inicio["suportado"])
        if not inicio["suportado"]:
            com_windows.setToolTip("Só no leitor.exe no Windows.")
        menu.setToolTipsVisible(True)
        pasta = menu.addAction(tema.icone("pasta", self.c["texto2"]), "Abrir pasta do programa")
        menu.addSeparator()
        sobre = menu.addAction(tema.icone("info", self.c["texto2"]), "Sobre esta versão…")
        verificar = menu.addAction(tema.icone("atualizar", self.c["texto2"]), "Verificar atualização…")

        escolha = menu.exec(self.bt_config.mapToGlobal(self.bt_config.rect().bottomLeft()))
        if escolha is None:
            return
        if escolha.actionGroup() is grupo:
            self.trocar_tema(escolha.data())
        elif escolha is login:
            if self.painel.admin():
                self.sair_admin()
            else:
                self.exigir_admin(lambda: None)
        elif escolha is raiz_acao:
            self.exigir_admin(self.abrir_raiz)
        elif escolha is com_windows:
            ativar = escolha.isChecked()
            self.exigir_admin(lambda: self.definir_inicio_automatico(ativar))
        elif escolha is pasta:
            abrir_no_explorador(self.painel.pasta_config)
        elif escolha is sobre:
            self.abrir_versao(verificar=False)
        elif escolha is verificar:
            self.abrir_versao(verificar=True)

    def abrir_raiz(self) -> None:
        dialogo = DialogoRaiz(self, self.painel, self.ponte, self.c)
        dialogo.exec()
        dialogo.deleteLater()
        self.pagina_maquina.m = {}  # recarrega formulário e log com as pastas novas
        self.pagina_adicionar.preparar()
        self.atualizar()

    def definir_inicio_automatico(self, ativar: bool) -> None:
        def pronto(r: Dict[str, Any]) -> None:
            if r.get("ok"):
                texto = ("Iniciar com o Windows ativado: o painel abre minimizado e inicia as máquinas marcadas com "
                         "\"Iniciar ao ligar\"." if ativar else "Iniciar com o Windows desativado.")
                self.st_painel.setText(f"<span style='color:{self.c['ok']}'>●</span> {texto}")
            else:
                QMessageBox.warning(self, "Iniciar com o Windows", r.get("mensagem", "Falhou."))

        self.ponte.executar(lambda: self.painel.definir_inicio_automatico(ativar), pronto)

    def iniciar_ao_ligar(self) -> None:
        """Aberto com o Windows (--ao-ligar): inicia as máquinas marcadas."""
        def pronto(resultados: Any) -> None:
            if isinstance(resultados, list):
                falhas = [r for r in resultados if not r.get("ok")]
                if falhas:
                    nomes = ", ".join(r["id"] for r in falhas)
                    self.st_painel.setText(f"<span style='color:{self.c['erro']}'>●</span> Ao ligar, não iniciaram: {nomes}")
            self.atualizar()

        self.ponte.executar(self.painel.iniciar_ao_ligar, pronto)

    def trocar_tema(self, valor: str) -> None:
        if valor == self.painel.preferencia("tema"):
            return
        self.painel.definir_preferencia("tema", valor)
        if self.ao_trocar_tema:
            # Recria a janela com as cores novas (ícones e estilos são gerados com elas).
            self.ao_trocar_tema(valor)

    def descartar(self) -> None:
        """Fecha esta janela sem encerrar o painel (troca de tema)."""
        self._descartada = True
        self.timer.stop()
        self.pagina_maquina.timer_log.stop()
        self.hide()
        self.deleteLater()

    def _status(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.st_painel = rotulo(nome="StatusItem")
        self.st_configs = Link("StatusMono")
        self.st_dica = rotulo("Fechar a janela não para as máquinas", "StatusItem")
        self.st_atualizado = rotulo(nome="StatusItem")
        self.st_versao = QPushButton(f"v{painel_versao()}  ▴")
        self.st_versao.setObjectName("StatusBotao")
        self.st_versao.setCursor(Qt.CursorShape.PointingHandCursor)
        self.st_versao.setToolTip("Sobre esta versão e atualização")
        self.st_versao.clicked.connect(self._menu_versao)
        sb.addWidget(self.st_painel)
        sb.addWidget(self.st_configs)
        sb.addWidget(self.st_dica)
        sb.addPermanentWidget(self.st_atualizado)
        sb.addPermanentWidget(self.st_versao)

    def _menu_versao(self) -> None:
        menu = QMenu(self)
        cab = menu.addAction(f"Leitor HL7 {painel_versao()}")
        cab.setEnabled(False)
        menu.addSeparator()
        sobre = menu.addAction(tema.icone("info", self.c["texto2"]), "Sobre esta versão…")
        verificar = menu.addAction(tema.icone("atualizar", self.c["texto2"]), "Verificar atualização…")
        pos = self.st_versao.mapToGlobal(self.st_versao.rect().topLeft())
        pos.setY(pos.y() - menu.sizeHint().height())
        escolha = menu.exec(pos)
        if escolha is sobre:
            self.abrir_versao(verificar=False)
        elif escolha is verificar:
            self.abrir_versao(verificar=True)

    def abrir_versao(self, verificar: bool) -> None:
        from shared.painel.janela_versao import DialogoVersao
        if self.dialogo_versao is None:
            self.dialogo_versao = DialogoVersao(self, self.ponte, self.c, self.caminho_changelog, self._reiniciar)
        self.dialogo_versao.show()
        self.dialogo_versao.raise_()
        if verificar:
            self.dialogo_versao.verificar()

    def _reiniciar(self) -> None:
        if self.ao_reiniciar:
            self.ao_reiniciar()

    def _grupo_nav(self, texto: str) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        lbl = rotulo(texto.upper(), "NavGrupo")
        lbl.setContentsMargins(14, 12, 0, 4)
        item.setSizeHint(lbl.sizeHint())
        self.nav.addItem(item)
        self.nav.setItemWidget(item, lbl)

    def _item_ferramenta(self, chave: str, icone: str, texto: str) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, chave)
        w = ItemFerramenta(self.c, icone, texto)
        item.setSizeHint(w.sizeHint())
        self.nav.addItem(item)
        self.nav.setItemWidget(item, w)
        self.itens[chave] = item

    def _montar_nav(self, maquinas: List[Dict[str, Any]], disponiveis: List[Dict[str, Any]]) -> None:
        self.nav.blockSignals(True)
        self.nav.clear()
        self.itens, self.widgets_nav = {}, {}
        self._grupo_nav("Equipamentos")
        for m in maquinas:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, m["id"])
            w = ItemMaquina(self.c)
            w.definir(m)
            item.setSizeHint(w.sizeHint().expandedTo(w.minimumSizeHint()))
            self.nav.addItem(item)
            self.nav.setItemWidget(item, w)
            self.itens[m["id"]] = item
            self.widgets_nav[m["id"]] = w
        if disponiveis:
            self._item_ferramenta("__adicionar__", "mais", "Adicionar equipamento")
        self._grupo_nav("Ferramentas")
        self._item_ferramenta("__portas__", "porta", "Portas seriais")
        self.ids_nav = [m["id"] for m in maquinas] + (["__adicionar__"] if disponiveis else [])
        self.nav.blockSignals(False)

    # ── estado ──────────────────────────────────────────────

    def atualizar(self) -> None:
        if self.atualizando:
            return
        self.atualizando = True
        self.ponte.executar(self.painel.estado, self._estado_chegou)

    def _estado_chegou(self, estado: Dict[str, Any]) -> None:
        self.atualizando = False
        if estado.get("_erro"):
            self.st_painel.setText(f"● Erro ao consultar o estado: {estado['mensagem']}")
            return
        self.estado = estado
        maquinas = estado["maquinas"]
        disponiveis = estado.get("disponiveis", [])
        ids = [m["id"] for m in maquinas] + (["__adicionar__"] if disponiveis else [])
        mudou_lista = ids != self.ids_nav
        if mudou_lista:
            self._montar_nav(maquinas, disponiveis)
            self.pagina_portas.definir_titulos(maquinas)
        self.pagina_adicionar.definir_disponiveis(disponiveis)
        for m in maquinas:
            self.widgets_nav[m["id"]].definir(m)

        em_exec = sum(1 for m in maquinas if m["estado"] == "rodando")
        pend = sum(m["pendentes"] for m in maquinas)
        cor_pend = self.c["aviso"] if pend else self.c["texto"]
        self.resumo.setText(
            f"Em execução <b style='color:{self.c['texto']}'>{em_exec}/{len(maquinas)}</b>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;Aguardando envio <b style='color:{cor_pend}'>{pend}</b>"
        )
        self.st_painel.setText(f"<span style='color:{self.c['ok']}'>●</span> Painel ativo")
        if "admin" in estado:
            # Sessão expirada (ou login em outra ação) aparece aqui.
            self._aplicar_acesso(bool(estado["admin"]))
        self.st_configs.definir(f"Configs: {estado['pasta_config']}", estado["pasta_config"])
        from datetime import datetime
        self.st_atualizado.setText(f"Atualizado {datetime.now().strftime('%H:%M:%S')}")

        if mudou_lista:
            # Lista nova (primeira vez, máquina adicionada ou removida): mantém a
            # seleção se ainda existir; sem máquinas, abre "Adicionar".
            atual = self.paginas.currentWidget()
            if self.selecionada in self.itens and atual is self.pagina_maquina:
                alvo = self.selecionada
            elif atual is self.pagina_portas:
                alvo = "__portas__"
            elif maquinas and atual is not self.pagina_adicionar:
                alvo = maquinas[0]["id"]
            else:
                alvo = "__adicionar__" if "__adicionar__" in self.itens else (maquinas[0]["id"] if maquinas else "__portas__")
            self.nav.setCurrentItem(self.itens[alvo])
        elif self.selecionada and self.paginas.currentWidget() is self.pagina_maquina:
            m = next((x for x in maquinas if x["id"] == self.selecionada), None)
            if m:
                self.pagina_maquina.definir(m)

    def _nav_mudou(self, atual: Optional[QListWidgetItem], _anterior: Any) -> None:
        if not atual:
            return
        mid = atual.data(Qt.ItemDataRole.UserRole)
        if mid == "__portas__":
            self.paginas.setCurrentWidget(self.pagina_portas)
            self.pagina_portas.carregar(False)
            return
        if mid == "__adicionar__":
            self.paginas.setCurrentWidget(self.pagina_adicionar)
            self.pagina_adicionar.preparar()
            return
        self.selecionada = mid
        self.paginas.setCurrentWidget(self.pagina_maquina)
        m = next((x for x in self.estado.get("maquinas", []) if x["id"] == mid), None)
        if m:
            self.pagina_maquina.definir(m)

    def _adicionada(self, mid: str) -> None:
        self.selecionada = mid
        self.paginas.setCurrentWidget(self.pagina_maquina)
        self.pagina_maquina.m = {}  # força recarregar o formulário e o log
        self.atualizar()

    def _removida(self, mid: str) -> None:
        if self.selecionada == mid:
            self.selecionada = ""
            self.paginas.setCurrentWidget(self.pagina_adicionar)
        self.atualizar()

    def selecionar_maquina(self, mid: str) -> None:
        if mid in self.itens:
            self.nav.setCurrentItem(self.itens[mid])

    def trazer_para_frente(self) -> None:
        """Outro clique no .exe: mostra esta janela em vez de abrir outra."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        logging.info("Janela do painel trazida para a frente (o .exe foi aberto de novo).")

    def closeEvent(self, evento) -> None:  # noqa: N802 (nome do Qt)
        if getattr(self, "_descartada", False):
            evento.accept()
            return
        self.painel.sair()
        logging.info("Janela fechada: painel encerrado. As máquinas abertas continuam rodando.")
        evento.accept()


def painel_versao() -> str:
    from shared.versao import VERSAO
    return VERSAO
