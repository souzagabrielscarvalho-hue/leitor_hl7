"""
Testes da URIT CA-150: mensagens do manual HL7 V1.01, consulta por etiqueta no
centralizador (GET /api/interfacing/tag), envio dos resultados e modo TCP.

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

from ca150 import ca150
from ca150.ca150 import AnalisadorCA150
from shared import base_analisador

SB, EB, CR = "\x0b", "\x1c", "\r"


def quadro(*linhas: str) -> str:
    return SB + "".join(linha + CR for linha in linhas) + EB + CR


# Exemplos do manual (4.1, 4.2 e 4.3).
ORU_PACIENTE = quadro(
    "MSH|^~\\&|URIT|CA-XXX|||20210701105518||ORU^R01|202107010001|p|2.3.1||||0||ANSI|||",
    "PID|1|OutPatient001|InPatient001|Bed001|Name001||10|M||||Year||||||||||||||N0001|1||||",
    "OBR|1|Barcode001|202107010001|URIT^CA-XXX|0||2021/07/01||||||ClinicalInfo001||Serum|Doctor001|Dep001|||||||||||||||||||||||||||||",
    "OBX|1|NM|0|ALB|0.0|g/L|35.0-55.0|N|||F||-0.0577|2021/07/01||Server||",
    "OBX|2|NM|1|DB|0.0|umol/L|0.0-8.0|N|||F||-0.9028|2021/07/01||Server||",
    "OBX|3|NM|2|ALP|0|U/L|45-125|N|||F||-1.6202|2021/07/01||Server||",
    "OBX|4|NM|3|TB|187.3|umol/L|2.0-20.0|N|||F||0.9499|2021/07/01||Server||",
    "OBX|5|NM|4|TP|0.0|g/L|60.0-85.0|N|||F||-1.2106|2021/07/01||Server||",
    "OBX|6|NM|5|AST|0|U/L|0-40|N|||F||27.5919|2021/07/01||Server||",
    "OBX|7|NM|6|ALT|44286|U/L|0-50|N|||F||-12.7083|2021/07/01||Server||",
    "OBX|8|NM|7|A/G|10000000.00||0.00-10.00|N|||F|||2021/07/01||Server||",
    "OBX|9|NM|8|ALT/AST|10000000.0||1.0-1.2|N|||F|||2021/07/01||Server||",
    "OBX|10|NM|9|GLB|0.0|g/L|0.0-45.0|N|||F|||2021/07/01||Server||",
    "OBX|11|NM|10|IBIL|187.3|umol/L|0.0-20.0|N|||F|||2021/07/01||Server||",
)

ORU_CQ = quadro(
    "MSH|^~\\&|URIT|CA-XXX|||20210701161031||ORU^R01||p|2.3.1||||2||ANSI|||",
    "PID|||||||||||||||||||||||||||||||",
    "OBR|1|7|ALT|URIT^CA-XXX|||20210629092159||||1|1292UN||1292UN|20210522||Level 2|34.0000|3.0000|0.0000|||||T14,T2|||||||||||||||||||||",
)

QRY = quadro(
    "MSH|^~\\&|URIT|CA-XXX|||20210701110314||QRY^Q02|20210701110314|p|2.3.1||||||ANSI|||",
    "QRD|20210701110314|R|D|1|||RD|Barcode001|OTH|||T|",
    "QRF|CA-XXX|||||RCT|COR|ALL||",
)

CONSULTA_OK = {
    "message": "Exames da etiqueta encontrados com sucesso!",
    "data": {
        "tag_id": "Barcode001",
        "patient": {"name": "João da Silva", "birth_date": "1985-01-01", "age": 40, "gender": "M"},
        "exams": [
            {"exam_code": "HEP", "materials": ["SORO"], "attributes": [
                {"code": "ALB", "name": "Albumina", "unit": "g/dL"},
                {"code": "ALT", "name": "TGP", "unit": "U/L"},
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
    texto = dados.decode("cp1252")
    return [q for q in texto.split(SB) if q]


class ServidorFalso:
    """GET /api/interfacing/tag e POST /api/integration/ca-150 do lab-apoio."""

    def __init__(self) -> None:
        self.requisicoes = []
        self.status_consulta = 200
        self.corpo_consulta = CONSULTA_OK
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
                self._responder(200, {"message": "ok", "data": {"saved": [1]}})

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


class BaseCA150(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="ca150_")
        self.servidor = ServidorFalso()
        ambiente = {"HOME": self.tmp, "USERPROFILE": self.tmp, "NO_PROXY": "127.0.0.1"}
        for patch in (
            mock.patch.dict(os.environ, ambiente),
            mock.patch.object(sys, "argv", [os.path.join(self.tmp, "app.py")]),
            mock.patch.object(base_analisador, "start_cleanup_thread"),
            mock.patch.object(ca150, "INTERVALO_DSR", 0),
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

    def criar(self, **extras) -> AnalisadorCA150:
        defaults = {
            "modo": "serial",
            "com_port": "COM9",
            "baud_rate": 9600,
            "tcp_host": "127.0.0.1",
            "tcp_port": 0,
            "franchise_credential_id": "unidade-teste",
            "webhook_url": f"{self.servidor.url}/api/integration/ca-150?franchise_credential_id={{franchise_credential_id}}",
            "consult_url": f"{self.servidor.url}/api/interfacing/tag?franchise_credential_id={{franchise_credential_id}}",
            **extras,
        }
        return AnalisadorCA150(machine_id="ca150", machine_name="CA150", config_defaults=defaults, health_port=0)

    def criar_serial(self, **extras):
        analisador = self.criar(**extras)
        analisador._serial = SerialGravadora()
        return analisador, analisador._serial


class TestMensagens(BaseCA150):
    def test_resultado_do_manual_vira_payload(self) -> None:
        payload = ca150.parse_resultado(ORU_PACIENTE)

        self.assertEqual(payload["tag_id"], "Barcode001")
        codigos = [r["code"] for r in payload["results"]]
        self.assertEqual(codigos, ["ALB", "DB", "ALP", "TB", "TP", "AST", "ALT", "GLB", "IBIL"])
        self.assertEqual(payload["results"][0], {"code": "ALB", "value": "0.0", "unit": "g/L"})

    def test_detecta_mensagens_em_sequencia_e_descarta_lixo(self) -> None:
        analisador = self.criar()
        buffer = "lixo" + QRY + ORU_PACIENTE[:20]

        mensagem, resto = analisador.detect_complete_message(buffer)
        self.assertEqual(mensagem, QRY)
        self.assertEqual(analisador.detect_complete_message(resto), (None, ORU_PACIENTE[:20]))

    def test_resultado_de_paciente_grava_e_responde_ack(self) -> None:
        analisador, porta = self.criar_serial()

        analisador._on_serial_message(ORU_PACIENTE)

        self.assertEqual(len(os.listdir(analisador.GERADOS_DIR)), 1)
        ack = quadros(porta.escrito)
        self.assertEqual(len(ack), 1)
        self.assertIn("|ACK^R01|202107010001|P|2.3.1||||0||ANSI|||", ack[0])
        self.assertIn("MSA|AA|202107010001|Message accepted|||0|", ack[0])
        self.assertTrue(ack[0].endswith(EB + CR))

    def test_controle_de_qualidade_so_recebe_ack(self) -> None:
        analisador, porta = self.criar_serial()

        analisador._on_serial_message(ORU_CQ)

        self.assertEqual(os.listdir(analisador.GERADOS_DIR), [])
        self.assertIn("|ACK^R01||P|2.3.1||||2||ANSI|||", quadros(porta.escrito)[0])

    def test_resultado_vai_para_o_endpoint_da_ca150(self) -> None:
        analisador = self.criar()
        caminho = os.path.join(analisador.GERADOS_DIR, "exame.hl7")
        with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(ORU_PACIENTE)

        payloads = analisador.process_file(caminho, "exame.hl7")
        self.assertTrue(analisador._enviar_payload_webhook(payloads[0], "exame.hl7", "Barcode001"))

        post = [r for r in self.servidor.requisicoes if r["metodo"] == "POST"][0]
        self.assertEqual(post["caminho"], "/api/integration/ca-150?franchise_credential_id=unidade-teste")
        self.assertEqual(post["corpo"]["tag_id"], "Barcode001")
        self.assertEqual(len(post["corpo"]["results"]), 9)


class TestConsulta(BaseCA150):
    def test_etiqueta_com_exames_responde_qck_e_dsr(self) -> None:
        analisador, porta = self.criar_serial()

        analisador._responder_consulta(QRY)

        get = self.servidor.requisicoes[0]
        query = parse_qs(urlparse(get["caminho"]).query)
        self.assertEqual(urlparse(get["caminho"]).path, "/api/interfacing/tag")
        self.assertEqual(query, {"franchise_credential_id": ["unidade-teste"], "tag_id": ["Barcode001"]})

        qck, dsr = quadros(porta.escrito)
        self.assertIn("|QCK^Q02|20210701110314|P|", qck)
        self.assertIn("QAK|SR|OK|", qck)

        linhas = dsr.rstrip(EB + CR).split(CR)
        self.assertIn("|DSR^Q03|20210701110314|P|2.3.1||||0||ANSI|||", linhas[0])
        self.assertEqual(linhas[1:4], ["MSA|AA|20210701110314|Message accepted|||0|", "ERR|0|", "QAK|SR|OK|"])
        self.assertTrue(linhas[4].startswith("QRD|") and "|-1|||RD|Barcode001|OTH|||T|" in linhas[4])
        self.assertEqual(linhas[5], "QRF|CA-XXX|||||RCT|COR|ALL||")
        dsp = linhas[6:-1]
        self.assertEqual(dsp[1], "DSP|2||Barcode001|||")
        self.assertEqual(dsp[2], "DSP|3||Serum|||")
        self.assertEqual(dsp[3], "DSP|4||João da Silva|||")
        self.assertEqual(dsp[4:7], ["DSP|5||M|||", "DSP|6||40|||", "DSP|7||0|||"])
        self.assertEqual(dsp[15], "DSP|16||N|||")
        self.assertEqual(dsp[16], "DSP|17||3|||")
        self.assertEqual(dsp[17:], ["DSP|18||1^ALB^^^^|||", "DSP|19||2^ALT^^^^|||", "DSP|20||3^CRE^^^^|||"])
        self.assertEqual(linhas[-1], "DSC|-1|")

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


class TestTCP(BaseCA150):
    def _receber(self, cliente: socket.socket, quantidade: int) -> list:
        recebido = b""
        limite = time.monotonic() + 10
        while recebido.count(b"\x1c\r") < quantidade:
            if time.monotonic() > limite:
                self.fail(f"Resposta incompleta: {recebido!r}")
            recebido += cliente.recv(4096)
        return quadros(recebido)

    def test_consulta_e_resultado_por_tcp(self) -> None:
        analisador = self.criar(modo="tcp")
        threading.Thread(target=analisador.start, daemon=True).start()

        limite = time.monotonic() + 10
        while analisador._servidor_tcp is None:
            self.assertLess(time.monotonic(), limite, "servidor TCP não abriu")
            time.sleep(0.01)
        self.addCleanup(analisador._servidor_tcp.close)
        time.sleep(0.05)

        with socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10) as cliente:
            cliente.sendall(QRY.encode("cp1252"))
            qck, dsr = self._receber(cliente, 2)
            self.assertIn("QAK|SR|OK|", qck)
            self.assertIn("DSP|17||3|||", dsr)

            cliente.sendall(ORU_PACIENTE.encode("cp1252"))
            ack, = self._receber(cliente, 1)
            self.assertIn("MSA|AA|202107010001|", ack)

        self.assertEqual(len(os.listdir(analisador.GERADOS_DIR)) + len(os.listdir(analisador.ENVIADOS_DIR)) >= 1, True)


if __name__ == "__main__":
    unittest.main()
