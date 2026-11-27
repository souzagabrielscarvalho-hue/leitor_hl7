"""
"Sobre esta versão" e atualização: o diálogo aberto pelo menu da versão na
barra inferior. A lógica (GitHub, download, SHA-256, troca do .exe) fica em
atualizacao.py; aqui só a interface.
"""

import logging
import subprocess
import sys
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from shared.painel import atualizacao, tema
from shared.versao import VERSAO


def reabrir_programa() -> bool:
    """Abre o .exe (já atualizado) avisando para esperar este painel fechar."""
    try:
        flags = 0
        if sys.platform.startswith("win"):
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, "--apos-atualizar"], close_fds=True, creationflags=flags)
        logging.info("✓ Painel reaberto na versão nova.")
        return True
    except OSError as e:
        logging.error(f"✗ Não foi possível reabrir o painel: {e}")
        return False


class DialogoVersao(QDialog):
    progresso = Signal(int, int)

    def __init__(
        self,
        parent: QWidget,
        ponte: Any,
        c: Dict[str, str],
        caminho_changelog: str,
        ao_reiniciar: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.ponte, self.c = ponte, c
        self.ao_reiniciar = ao_reiniciar
        self.info: Dict[str, Any] = {}
        self.setWindowTitle("Sobre o Leitor HL7")
        self.setMinimumSize(640, 560)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        topo = QHBoxLayout()
        nome = QLabel(f"Leitor HL7 <span style='color:{c['texto3']}'>versão</span> {VERSAO}")
        nome.setObjectName("CabecaNome")
        topo.addWidget(nome)
        topo.addStretch(1)
        repo = QLabel(f"<a style='color:{c['destaque']}' href='https://github.com/{atualizacao.REPOSITORIO}/releases'>"
                      f"github.com/{atualizacao.REPOSITORIO}</a>")
        repo.setObjectName("Ajuda")
        repo.setOpenExternalLinks(True)
        topo.addWidget(repo)
        lay.addLayout(topo)

        lay.addWidget(self._titulo("Nesta versão"))
        notas = atualizacao.notas_da_versao(caminho_changelog) or "Notas desta versão indisponíveis."
        self.texto_versao = QTextBrowser()
        self.texto_versao.setOpenExternalLinks(True)
        self.texto_versao.setMarkdown(notas)
        lay.addWidget(self.texto_versao, 3)

        lay.addWidget(self._titulo("Atualização"))
        self.situacao = QLabel("Clique em Verificar para consultar a última versão publicada.")
        self.situacao.setWordWrap(True)
        self.situacao.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.situacao)

        self.texto_nova = QTextBrowser()
        self.texto_nova.setOpenExternalLinks(True)
        self.texto_nova.hide()
        lay.addWidget(self.texto_nova, 2)

        self.barra = QProgressBar()
        self.barra.setTextVisible(True)
        self.barra.hide()
        lay.addWidget(self.barra)

        botoes = QHBoxLayout()
        self.bt_pagina = QPushButton("Abrir página da release")
        self.bt_pagina.clicked.connect(self._abrir_pagina)
        self.bt_pagina.hide()
        botoes.addWidget(self.bt_pagina)
        botoes.addStretch(1)
        self.bt_verificar = QPushButton(tema.icone("atualizar", c["texto"], 14), "Verificar atualização")
        self.bt_baixar = QPushButton(tema.icone("salvar", c["destaque_texto"], 14), "Baixar e instalar")
        self.bt_reiniciar = QPushButton(tema.icone("reiniciar", c["destaque_texto"], 14), "Reabrir na versão nova")
        for b in (self.bt_baixar, self.bt_reiniciar):
            b.setProperty("papel", "primario")
            b.hide()
        fechar = QPushButton("Fechar")
        fechar.clicked.connect(self.close)
        self.bt_verificar.clicked.connect(self.verificar)
        self.bt_baixar.clicked.connect(self._baixar)
        self.bt_reiniciar.clicked.connect(self._reiniciar)
        for b in (self.bt_verificar, self.bt_baixar, self.bt_reiniciar, fechar):
            botoes.addWidget(b)
        lay.addLayout(botoes)

        self.progresso.connect(self._progresso)

    def _titulo(self, texto: str) -> QLabel:
        t = QLabel(texto.upper())
        t.setObjectName("LeituraRot")
        return t

    # ── verificar ──────────────────────────────────────────

    def verificar(self) -> None:
        self.bt_verificar.setEnabled(False)
        self.bt_baixar.hide()
        self.texto_nova.hide()
        self.situacao.setText("Consultando o GitHub…")
        self.ponte.executar(atualizacao.verificar, self._verificado)

    def _verificado(self, info: Dict[str, Any]) -> None:
        self.bt_verificar.setEnabled(True)
        self.info = info
        c = self.c
        if info.get("erro") or info.get("_erro"):
            self.situacao.setText(f"<span style='color:{c['erro']}'>✗ {info.get('erro') or info.get('mensagem')}</span>")
            return
        if info.get("sem_release"):
            self.situacao.setText("Nenhuma versão publicada ainda no repositório.")
            return
        if not info["disponivel"]:
            self.situacao.setText(f"<span style='color:{c['ok']}'>✓</span> Você está na versão mais recente "
                                  f"(<b>{VERSAO}</b>).")
            return
        self.situacao.setText(f"<span style='color:{c['aviso']}'>●</span> Versão <b>{info['versao']}</b> disponível "
                              f"(instalada: {VERSAO}).")
        self.texto_nova.setMarkdown(info.get("notas") or "Sem notas na release.")
        self.texto_nova.show()
        self.bt_pagina.setVisible(bool(info.get("pagina")))
        if info.get("erro_anexo"):
            self.situacao.setText(self.situacao.text() + f"<br><span style='color:{c['erro']}'>✗ {info['erro_anexo']}</span>")
        elif not atualizacao.pode_atualizar_sozinho():
            self.situacao.setText(self.situacao.text() + "<br>Executando pelo código-fonte: a troca automática só "
                                  "funciona no leitor.exe. Use a página da release.")
        else:
            self.bt_baixar.show()

    # ── baixar e instalar ──────────────────────────────────

    def _baixar(self) -> None:
        self.bt_baixar.setEnabled(False)
        self.bt_verificar.setEnabled(False)
        self.barra.setValue(0)
        self.barra.show()
        self.situacao.setText(f"Baixando a versão {self.info['versao']}…")
        exe = sys.executable
        info = dict(self.info)

        def trabalho() -> Dict[str, Any]:
            r = atualizacao.baixar(info, exe, progresso=lambda feito, total: self.progresso.emit(feito, total))
            if not r["ok"]:
                return r
            return atualizacao.instalar(exe, r["arquivo"])

        self.ponte.executar(trabalho, self._instalado)

    def _progresso(self, feito: int, total: int) -> None:
        if total > 0:
            self.barra.setMaximum(total)
            self.barra.setValue(feito)
            self.barra.setFormat(f"{feito / 1048576:.1f} de {total / 1048576:.1f} MB")
        else:
            self.barra.setMaximum(0)

    def _instalado(self, r: Dict[str, Any]) -> None:
        c = self.c
        self.bt_verificar.setEnabled(True)
        if not r.get("ok"):
            self.barra.hide()
            self.bt_baixar.setEnabled(True)
            self.situacao.setText(f"<span style='color:{c['erro']}'>✗ {r.get('mensagem')}</span> "
                                  f"A versão {VERSAO} continua instalada.")
            return
        self.bt_baixar.hide()
        self.bt_reiniciar.show()
        self.situacao.setText(
            f"<span style='color:{c['ok']}'>✓</span> Versão <b>{self.info['versao']}</b> instalada. Reabra o painel "
            f"para usá-la. As máquinas em execução continuam na versão {VERSAO} até serem reiniciadas "
            f"(o que estiver em gerados/ é enviado ao reabrir)."
        )

    def _reiniciar(self) -> None:
        if self.ao_reiniciar:
            self.ao_reiniciar()

    def _abrir_pagina(self) -> None:
        if self.info.get("pagina"):
            QDesktopServices.openUrl(QUrl(self.info["pagina"]))
