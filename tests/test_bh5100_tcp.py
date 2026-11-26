"""
Testes do modo TCP da BH5100 (Mindray): ACK, gravação em gerados/ e queda.

Espelha tests/test_ca150.py::TestTCP e tests/test_urit8021.py::TestTCP. Sobe o
leitor em modo TCP numa porta efêmera (tcp_port: 0), conecta um socket cliente e
confere que o ACK^R01 volta, que o resultado vai para gerados/ e que a queda da
conexão encerra o programa depois de esvaziar gerados/.

Usam servidor HTTP e TCP locais; nada sai da máquina.
Rode na raiz do projeto: python3 -m unittest discover -s tests
"""

import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from bh5100.bh5100 import AnalisadorBH5100, SB, EB, CR  # noqa: E402
from shared import base_analisador  # noqa: E402

# ORU^R01 de hemograma emoldurado (SB + segmentos + EB + CR).
ORU_HEMOGRAMA = SB + CR.join([
    "MSH|^~\\&|BH5100|X|LIS||20261105||ORU^R01|1|P|2.3.1",
    "OBR|1||151020262929|",
    "OBX|1|NM|WBC^White||7.64|10^9/L|4-10|N|||F",
    "OBX|2|NM|RBC^Red||4.5|10^12/L|4-5|N|||F",
    "OBX|3|NM|HGB^Hemoglobin||13.2|g/dL|12-16|N|||F",
    "OBX|4|NM|PLT^Platelet||250|10^9/L|150-400|N|||F",
]) + CR + EB + CR


class ServidorFalso:
    """POST /api/integration/bh5100 do lab-apoio."""

    def __init__(self) -> None:
        self.requisicoes = []
        self.status_post = 200
        self.corpo_post = {"ok": True}
        dono = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                corpo = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                dono.requisicoes.append({"metodo": "POST", "caminho": self.path, "corpo": corpo})
                dados = json.dumps(dono.corpo_post).encode()
                self.send_response(dono.status_post)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}"
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def parar(self) -> None:
        self._http.shutdown()
        self._http.server_close()


class TestBH5100TCP(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="bh5100_tcp_")
        self.servidor = ServidorFalso()
        self.eventos = []
        self.encerrou = threading.Event()

        ambiente = {"HOME": self.tmp, "USERPROFILE": self.tmp, "NO_PROXY": "127.0.0.1"}
        for patch in (
            mock.patch.dict(os.environ, ambiente),
            mock.patch.object(sys, "argv", [os.path.join(self.tmp, "app.py")]),
            mock.patch.object(base_analisador, "start_cleanup_thread"),
            # Watchdog e envio rápidos; a queda conta depois de 0,3 s sem a máquina.
            mock.patch.object(base_analisador, "WATCHDOG_POLL_INTERVAL", 0.05),
            mock.patch.object(base_analisador, "CHECK_FILES_INTERVAL", 0.05),
            mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 0.3),
            mock.patch.object(base_analisador, "RETRY_INTERVAL", 0),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.servidor.parar()
        raiz = logging.getLogger()
        for handler in raiz.handlers[:]:
            handler.close()
            raiz.removeHandler(handler)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ───────────────────────────────────────────────

    def criar(self, **extras) -> AnalisadorBH5100:
        defaults = {
            "modo": "tcp",
            "com_port": "COM9",
            "baud_rate": 9600,
            "tcp_host": "127.0.0.1",
            "tcp_port": 0,
            "franchise_credential_id": "unidade-teste",
            "webhook_url": f"{self.servidor.url}/api/integration/bh5100?franchise_credential_id={{franchise_credential_id}}",
            "exam_images_url": "",
            "exam_images_machine_token": "",
            **extras,
        }
        return AnalisadorBH5100(
            machine_id="bh5100", machine_name="BH5100",
            config_defaults=defaults, health_port=0,
        )

    def _esperar(self, condicao, mensagem: str, segundos: float = 10) -> None:
        limite = time.monotonic() + segundos
        while not condicao():
            self.assertLess(time.monotonic(), limite, mensagem)
            time.sleep(0.02)

    def _receber_ack(self, cliente: socket.socket) -> str:
        recebido = b""
        limite = time.monotonic() + 10
        while (EB + CR).encode("utf-8") not in recebido:
            if time.monotonic() > limite:
                self.fail(f"ACK incompleto: {recebido!r}")
            recebido += cliente.recv(4096)
        return recebido.decode("utf-8")

    def _iniciar_tcp(self) -> AnalisadorBH5100:
        """Sobe o analisador em TCP com popup e encerramento gravados em self.eventos."""
        analisador = self.criar()
        analisador._show_popup = lambda titulo, mensagem: self.eventos.append(("popup", titulo, mensagem))

        def _sair(codigo):
            self.eventos.append(("exit", codigo))
            self.encerrou.set()

        patch = mock.patch.object(base_analisador.os, "_exit", side_effect=_sair)
        patch.start()
        self.addCleanup(patch.stop)

        threading.Thread(target=analisador.start, daemon=True).start()
        self._esperar(lambda: analisador._servidor_tcp is not None, "servidor TCP não abriu")
        self.addCleanup(analisador._servidor_tcp.close)
        # No fim do teste o watchdog fica parado: nada vaza para os próximos testes.
        self.addCleanup(setattr, analisador, "_serial_is_open", lambda: True)
        time.sleep(0.05)
        return analisador

    def _conectar(self, analisador: AnalisadorBH5100) -> socket.socket:
        cliente = socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10)
        self._esperar(lambda: analisador._conexao is not None, "máquina não conectou")
        return cliente

    # ── testes ────────────────────────────────────────────────

    def test_resultado_por_tcp_responde_ack_e_grava_em_gerados(self) -> None:
        analisador = self._iniciar_tcp()

        with socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10) as cliente:
            cliente.sendall(ORU_HEMOGRAMA.encode("utf-8"))
            ack = self._receber_ack(cliente)
            self.assertIn("ACK^R01", ack)
            self.assertIn("MSA|AA|1", ack)

        # O resultado foi salvo em gerados/ (ainda não enviado ou já enviado).
        self._esperar(
            lambda: len(os.listdir(analisador.GERADOS_DIR)) + len(os.listdir(analisador.ENVIADOS_DIR)) >= 1,
            "resultado não foi salvo",
        )

    def test_queda_envia_gerados_antes_do_popup_e_encerra(self) -> None:
        analisador = self._iniciar_tcp()
        cliente = self._conectar(analisador)

        cliente.close()
        self._esperar(lambda: not analisador._serial_is_open(), "queda não detectada")
        with open(os.path.join(analisador.GERADOS_DIR, "pendente.hl7"), "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(ORU_HEMOGRAMA)

        self.assertTrue(self.encerrou.wait(15), self.eventos)

        # O resultado foi enviado antes do popup.
        self.assertTrue([r for r in self.servidor.requisicoes if r["metodo"] == "POST"])
        self.assertIn("pendente.hl7", os.listdir(analisador.ENVIADOS_DIR))
        self.assertEqual([evento[0] for evento in self.eventos[:2]], ["popup", "exit"])
        _, titulo, mensagem = self.eventos[0]
        self.assertEqual(titulo, "Conexão perdida - BH5100")
        self.assertIn("foi perdida e não voltou", mensagem)
        self.assertIn(f"Conexão: TCP, porta {analisador.TCP_PORT}", mensagem)
        self.assertIn("enviados durante a queda: 1", mensagem)
        self.assertIn("ainda pendentes (serão enviados ao reabrir): 0", mensagem)

    def test_antes_da_primeira_conexao_nao_encerra(self) -> None:
        analisador = self._iniciar_tcp()

        time.sleep(1)

        self.assertTrue(analisador._serial_is_open())
        self.assertEqual(self.eventos, [])

    def test_conexao_substituida_nao_e_queda(self) -> None:
        with mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 5):
            analisador = self._iniciar_tcp()
            primeira = self._conectar(analisador)
            antiga = analisador._conexao
            segunda = socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10)
            self._esperar(lambda: analisador._conexao not in (None, antiga), "nova conexão não substituiu")
            time.sleep(0.3)

            self.assertTrue(analisador._serial_is_open())
            self.assertEqual(self.eventos, [])
            primeira.close()
            segunda.close()

    def test_porta_ocupada_avisa_sem_empilhar_popups(self) -> None:
        analisador = self.criar()
        liberar = threading.Event()
        with mock.patch.object(AnalisadorBH5100, "_show_popup", side_effect=lambda *a: liberar.wait(5)) as popup:
            analisador._popup_sem_travar("t", "primeira")
            self._esperar(lambda: popup.call_count == 1, "primeiro popup não abriu")
            analisador._popup_sem_travar("t", "segunda")
            time.sleep(0.1)
            self.assertEqual(popup.call_count, 1)
            liberar.set()
            self._esperar(lambda: not analisador._popup_aberto.is_set(), "popup não fechou")
            analisador._popup_sem_travar("t", "terceira")
            self._esperar(lambda: popup.call_count == 2, "popup não reabriu depois de fechado")


if __name__ == "__main__":
    unittest.main()
