"""
Testes da URIT-8021A: mensagens do "Urit HL7 LIS Interface Dev Reference
Manual", consulta por etiqueta no centralizador (GET /api/interfacing/tag),
envio dos resultados e modo TCP. A queda da serial está em
test_interfaceamento.py::TestQuedaCOM.

Usam um servidor HTTP local e serial/TCP locais; nada sai da máquina.
Rode na raiz do projeto: python -m unittest discover -s tests -v
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
from urllib.parse import parse_qs, urlparse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
# A pasta tem hífen (urit-8021): entra no path para o import.
sys.path.insert(0, os.path.join(RAIZ, "urit-8021"))

import urit8021  # noqa: E402
from urit8021 import AnalisadorURIT8021  # noqa: E402
from shared import base_analisador  # noqa: E402

SB, EB, CR = "\x0b", "\x1c", "\r"


def quadro(*linhas: str) -> str:
    return SB + "".join(linha + CR for linha in linhas) + EB + CR


# Exemplo do manual (7.1), com a etiqueta em OBR.2 (no manual vem "null").
ORU_PACIENTE = quadro(
    "MSH|^~\\&|urit|8030|||20120830103931||ORU^R01|1|P|2.3.1||||0||ASCII|||",
    "PID|1||||null||0|M|||||||||||||||||||||||",
    "OBR|1|Barcode001|201208290001|urit^8030|N||2012-08-29||||||||other0|||||||||||||||||||||||||||||||",
    "OBX|1|NM|1|ALB|11.8|g/l|35.0-55.0|N|||F||0.3279|2012-08-29||Server||",
    "OBX|2|NM|2|APOA_1|1.43|g/L|0.73-1.69|N|||F||0.3767|2012-08-29||Server||",
    "OBX|3|NM|3|LDL_C|4.47|mmol/L|2.07-3.10|N|||F||0.7833|2012-08-29||Server||",
    "OBX|4|NM|4|GGT|7939|U/L|0-50|N|||F||-7.0474|2012-08-29||Server||",
)

# Exatamente como no manual: sem código de barras (OBR.2 = "null").
ORU_SEM_ETIQUETA = ORU_PACIENTE.replace("OBR|1|Barcode001|", "OBR|1|null|")

# Consulta por etiqueta (manual 7.3 com QRD.8 preenchido).
QRY = quadro(
    "MSH|^~\\&|urit|8030|||20120830104844||QRY^Q02|20120830104844|P|2.3.1||||0||ASCII|||",
    "QRD|20120830104844|R|D|14|||RD|Barcode001|OTH|||T|",
    "QRF|8030|||||RCT|COR|ALL||",
)

# Consulta por período (exemplo do manual 7.3: QRD.8 vazio, QRF com datas).
QRY_PERIODO = quadro(
    "MSH|^~\\&|urit|8030|||20120830104844||QRY^Q02|1|P|2.3.1||||0||ASCII|||",
    "QRD|20120830104844|R|D|14|||RD||OTH|||T|",
    "QRF|8030|20120821000000|20120821235959|||RCT|COR|ALL||",
)

ACK_Q03 = quadro(
    "MSH|^~\\&|urit|8030|||20120830105821||ACK^Q03|1|P|2.3.1||||0||ASCII|||",
    "MSA|AA|20120830104844|Message accepted|||0|",
)

CONSULTA_OK = {
    "message": "Exames da etiqueta encontrados com sucesso!",
    "data": {
        "tag_id": "Barcode001",
        "patient": {"name": "João da Silva", "birth_date": "1985-01-01", "age": 40, "gender": "M"},
        "exams": [
            {"exam_code": "HEP", "materials": ["SORO"], "attributes": [
                {"code": "ALB", "name": "Albumina", "unit": "g/dL"},
                {"code": "GGT", "name": "Gama GT", "unit": "U/L"},
            ]},
            {"exam_code": "REN", "materials": ["SORO"], "attributes": [
                {"code": "CRE", "name": "Creatinina", "unit": "mg/dL"},
                {"code": "ALB", "name": "Albumina", "unit": "g/dL"},
            ]},
        ],
    },
}


def quadros(dados: bytes) -> list:
    """Divide bytes escritos para a máquina em mensagens (texto)."""
    texto = dados.decode("ascii")
    return [q for q in texto.split(SB) if q]


class ServidorFalso:
    """GET /api/interfacing/tag e POST do webhook do lab-apoio."""

    def __init__(self) -> None:
        self.requisicoes = []
        self.status_consulta = 200
        self.corpo_consulta = CONSULTA_OK
        self.status_post = 200
        self.corpo_post = {"message": "ok", "data": {"saved": [1]}}
        dono = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _responder(self, status, dados):
                corpo = json.dumps(dados).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def do_GET(self):
                dono.requisicoes.append({"metodo": "GET", "caminho": self.path})
                self._responder(dono.status_consulta, dono.corpo_consulta)

            def do_POST(self):
                corpo = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                dono.requisicoes.append({"metodo": "POST", "caminho": self.path, "corpo": json.loads(corpo)})
                self._responder(dono.status_post, dono.corpo_post)

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}"
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def parar(self) -> None:
        self._http.shutdown()
        self._http.server_close()


class SerialGravadora:
    def __init__(self) -> None:
        self.is_open = True
        self.escrito = b""

    def write(self, dados: bytes) -> int:
        self.escrito += dados
        return len(dados)


class BaseURIT8021(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="urit8021_")
        self.servidor = ServidorFalso()
        ambiente = {"HOME": self.tmp, "USERPROFILE": self.tmp, "NO_PROXY": "127.0.0.1"}
        for patch in (
            mock.patch.dict(os.environ, ambiente),
            mock.patch.object(sys, "argv", [os.path.join(self.tmp, "app.py")]),
            mock.patch.object(base_analisador, "start_cleanup_thread"),
            mock.patch.object(urit8021, "INTERVALO_DSR", 0),
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

    def criar(self, **extras) -> AnalisadorURIT8021:
        defaults = {
            "modo": "serial",
            "com_port": "COM9",
            "baud_rate": 9600,
            "tcp_host": "127.0.0.1",
            "tcp_port": 0,
            "franchise_credential_id": "unidade-teste",
            "webhook_url": f"{self.servidor.url}/api/integration/urit-8021?franchise_credential_id={{franchise_credential_id}}",
            "consult_url": f"{self.servidor.url}/api/interfacing/tag?franchise_credential_id={{franchise_credential_id}}",
            **extras,
        }
        return AnalisadorURIT8021(
            machine_id="urit8021", machine_name="URIT8021", config_defaults=defaults, health_port=0
        )

    def criar_serial(self, **extras):
        analisador = self.criar(**extras)
        analisador._serial = SerialGravadora()
        return analisador, analisador._serial

    def _log(self, analisador) -> str:
        for handler in logging.getLogger().handlers:
            handler.flush()
        with open(analisador.LOG_FILE, encoding="utf-8") as arquivo:
            return arquivo.read()


class TestMensagens(BaseURIT8021):
    def test_resultado_do_manual_vira_payload_com_virgula(self) -> None:
        payload = urit8021.parse_resultado(ORU_PACIENTE)

        self.assertEqual(payload["tag_id"], "Barcode001")
        self.assertEqual(payload["results"], [
            {"code": "ALB", "value": "11,8", "unit": "g/l"},
            {"code": "APOA_1", "value": "1,43", "unit": "g/L"},
            {"code": "LDL_C", "value": "4,47", "unit": "mmol/L"},
            {"code": "GGT", "value": "7939", "unit": "U/L"},
        ])

    def test_valor_com_ponto_vira_virgula_e_texto_fica_igual(self) -> None:
        self.assertEqual(urit8021.valor_com_virgula("0.54"), "0,54")
        self.assertEqual(urit8021.valor_com_virgula("-0.2"), "-0,2")
        self.assertEqual(urit8021.valor_com_virgula("<0.05"), "<0,05")
        self.assertEqual(urit8021.valor_com_virgula("6477"), "6477")
        self.assertEqual(urit8021.valor_com_virgula("Positivo"), "Positivo")
        self.assertEqual(urit8021.valor_com_virgula("1.2.3"), "1.2.3")

    def test_null_da_dll_vira_vazio(self) -> None:
        self.assertEqual(urit8021.parse_resultado(ORU_SEM_ETIQUETA)["tag_id"], "")

    def test_obx_com_base64_nao_vira_resultado(self) -> None:
        com_imagem = ORU_PACIENTE.replace(
            EB, "OBX|5|ED|5|CURVA|urit^Image^BMP^Base64^Qk0eAAAAAAAAABoAAAAMAAAA||||||F" + CR + EB
        )

        payload = urit8021.parse_resultado(com_imagem)

        self.assertEqual([r["code"] for r in payload["results"]], ["ALB", "APOA_1", "LDL_C", "GGT"])

    def test_detecta_mensagens_em_sequencia_e_descarta_lixo(self) -> None:
        analisador = self.criar()
        buffer = "lixo" + QRY + ORU_PACIENTE[:20]

        mensagem, resto = analisador.detect_complete_message(buffer)
        self.assertEqual(mensagem, QRY)
        self.assertEqual(analisador.detect_complete_message(resto), (None, ORU_PACIENTE[:20]))

    def test_resultado_grava_e_responde_ack_com_o_sample_id(self) -> None:
        analisador, porta = self.criar_serial()

        analisador._on_serial_message(ORU_PACIENTE)

        self.assertEqual(len(os.listdir(analisador.GERADOS_DIR)), 1)
        ack = quadros(porta.escrito)
        self.assertEqual(len(ack), 1)
        linhas = ack[0].rstrip(EB + CR).split(CR)
        self.assertTrue(linhas[0].startswith("MSH|^~\\&|urit|8030|||"))
        self.assertTrue(linhas[0].endswith("||ACK^R01|1|P|2.3.1||||0||ASCII|||"))
        # Manual 8.1: ACKmsgHL7("R01", sam->ID, ...): MSA.2 é o Sample ID (OBR.3).
        self.assertEqual(linhas[1], "MSA|AA|201208290001|Message accepted|||0|")
        self.assertTrue(ack[0].endswith(EB + CR))

    def test_resultado_vai_para_o_webhook(self) -> None:
        analisador = self.criar()
        caminho = os.path.join(analisador.GERADOS_DIR, "exame.hl7")
        with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(ORU_PACIENTE)

        payloads = analisador.process_file(caminho, "exame.hl7")
        self.assertTrue(analisador._enviar_payload_webhook(payloads[0], "exame.hl7", "Barcode001"))

        post = [r for r in self.servidor.requisicoes if r["metodo"] == "POST"][0]
        self.assertEqual(post["caminho"], "/api/integration/urit-8021?franchise_credential_id=unidade-teste")
        self.assertEqual(post["corpo"]["tag_id"], "Barcode001")
        self.assertEqual(post["corpo"]["results"][0], {"code": "ALB", "value": "11,8", "unit": "g/l"})

    def test_resultado_sem_etiqueta_nao_e_enviado(self) -> None:
        analisador = self.criar()
        caminho = os.path.join(analisador.GERADOS_DIR, "exame.hl7")
        with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(ORU_SEM_ETIQUETA)

        self.assertEqual(analisador.process_file(caminho, "exame.hl7"), [])
        self.assertIn("✗ exame.hl7: etiqueta (OBR.2) vazia na amostra 201208290001", self._log(analisador))


class TestConsulta(BaseURIT8021):
    def test_etiqueta_com_exames_responde_qck_e_dsr(self) -> None:
        analisador, porta = self.criar_serial()

        analisador._responder_consulta(QRY)

        get = self.servidor.requisicoes[0]
        query = parse_qs(urlparse(get["caminho"]).query)
        self.assertEqual(urlparse(get["caminho"]).path, "/api/interfacing/tag")
        self.assertEqual(query, {"franchise_credential_id": ["unidade-teste"], "tag_id": ["Barcode001"]})

        qck, dsr = quadros(porta.escrito)
        qck_linhas = qck.rstrip(EB + CR).split(CR)
        self.assertIn("||QCK^Q02|20120830104844|P|2.3.1||||0||ASCII|||", qck_linhas[0])
        self.assertEqual(qck_linhas[1:], [
            "MSA|AA|20120830104844|Message accepted|||0|", "ERR|0|", "QAK|SR|OK|",
        ])

        linhas = dsr.rstrip(EB + CR).split(CR)
        self.assertIn("||DSR^Q03|20120830104844|P|2.3.1||||0||ASCII|||", linhas[0])
        self.assertEqual(linhas[1:4], ["MSA|AA|20120830104844|Message accepted|||0|", "ERR|0|", "QAK|SR|OK|"])
        self.assertTrue(linhas[4].startswith("QRD|") and linhas[4].endswith("|R|D|14|||RD|Barcode001|OTH|||T|"))
        self.assertEqual(linhas[5], "QRF|8030|||||RCT|COR|ALL||")
        dsp = linhas[6:-1]
        self.assertEqual(dsp[0], "DSP|1|||||")
        self.assertEqual(dsp[1], "DSP|2||Barcode001|||")
        self.assertEqual(dsp[2], "DSP|3||Serum|||")
        # O manual declara ASCII: o nome vai sem acento.
        self.assertEqual(dsp[3], "DSP|4||Joao da Silva|||")
        self.assertEqual(dsp[4:7], ["DSP|5||M|||", "DSP|6||40|||", "DSP|7||0|||"])
        self.assertEqual(dsp[15], "DSP|16||N|||")
        self.assertEqual(dsp[16], "DSP|17||3|||")
        self.assertEqual(dsp[17:], ["DSP|18||1^ALB^^^^|||", "DSP|19||2^GGT^^^^|||", "DSP|20||3^CRE^^^^|||"])
        self.assertEqual(linhas[-1], "DSC|-1|")

    def test_consulta_por_periodo_responde_nf_sem_consultar(self) -> None:
        analisador, porta = self.criar_serial()

        analisador._responder_consulta(QRY_PERIODO)

        self.assertEqual(self.servidor.requisicoes, [])
        respostas = quadros(porta.escrito)
        self.assertEqual(len(respostas), 1)
        self.assertIn("QAK|SR|NF|", respostas[0])
        self.assertIn(
            "✗ Consulta sem etiqueta (por período 20120821000000 a 20120821235959): não suportada",
            self._log(analisador),
        )

    def test_etiqueta_inexistente_responde_nf_sem_dsr(self) -> None:
        analisador, porta = self.criar_serial()
        self.servidor.status_consulta = 404
        self.servidor.corpo_consulta = {"message": "Nenhum procedimento", "data": None}

        analisador._responder_consulta(QRY)

        respostas = quadros(porta.escrito)
        self.assertEqual(len(respostas), 1)
        self.assertIn("QAK|SR|NF|", respostas[0])

    def test_etiqueta_sem_exames_de_interface_responde_nf(self) -> None:
        analisador, porta = self.criar_serial()
        self.servidor.corpo_consulta = {"message": "", "data": {"tag_id": "Barcode001", "patient": {}, "exams": []}}

        analisador._responder_consulta(QRY)

        respostas = quadros(porta.escrito)
        self.assertEqual(len(respostas), 1)
        self.assertIn("QAK|SR|NF|", respostas[0])

    def test_lab_apoio_fora_do_ar_responde_ae(self) -> None:
        livre = socket.socket()
        livre.bind(("127.0.0.1", 0))
        porta_fechada = livre.getsockname()[1]
        livre.close()
        analisador, porta = self.criar_serial(consult_url=f"http://127.0.0.1:{porta_fechada}/api/interfacing/tag")

        analisador._responder_consulta(QRY)

        self.assertIn("QAK|SR|AE|", quadros(porta.escrito)[0])


class TestLog(BaseURIT8021):
    """Cada etapa, com sucesso ou falha, deixa uma linha no analisador_urit8021.log."""

    def test_abertura_registra_a_versao(self) -> None:
        from shared.versao import VERSAO

        analisador = self.criar()
        analisador._banner()

        self.assertIn(f"Analisador URIT8021 - Serviço de Integração - versão {VERSAO}", self._log(analisador))

    def test_health_check_informa_a_versao(self) -> None:
        from shared.health_server import HealthServer
        from shared.versao import VERSAO

        analisador = self.criar()
        analisador._health = HealthServer(port=0, machine_name="URIT8021")
        analisador._update_health_stats()

        self.assertEqual(analisador._health._get_stats_snapshot()["version"], VERSAO)

    def test_consulta_com_sucesso_registra_cada_etapa(self) -> None:
        analisador, _ = self.criar_serial()

        analisador._responder_consulta(QRY)
        analisador._on_serial_message(ACK_Q03)

        log = self._log(analisador)
        self.assertIn("Consulta da máquina para a etiqueta Barcode001.", log)
        self.assertIn("✓ Etiqueta Barcode001 encontrada: exames HEP, REN; itens por interface: ALB, GGT, CRE.", log)
        self.assertIn("✓ Etiqueta Barcode001: enviados 3 item(ns) à máquina: ALB, GGT, CRE.", log)
        self.assertIn("✓ Máquina confirmou a mensagem 20120830104844 (ACK^Q03, AA).", log)

    def test_resposta_que_nao_chega_na_maquina_e_registrada(self) -> None:
        analisador, porta = self.criar_serial()
        porta.is_open = False

        analisador._responder_consulta(QRY)

        log = self._log(analisador)
        self.assertIn("✗ Porta serial fechada: resposta descartada.", log)
        self.assertIn("✗ Etiqueta Barcode001: QAK|SR|OK não chegou à máquina.", log)
        self.assertNotIn("enviados 3 item(ns)", log)

    def test_maquina_recusando_a_lista_de_exames_e_registrada(self) -> None:
        analisador, _ = self.criar_serial()

        analisador._on_serial_message(ACK_Q03.replace("MSA|AA|", "MSA|AE|"))

        self.assertIn("✗ Máquina recusou a mensagem 20120830104844 (ACK^Q03, AE)", self._log(analisador))

    def test_resultado_recebido_e_ack_sao_registrados(self) -> None:
        analisador, _ = self.criar_serial()

        analisador._on_serial_message(ORU_PACIENTE)

        log = self._log(analisador)
        self.assertIn(
            "Resultado recebido da máquina: etiqueta Barcode001, amostra 201208290001, itens: ALB=11.8 g/l", log
        )
        self.assertIn("✓ ACK^R01 enviado à máquina.", log)

    def test_imagem_base64_e_registrada_como_ignorada(self) -> None:
        analisador = self.criar()
        com_imagem = ORU_PACIENTE.replace(EB, "OBX|5|ED|5|CURVA|urit^Image^BMP^Base64^Qk0e||||||F" + CR + EB)

        urit8021.parse_resultado(com_imagem)

        self.assertIn("✗ OBX com imagem/base64 ignorado(s) (Barcode001): ['CURVA']", self._log(analisador))

    def test_envio_registra_o_que_foi_gravado_e_o_que_ficou_de_fora(self) -> None:
        analisador = self.criar()
        self.servidor.corpo_post = {"message": "ok", "data": {
            "tag_id": "Barcode001",
            "saved": [{"exam_code": "HEP", "code": "ALB", "value": "11,8"}],
            "ignored": ["APOA_1"],
        }}

        payload = {"tag_id": "Barcode001", "results": [{"code": "ALB", "value": "11,8", "unit": "g/l"}]}
        self.assertTrue(analisador._enviar_payload_webhook(payload, "exame.hl7", "Barcode001"))

        log = self._log(analisador)
        self.assertIn("✓ Gravado no laudo: etiqueta Barcode001, exame HEP, ALB=11,8.", log)
        self.assertIn("Itens da etiqueta Barcode001 NÃO gravados: APOA_1.", log)

    def test_envio_recusado_registra_a_mensagem_do_lab_apoio(self) -> None:
        analisador = self.criar()
        self.servidor.status_post = 400
        self.servidor.corpo_post = {"message": "HEP: procedimento já liberado", "data": None}

        payload = {"tag_id": "Barcode001", "results": [{"code": "ALB", "value": "11,8", "unit": ""}]}
        self.assertFalse(analisador._enviar_payload_webhook(payload, "exame.hl7", "Barcode001"))

        self.assertIn("✗ lab-apoio respondeu 400 para a etiqueta Barcode001: HEP: procedimento já liberado", self._log(analisador))

    def test_erro_inesperado_vai_para_o_log(self) -> None:
        analisador, _ = self.criar_serial()

        with mock.patch.object(urit8021, "gerar_dsr", side_effect=RuntimeError("quebrou")):
            analisador._responder_consulta(QRY)

        log = self._log(analisador)
        self.assertIn("✗ Erro inesperado ao responder a consulta da etiqueta Barcode001.", log)
        self.assertIn("RuntimeError: quebrou", log)


class TestTCP(BaseURIT8021):
    def setUp(self) -> None:
        super().setUp()
        # Watchdog e envio rápidos; a queda conta depois de 0,3 s sem a máquina.
        for patch in (
            mock.patch.object(base_analisador, "WATCHDOG_POLL_INTERVAL", 0.05),
            mock.patch.object(base_analisador, "CHECK_FILES_INTERVAL", 0.05),
            mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 0.3),
            mock.patch.object(base_analisador, "RETRY_INTERVAL", 0),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.eventos = []
        self.encerrou = threading.Event()

    def _receber(self, cliente: socket.socket, quantidade: int) -> list:
        recebido = b""
        limite = time.monotonic() + 10
        while recebido.count(b"\x1c\r") < quantidade:
            if time.monotonic() > limite:
                self.fail(f"Resposta incompleta: {recebido!r}")
            recebido += cliente.recv(4096)
        return quadros(recebido)

    def _esperar(self, condicao, mensagem: str, segundos: float = 10) -> None:
        limite = time.monotonic() + segundos
        while not condicao():
            self.assertLess(time.monotonic(), limite, mensagem)
            time.sleep(0.02)

    def _iniciar_tcp(self) -> AnalisadorURIT8021:
        """Sobe o analisador em TCP com popup e encerramento gravados em self.eventos."""
        analisador = self.criar(modo="tcp")
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

    def _conectar(self, analisador: AnalisadorURIT8021) -> socket.socket:
        cliente = socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10)
        self._esperar(lambda: analisador._conexao is not None, "máquina não conectou")
        return cliente

    def test_consulta_e_resultado_por_tcp(self) -> None:
        analisador = self._iniciar_tcp()

        with socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10) as cliente:
            cliente.sendall(QRY.encode("ascii"))
            qck, dsr = self._receber(cliente, 2)
            self.assertIn("QAK|SR|OK|", qck)
            self.assertIn("DSP|17||3|||", dsr)

            cliente.sendall(ORU_PACIENTE.encode("ascii"))
            ack, = self._receber(cliente, 1)
            self.assertIn("MSA|AA|201208290001|", ack)

        self._esperar(
            lambda: os.listdir(analisador.GERADOS_DIR) or os.listdir(analisador.ENVIADOS_DIR),
            "resultado não foi gravado",
        )

    def test_queda_envia_gerados_antes_do_popup_e_encerra(self) -> None:
        analisador = self._iniciar_tcp()
        cliente = self._conectar(analisador)

        cliente.close()
        self._esperar(lambda: not analisador._serial_is_open(), "queda não detectada")
        with open(os.path.join(analisador.GERADOS_DIR, "pendente.hl7"), "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(ORU_PACIENTE)

        self.assertTrue(self.encerrou.wait(15), self.eventos)

        # O resultado foi enviado antes do popup.
        self.assertTrue([r for r in self.servidor.requisicoes if r["metodo"] == "POST"])
        self.assertIn("pendente.hl7", os.listdir(analisador.ENVIADOS_DIR))
        self.assertEqual([evento[0] for evento in self.eventos[:2]], ["popup", "exit"])
        _, titulo, mensagem = self.eventos[0]
        self.assertEqual(titulo, "Conexão perdida - URIT8021")
        self.assertIn("foi perdida e não voltou", mensagem)
        self.assertIn(f"Conexão: TCP, porta {analisador.TCP_PORT}", mensagem)
        self.assertIn("enviados durante a queda: 1", mensagem)
        self.assertIn("ainda pendentes (serão enviados ao reabrir): 0", mensagem)
        self.assertIn("urit8021.exe", mensagem)

    def test_maquina_que_reconecta_no_prazo_nao_encerra(self) -> None:
        with mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 2):
            analisador = self._iniciar_tcp()
            self._conectar(analisador).close()
            self._esperar(lambda: not analisador._serial_is_open(), "queda não detectada")

            cliente = self._conectar(analisador)
            time.sleep(2.5)
            cliente.close()

        self.assertEqual(self.eventos, [])

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
        analisador = self.criar(modo="tcp")
        liberar = threading.Event()
        with mock.patch.object(AnalisadorURIT8021, "_show_popup", side_effect=lambda *a: liberar.wait(5)) as popup:
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
