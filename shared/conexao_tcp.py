"""
Conexão TCP com cara de porta serial, para qualquer máquina usar TCP no lugar
da COM sem mudar o protocolo.

O programa escuta em tcp_host:tcp_port e a máquina conecta no IP deste PC (o
mesmo papel do LIS na CA-150 e na URIT-8021A). PortaTCP expõe o que os laços
seriais da base usam do pyserial (is_open, in_waiting, read, write, close):
assim o loop simples, o SerialListener e o ASTM bidirecional do PKL funcionam
em TCP sem alteração.

- Uma conexão nova substitui a anterior (cabo de rede que cai sem aviso deixa
  a conexão velha pendurada e a máquina reconecta). Substituição não é queda.
- Antes da primeira conexão não é queda: a máquina pode estar desligada.
- Quando a máquina desconecta, in_waiting/read levantam ConexaoTCPEncerrada
  (uma serial.SerialException), como uma COM que caiu; o laço fecha e chama
  aguardar(), que espera a máquina conectar de novo.
"""

import logging
import socket
import threading
import time
from typing import Callable, Optional

import serial

# Intervalo entre tentativas de escutar na porta TCP (porta ocupada).
TCP_RETRY_INTERVAL = 10
# Espera máxima de read() por dados, como o timeout=0.1 da serial.
TIMEOUT_LEITURA = 0.1


class ConexaoTCPEncerrada(serial.SerialException):
    """A máquina fechou a conexão TCP (ou ela caiu): equivale à COM que caiu."""


class PortaTCP:
    def __init__(
        self,
        host: str,
        porta: int,
        ao_falhar_escuta: Optional[Callable[[OSError], None]] = None,
    ) -> None:
        self.host = host
        self.porta = porta
        self._ao_falhar_escuta = ao_falhar_escuta
        self._cond = threading.Condition()
        self._lock_escrita = threading.Lock()
        self._servidor: Optional[socket.socket] = None
        self._cliente: Optional[socket.socket] = None
        self._buffer = bytearray()
        self._encerrada = False
        self._thread: Optional[threading.Thread] = None
        self.ja_conectou = False
        self.endereco = ""
        self.motivo_queda = ""
        self.escutando = threading.Event()

    # ── interface parecida com a do pyserial ─────────────────

    @property
    def is_open(self) -> bool:
        return self._cliente is not None

    @property
    def in_waiting(self) -> int:
        with self._cond:
            if not self._buffer and self._cliente is None:
                raise ConexaoTCPEncerrada(self.motivo_queda or "máquina desconectada")
            return len(self._buffer)

    def read(self, tamanho: int = 1) -> bytes:
        with self._cond:
            if not self._buffer and self._cliente is not None:
                self._cond.wait(TIMEOUT_LEITURA)
            if not self._buffer and self._cliente is None:
                raise ConexaoTCPEncerrada(self.motivo_queda or "máquina desconectada")
            dados = bytes(self._buffer[:tamanho])
            del self._buffer[:tamanho]
            return dados

    def write(self, dados: bytes) -> int:
        cliente = self._cliente
        if cliente is None:
            raise ConexaoTCPEncerrada("sem máquina conectada por TCP")
        with self._lock_escrita:
            try:
                cliente.sendall(dados)
            except OSError as e:
                raise ConexaoTCPEncerrada(f"{type(e).__name__}: {e}") from e
        return len(dados)

    def close(self) -> None:
        """Fecha só a conexão com a máquina; a porta continua escutando."""
        with self._cond:
            cliente, self._cliente = self._cliente, None
            self._buffer.clear()
            self._cond.notify_all()
        _fechar(cliente)

    # ── servidor ─────────────────────────────────────────────

    def iniciar(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._servir, daemon=True, name=f"tcp-{self.porta}")
            self._thread.start()

    def encerrar(self) -> None:
        """Para de escutar e solta a porta (encerramento do programa)."""
        self._encerrada = True
        self.close()
        _fechar(self._servidor)
        with self._cond:
            self._cond.notify_all()

    def aguardar(self, parar: Callable[[], bool] = lambda: False) -> "PortaTCP":
        """Bloqueia até a máquina conectar. Levanta ConexaoTCPEncerrada se a porta for encerrada."""
        self.iniciar()
        avisado = False
        with self._cond:
            while self._cliente is None:
                if self._encerrada or parar():
                    raise ConexaoTCPEncerrada("porta TCP encerrada")
                if not avisado and self.escutando.is_set():
                    avisado = True
                    logging.info(f"Aguardando a máquina conectar em TCP {self.host}:{self.porta}.")
                self._cond.wait(0.5)
        return self

    def _escutar(self) -> Optional[socket.socket]:
        avisado = False
        while not self._encerrada:
            servidor = None
            try:
                servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                servidor.bind((self.host, self.porta))
                servidor.listen(1)
                servidor.settimeout(0.5)
                self.porta = servidor.getsockname()[1]
                logging.info(f"✓ Escutando em TCP {self.host}:{self.porta}.")
                return servidor
            except OSError as e:
                _fechar(servidor)
                logging.error(
                    f"✗ Não foi possível escutar em TCP {self.host}:{self.porta}: {e}. "
                    f"Tentando de novo em {TCP_RETRY_INTERVAL}s..."
                )
                if not avisado and self._ao_falhar_escuta:
                    avisado = True
                    try:
                        self._ao_falhar_escuta(e)
                    except Exception:
                        logging.exception("✗ Erro ao avisar porta TCP ocupada.")
                time.sleep(TCP_RETRY_INTERVAL)
        return None

    def _servir(self) -> None:
        try:
            self._servidor = self._escutar()
            if self._servidor is None:
                return
            self.escutando.set()
            with self._cond:
                self._cond.notify_all()
            while not self._encerrada:
                try:
                    cliente, endereco = self._servidor.accept()
                except socket.timeout:
                    # Ponto de parada do laço (o time.sleep é o que os testes interrompem).
                    time.sleep(0)
                    continue
                except OSError as e:
                    if self._encerrada:
                        break
                    logging.error(f"✗ Erro ao aceitar conexão TCP: {e}")
                    time.sleep(1)
                    continue
                self._aceitar(cliente, f"{endereco[0]}:{endereco[1]}")
        except Exception:
            logging.exception("✗ Erro inesperado no servidor TCP.")

    def _aceitar(self, cliente: socket.socket, endereco: str) -> None:
        cliente.settimeout(None)
        try:
            cliente.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        with self._cond:
            anterior, self._cliente = self._cliente, cliente
            self._buffer.clear()
            self.ja_conectou = True
            self.endereco = endereco
            self.motivo_queda = ""
            self._cond.notify_all()
        if anterior is not None:
            logging.info("Conexão TCP anterior substituída pela nova.")
            _fechar(anterior)
        logging.info(f"✓ Máquina conectada por TCP: {endereco}")
        threading.Thread(target=self._receber, args=(cliente, endereco), daemon=True, name="tcp-leitura").start()

    def _receber(self, cliente: socket.socket, endereco: str) -> None:
        motivo = "a máquina fechou a conexão"
        try:
            while True:
                dados = cliente.recv(4096)
                if not dados:
                    break
                with self._cond:
                    if self._cliente is not cliente:
                        return
                    self._buffer.extend(dados)
                    self._cond.notify_all()
        except OSError as e:
            motivo = f"{type(e).__name__}: {e}"
        except Exception as e:
            motivo = f"erro inesperado: {type(e).__name__}: {e}"
            logging.exception("✗ Erro inesperado na leitura TCP.")
        with self._cond:
            # Conexão substituída por uma nova (ou fechada pelo programa) não é queda.
            caiu = self._cliente is cliente
            if caiu:
                self._cliente = None
                self.motivo_queda = motivo
                self._cond.notify_all()
        _fechar(cliente)
        if caiu and not self._encerrada:
            logging.warning(f"⚠ Conexão TCP com a máquina perdida ({endereco}): {motivo}.")


def _fechar(sock: Optional[socket.socket]) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
