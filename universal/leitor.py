"""
Leitor universal: um .exe para todas as máquinas.

    leitor.exe                     abre o painel (janela própria em Qt):
                                   escolher, configurar, iniciar e acompanhar
                                   as máquinas
    leitor.exe --maquina ca150     roda a CA-150, igual ao ca150.exe

No modo máquina o comportamento é o do .exe próprio da máquina: config ao lado
do .exe (config_<maquina>.json), log em Desktop\\Analisador<MAQUINA>, gerados/,
watchdog e popup de queda. O painel abre cada máquina nesse modo, em processo
separado, e fechar o painel não para nenhuma delas.
"""

import argparse
import getpass
import logging
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from shared import maquinas  # noqa: E402
from shared.versao import VERSAO  # noqa: E402

# Código de saída do modo máquina (o painel explica cada um na tela).
SAIDA_MAQUINA_DESCONHECIDA = 2
SAIDA_ERRO_INESPERADO = 3


# ═══════════════════════════════════════════════════════════
# MODO MÁQUINA
# ═══════════════════════════════════════════════════════════

def rodar_maquina(maquina_id: str) -> int:
    # Aberta fora do painel (ex.: atalho): usa a pasta raiz escolhida no painel.
    from shared.painel import raiz
    raiz.aplicar_se_ausente(raiz.ler())
    try:
        modulo = maquinas.modulo(maquina_id)
    except KeyError as e:
        _stderr(f"✗ {e.args[0]}")
        return SAIDA_MAQUINA_DESCONHECIDA
    try:
        modulo.criar_analisador().start()
    except Exception:
        # Sem console no .exe: o erro vai para o log da máquina (se já existir)
        # e para a saída que o painel guarda em saida_<maquina>.txt.
        logging.exception(f"✗ {maquina_id} encerrada por erro inesperado")
        _stderr(traceback.format_exc())
        return SAIDA_ERRO_INESPERADO
    return 0


def _stderr(texto: str) -> None:
    try:
        if sys.stderr:
            sys.stderr.write(texto + "\n")
            sys.stderr.flush()
    except Exception:
        pass


def comando_maquina(maquina_id: str) -> List[str]:
    """Linha de comando que abre uma máquina (usada pelo painel)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--maquina", maquina_id]
    return [sys.executable, os.path.abspath(__file__), "--maquina", maquina_id]


# ═══════════════════════════════════════════════════════════
# MODO PAINEL
# ═══════════════════════════════════════════════════════════

def pasta_painel() -> str:
    """Log do painel: <raiz>/Painel, ou Desktop\\LeitorHL7 sem pasta raiz."""
    from shared.painel import raiz
    return raiz.pasta_painel(raiz.ler())


def recurso(*partes: str) -> str:
    """Arquivo embutido no .exe (PyInstaller) ou na pasta universal/ em desenvolvimento."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *partes)


def recurso_changelog() -> str:
    """CHANGELOG.md embutido no .exe (raiz do _MEIPASS) ou o da raiz do projeto."""
    base = getattr(sys, "_MEIPASS", None) or RAIZ
    return os.path.join(base, "CHANGELOG.md")


def nome_instancia() -> str:
    """Nome do canal local que garante um painel só por usuário do Windows."""
    try:
        usuario = getpass.getuser()
    except Exception:
        usuario = "usuario"
    return f"leitor-hl7-painel-{usuario}"


def _configurar_log_painel() -> None:
    pasta = pasta_painel()
    os.makedirs(pasta, exist_ok=True)
    handlers: List[logging.Handler] = [
        logging.FileHandler(os.path.join(pasta, "leitor_painel.log"), encoding="utf-8")
    ]
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers, force=True
    )


def _identificar_no_windows() -> None:
    """Barra de tarefas agrupa e mostra o ícone do leitor, não o do Python."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LeitorHL7.Painel")
        except Exception:
            pass


# Depois de atualizar, a versão nova espera o painel antigo fechar por até este tempo.
ESPERA_APOS_ATUALIZAR = 20


def rodar_painel(apos_atualizar: bool = False, ao_ligar: bool = False) -> int:
    # Pasta raiz (Configurações): vale para o painel e para as máquinas que ele abrir.
    from shared.painel import raiz
    raiz.aplicar(raiz.ler())
    _configurar_log_painel()
    _identificar_no_windows()

    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Leitor HL7")
    app.setApplicationVersion(VERSAO)

    # Um painel só: o segundo clique no .exe traz a janela do que já está aberto.
    canal = nome_instancia()

    def painel_aberto() -> Optional[QLocalSocket]:
        s = QLocalSocket()
        s.connectToServer(canal)
        return s if s.waitForConnected(500) else None

    socket = painel_aberto()
    if socket is not None and apos_atualizar:
        # Reaberto pela atualização: o painel antigo ainda está fechando.
        limite = time.time() + ESPERA_APOS_ATUALIZAR
        while socket is not None and time.time() < limite:
            socket.disconnectFromServer()
            time.sleep(0.5)
            socket = painel_aberto()
    if socket is not None and ao_ligar:
        socket.disconnectFromServer()
        logging.info("Ao ligar: o painel já estava aberto; nada a fazer.")
        return 0
    if socket is not None:
        socket.write(QByteArray(b"mostrar"))
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        logging.info("Painel já aberto: janela existente trazida para a frente.")
        return 0

    from shared.painel import tema
    from shared.painel.janela import JanelaPrincipal
    from shared.painel.nucleo import Painel
    from shared.painel.processos import Gerenciador

    logging.info("=" * 60)
    logging.info(f"Leitor HL7 - Painel - versão {VERSAO}")
    logging.info("=" * 60)

    icone_app = QIcon(recurso("icone", "leitor.ico"))
    logo = QIcon(recurso("icone", "leitor.svg")).pixmap(24, 24)

    painel = Painel(Gerenciador(comando_maquina, pasta_painel()))
    # Tema escolhido em Configurações > Tema (claro, escuro ou o do sistema).
    tema.definir_preferencia(painel.preferencia("tema"))
    tema.aplicar_paleta(app, tema.cores())
    logging.info(f"✓ Painel aberto | configs em {painel.pasta_config} | tema {tema.PREFERENCIA}")
    if apos_atualizar:
        logging.info(f"✓ Painel aberto depois da atualização para a versão {VERSAO}.")
    if ao_ligar:
        logging.info("Painel aberto com o Windows (--ao-ligar): iniciando as máquinas marcadas.")

    def reiniciar() -> None:
        # Fecha este painel e abre o .exe (já trocado) na versão nova.
        from shared.painel.janela_versao import reabrir_programa
        if reabrir_programa():
            servidor.close()
            app.quit()

    # Primeira abertura neste computador: senha e pasta raiz antes da janela.
    # Ao ligar com o Windows nunca pergunta (ninguém estaria olhando).
    if not ao_ligar and not raiz.existe():
        if painel.sessao.definida:
            from shared.painel.janela import DialogoPrimeiroUso, Ponte
            assistente = DialogoPrimeiroUso(None, painel, Ponte(), tema.cores())
            assistente.setWindowIcon(icone_app)
            if assistente.exec() != DialogoPrimeiroUso.DialogCode.Accepted:
                logging.info("Primeira configuração cancelada: painel fechado (será perguntado de novo).")
                logging.shutdown()
                return 0
            logging.info(f"✓ Primeira configuração concluída (pasta raiz: {painel.raiz() or 'padrão'}).")
        else:
            logging.warning("⚠ Primeira abertura sem senha mestra neste build: assistente pulado.")

    atual: Dict[str, Any] = {}

    def criar_janela() -> Any:
        return JanelaPrincipal(
            painel, icone_app, logo if not logo.isNull() else None,
            caminho_changelog=recurso_changelog(), ao_reiniciar=reiniciar, ao_trocar_tema=trocar_tema,
        )

    def trocar_tema(valor: str) -> None:
        # Ícones e estilos são gerados com as cores: recria a janela no mesmo lugar.
        tema.definir_preferencia(valor)
        tema.aplicar_paleta(app, tema.cores())
        antiga = atual["janela"]
        nova = criar_janela()
        nova.setGeometry(antiga.geometry())
        if antiga.isMaximized():
            nova.showMaximized()
        else:
            nova.show()
        if antiga.selecionada:
            nova.selecionada = antiga.selecionada
        atual["janela"] = nova
        antiga.descartar()
        logging.info(f"✓ Tema do painel trocado para {valor}.")

    atual["janela"] = criar_janela()
    app.setWindowIcon(icone_app)

    servidor = QLocalServer()
    QLocalServer.removeServer(canal)  # canal órfão de um painel que caiu
    if servidor.listen(canal):
        def ao_conectar() -> None:
            conexao = servidor.nextPendingConnection()
            if conexao is not None:
                conexao.waitForReadyRead(300)
                conexao.readAll()
                conexao.disconnectFromServer()
                atual["janela"].trazer_para_frente()
        servidor.newConnection.connect(ao_conectar)
    else:
        logging.warning(f"⚠ Canal de instância única indisponível: {servidor.errorString()}")

    if ao_ligar:
        # Minimizado: abre sozinho com o Windows e só inicia as máquinas marcadas.
        atual["janela"].showMinimized()
        atual["janela"].iniciar_ao_ligar()
    else:
        atual["janela"].show()
    codigo = app.exec()
    servidor.close()
    logging.shutdown()
    return codigo


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="leitor", description=f"Leitor HL7 universal {VERSAO}")
    parser.add_argument("--maquina", help=f"roda uma máquina: {', '.join(maquinas.POR_ID)}")
    parser.add_argument("--apos-atualizar", action="store_true", help=argparse.SUPPRESS)
    # Registrado em HKCU\\...\\Run por "Iniciar com o Windows".
    parser.add_argument("--ao-ligar", action="store_true", help="abre minimizado e inicia as máquinas marcadas")
    args = parser.parse_args(argv)

    if args.maquina:
        return rodar_maquina(args.maquina)
    return rodar_painel(apos_atualizar=args.apos_atualizar, ao_ligar=args.ao_ligar)


if __name__ == "__main__":
    sys.exit(main())
