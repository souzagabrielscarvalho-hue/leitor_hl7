"""
Modo TCP da base (shared/conexao_tcp.py) nas máquinas que antes só tinham
serial: Coagmaster, MEK7300, PKL e VIDAS 1600. A BH5100 tem TCP próprio
(tests/test_bh5100_tcp.py), como a CA-150 e a URIT-8021A.

A máquina conecta no programa (servidor TCP local, porta escolhida pelo
sistema). Mesma regra da serial: queda sem reconexão em CONNECTION_DEADLINE
espera gerados/ ser enviada, mostra o popup e encerra; antes da primeira
conexão não é queda e uma conexão substituída por outra também não.
"""

import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_interfaceamento import HL7_VIDAS, MAQUINAS, BaseTeste  # noqa: E402

from shared import base_analisador  # noqa: E402
from unittest import mock  # noqa: E402

GENERICAS = ["coagmaster", "mek7300", "pkl", "vidas1600"]


class BaseTCP(BaseTeste):
    def criar_tcp(self, maquina: str, porta: int = 0):
        analisador = self.criar(maquina, modo="tcp", tcp_host="127.0.0.1", tcp_port=porta)
        self.addCleanup(lambda: analisador._porta_tcp and analisador._porta_tcp.encerrar())
        return analisador

    def iniciar_tcp(self, analisador) -> int:
        self.iniciar(analisador)
        self.esperar(lambda: analisador._porta_tcp is not None and analisador._porta_tcp.escutando.is_set())
        return analisador._porta_tcp.porta

    def conectar(self, porta: int) -> socket.socket:
        cliente = socket.create_connection(("127.0.0.1", porta), timeout=5)
        self.addCleanup(cliente.close)
        return cliente


class TestQuedaTCP(BaseTCP):
    def _verificar_queda(self, maquina: str) -> None:
        analisador = self.criar_tcp(maquina)
        _, _, _, nome, conteudo, _ = MAQUINAS[maquina]
        porta = self.iniciar_tcp(analisador)

        cliente = self.conectar(porta)
        self.esperar(analisador._conexao_ativa)
        cliente.close()
        self.esperar(lambda: not analisador._serial_is_open())
        self.gravar_exame(analisador, nome, conteudo)
        self.assertTrue(self.encerrou.wait(15), self.eventos)

        self.assertTrue(self.servidor.caminhos(f"/integration/{maquina}"))
        self.assertEqual([evento[0] for evento in self.eventos], ["popup", "exit"])
        mensagem = self.mensagem_popup()
        self.assertIn("foi perdida", mensagem)
        self.assertIn(f"Conexão: TCP, porta {porta} (máquina: 127.0.0.1:", mensagem)
        self.assertIn("enviados durante a queda: 1", mensagem)
        self.assertIn("ainda pendentes (serão enviados ao reabrir): 0", mensagem)
        # A porta foi solta antes do popup: dá para reabrir o programa.
        livre = socket.socket()
        self.addCleanup(livre.close)
        livre.bind(("127.0.0.1", porta))

    def test_queda_coagmaster(self) -> None:
        self._verificar_queda("coagmaster")

    def test_queda_mek7300(self) -> None:
        self._verificar_queda("mek7300")

    def test_queda_pkl(self) -> None:
        self._verificar_queda("pkl")

    def test_queda_vidas1600(self) -> None:
        self._verificar_queda("vidas1600")

    def test_antes_da_primeira_conexao_nao_e_queda(self) -> None:
        for maquina in ("coagmaster", "mek7300"):  # modo direto e listener
            with self.subTest(maquina=maquina):
                analisador = self.criar_tcp(maquina)
                self.iniciar_tcp(analisador)
                self._sleep_real(0.8)
                self.assertTrue(analisador._serial_is_open())
                self.assertFalse(analisador._conexao_ativa())
                self.assertEqual(self.eventos, [])
                analisador._porta_tcp.encerrar()

    def test_conexao_substituida_nao_e_queda(self) -> None:
        analisador = self.criar_tcp("vidas1600")
        porta = self.iniciar_tcp(analisador)
        primeira = self.conectar(porta)
        self.esperar(analisador._conexao_ativa)
        endereco = analisador._porta_tcp.endereco
        self.conectar(porta)
        self.esperar(lambda: analisador._porta_tcp.endereco != endereco)
        self._sleep_real(0.8)
        self.assertTrue(analisador._conexao_ativa())
        self.assertEqual(self.eventos, [])
        primeira.close()

    def test_reconexao_em_ate_5_min_nao_encerra(self) -> None:
        analisador = self.criar_tcp("mek7300")
        with mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 1.0):
            porta = self.iniciar_tcp(analisador)
            self.conectar(porta).close()
            self.esperar(lambda: analisador._porta_tcp.ja_conectou)
            self.esperar(lambda: not analisador._conexao_ativa())
            self.conectar(porta)
            self.esperar(analisador._conexao_ativa)
            self._sleep_real(1.3)
        self.assertEqual(self.eventos, [])

    def test_porta_ocupada_avisa_sem_encerrar(self) -> None:
        ocupada = socket.socket()
        self.addCleanup(ocupada.close)
        ocupada.bind(("127.0.0.1", 0))
        ocupada.listen(1)
        porta = ocupada.getsockname()[1]
        analisador = self.criar_tcp("coagmaster", porta=porta)
        self.iniciar(analisador)
        self.esperar(lambda: self.eventos)
        self._sleep_real(0.3)
        self.assertEqual(len(self.eventos), 1, self.eventos)
        self.assertEqual(self.eventos[0][0], "popup")
        self.assertIn("Porta ocupada", self.eventos[0][1])
        self.assertIn(str(porta), self.eventos[0][2])
        self.assertFalse(self.encerrou.is_set())


class TestRecebeTCP(BaseTCP):
    def _receber(self, maquina: str, mensagem: str) -> bytes:
        analisador = self.criar_tcp(maquina)
        porta = self.iniciar_tcp(analisador)
        cliente = self.conectar(porta)
        cliente.sendall(mensagem.encode("utf-8"))
        self.esperar(lambda: self.servidor.caminhos(f"/integration/{maquina}"))
        cliente.settimeout(5)
        return cliente.recv(4096)

    def test_vidas1600_recebe_e_responde_ack_por_tcp(self) -> None:
        ack = self._receber("vidas1600", HL7_VIDAS)
        self.assertIn(b"MSA|", ack)


if __name__ == "__main__":
    unittest.main()
