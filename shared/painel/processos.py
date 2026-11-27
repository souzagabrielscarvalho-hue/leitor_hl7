"""
Processos das máquinas: iniciar, parar e saber o estado.

Cada máquina roda em um processo próprio ("leitor.exe --maquina <id>"), que não
depende do painel: fechar o painel não para a leitura. O estado vem do GET
/health da máquina (127.0.0.1:<HEALTH_PORT>), que também responde para a
máquina aberta pelo .exe antigo dela.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Union

from shared import maquinas
from shared.config_loader import VARIAVEL_PASTA_CONFIG
from shared.painel import config

# Sem resposta do /health por este tempo depois de iniciar: algo deu errado.
TEMPO_PARA_RESPONDER = 30
TEMPO_PARA_PARAR = 10

# Sem proxy: no Windows, o proxy do sistema pode pegar até o 127.0.0.1.
_SEM_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

MOTIVO_SAIDA = {
    # _die_no_connection: queda da conexão, depois do popup.
    1: "Encerrada após perder a conexão com a máquina (veja o log).",
    2: "Não abriu: máquina desconhecida (veja a saída).",
    3: "Encerrada por erro inesperado (veja o log e a saída).",
}


def health(maquina_id: str, timeout: float = 0.8) -> Optional[Dict[str, Any]]:
    """GET /health da máquina, ou None se não responder ou for outra máquina."""
    url = f"http://127.0.0.1:{maquinas.health_port(maquina_id)}/health"
    try:
        with _SEM_PROXY.open(url, timeout=timeout) as resp:
            dados = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(dados, dict) or dados.get("machine") != maquinas.nome_maquina(maquina_id):
        return None
    return dados


def contar_pendentes(maquina_id: str) -> int:
    pasta = maquinas.pasta_gerados(maquina_id)
    try:
        return sum(1 for nome in os.listdir(pasta) if os.path.isfile(os.path.join(pasta, nome)))
    except OSError:
        return 0


def ultimas_linhas(caminho: str, quantidade: int = 300, limite_bytes: int = 512 * 1024) -> List[str]:
    """Últimas linhas de um log sem ler o arquivo inteiro."""
    try:
        with open(caminho, "rb") as f:
            f.seek(0, os.SEEK_END)
            tamanho = f.tell()
            f.seek(max(0, tamanho - limite_bytes))
            dados = f.read()
    except OSError:
        return []
    linhas = dados.decode("utf-8", errors="replace").splitlines()
    if tamanho > limite_bytes and linhas:
        linhas = linhas[1:]  # a primeira pode estar cortada no meio
    return linhas[-quantidade:]


def _encerrar_pid(pid: int) -> None:
    # No Windows, os.kill com SIGTERM chama TerminateProcess.
    os.kill(pid, signal.SIGTERM)


class Gerenciador:
    """
    Args:
        comando: monta a linha de comando que abre a máquina.
        pasta_saida: onde fica saida_<id>.txt (stdout/stderr do processo, para
            o erro que acontece antes do log da máquina existir).
        pasta_config: pasta dos config_<id>.json (padrão: ao lado do .exe), ou
            uma função id -> pasta quando cada máquina tem a sua.
    """

    def __init__(
        self,
        comando: Callable[[str], List[str]],
        pasta_saida: str,
        pasta_config: Union[None, str, Callable[[str], str]] = None,
    ) -> None:
        self._comando = comando
        self._pasta_saida = pasta_saida
        self.definir_pastas(pasta_config)
        self._lock = threading.Lock()
        self._processos: Dict[str, subprocess.Popen] = {}
        self._iniciado_em: Dict[str, float] = {}
        os.makedirs(pasta_saida, exist_ok=True)

    def definir_pastas(self, pasta_config: Union[None, str, Callable[[str], str]]) -> None:
        if callable(pasta_config):
            self._pasta = pasta_config
        else:
            fixa = pasta_config or config.app_dir()
            self._pasta = lambda _mid: fixa

    def arquivo_saida(self, maquina_id: str) -> str:
        return os.path.join(self._pasta_saida, f"saida_{maquina_id}.txt")

    # ── ESTADO ──────────────────────────────────────────────

    def estado(self, maquina_id: str) -> Dict[str, Any]:
        h = health(maquina_id)
        proc = self._processos.get(maquina_id)
        codigo = proc.poll() if proc else None
        info: Dict[str, Any] = {"estado": "parada", "pid": None, "health": h, "mensagem": ""}

        if h:
            info["estado"] = "rodando"
            info["pid"] = h.get("pid")
            if info["pid"] is None:
                info["mensagem"] = "Aberta por um .exe antigo (sem PID): pare pelo próprio programa."
        elif proc is not None and codigo is None:
            info["pid"] = proc.pid
            if time.time() - self._iniciado_em.get(maquina_id, 0) < TEMPO_PARA_RESPONDER:
                info["estado"] = "iniciando"
            else:
                info["estado"] = "sem_resposta"
                info["mensagem"] = (
                    f"O processo existe, mas o health (porta {maquinas.health_port(maquina_id)}) "
                    f"não responde. Veja o log."
                )
        elif proc is not None:
            info["codigo_saida"] = codigo
            info["mensagem"] = MOTIVO_SAIDA.get(codigo, f"Encerrada com código {codigo}.")
        return info

    # ── INICIAR / PARAR ─────────────────────────────────────

    def iniciar(self, maquina_id: str) -> Dict[str, Any]:
        titulo = maquinas.obter(maquina_id).titulo
        with self._lock:
            pasta = self._pasta(maquina_id)
            ok, motivo = config.pronta(maquina_id, pasta)
            if not ok:
                logging.warning(f"✗ {titulo} não iniciada: {motivo}")
                return {"ok": False, "mensagem": motivo}

            atual = self.estado(maquina_id)["estado"]
            if atual in ("rodando", "iniciando", "sem_resposta"):
                motivo = "Ela já está aberta."
                logging.warning(f"✗ {titulo} não iniciada: {motivo}")
                return {"ok": False, "mensagem": motivo}

            comando = self._comando(maquina_id)
            flags = 0
            if sys.platform.startswith("win"):
                # Sem janela de console e fora do grupo do painel: fechar o
                # painel não derruba a máquina.
                flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                with open(self.arquivo_saida(maquina_id), "w", encoding="utf-8") as saida:
                    proc = subprocess.Popen(
                        comando,
                        stdin=subprocess.DEVNULL,
                        stdout=saida,
                        stderr=subprocess.STDOUT,
                        creationflags=flags,
                        close_fds=True,
                        # A máquina lê o config da pasta escolhida para ela.
                        env={**os.environ, VARIAVEL_PASTA_CONFIG: pasta},
                    )
            except OSError as e:
                logging.error(f"✗ {titulo} não iniciada: {e} (comando: {comando})")
                return {"ok": False, "mensagem": f"Não foi possível abrir o processo: {e}"}

            self._processos[maquina_id] = proc
            self._iniciado_em[maquina_id] = time.time()
            logging.info(f"✓ {titulo} iniciada (PID {proc.pid}, config em {pasta}).")
            return {"ok": True, "pid": proc.pid}

    def parar(self, maquina_id: str) -> Dict[str, Any]:
        titulo = maquinas.obter(maquina_id).titulo
        with self._lock:
            proc = self._processos.get(maquina_id)
            if proc is not None and proc.poll() is None:
                pid = proc.pid
                proc.terminate()
                try:
                    proc.wait(timeout=TEMPO_PARA_PARAR)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=TEMPO_PARA_PARAR)
            else:
                h = health(maquina_id)
                pid = h.get("pid") if h else None
                if not h:
                    motivo = "Ela não está aberta."
                    logging.warning(f"✗ {titulo} não parada: {motivo}")
                    return {"ok": False, "mensagem": motivo}
                if not isinstance(pid, int):
                    motivo = "Aberta por um .exe antigo (sem PID): feche pelo Gerenciador de Tarefas."
                    logging.warning(f"✗ {titulo} não parada: {motivo}")
                    return {"ok": False, "mensagem": motivo}
                try:
                    _encerrar_pid(pid)
                except OSError as e:
                    logging.error(f"✗ {titulo} não parada (PID {pid}): {e}")
                    return {"ok": False, "mensagem": f"Não foi possível encerrar o PID {pid}: {e}"}

            # Espera o health parar de responder (a porta da máquina é solta).
            limite = time.time() + TEMPO_PARA_PARAR
            while health(maquina_id, timeout=0.3) and time.time() < limite:
                time.sleep(0.2)
            if health(maquina_id, timeout=0.3):
                logging.error(f"✗ {titulo} (PID {pid}) ainda responde depois de encerrada.")
                return {"ok": False, "mensagem": "O processo foi encerrado, mas o health ainda responde."}

            self._processos.pop(maquina_id, None)
            pendentes = contar_pendentes(maquina_id)
            logging.info(
                f"✓ {titulo} parada (PID {pid}). {pendentes} exame(s) em gerados/ "
                f"serão enviados quando ela for aberta de novo."
            )
            return {"ok": True, "pendentes": pendentes}
