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


class TestDepara(BaseCA150):
    """De-para de códigos (config depara_codigos), nos dois sentidos."""

    # Consulta: o Vida manda o código do atributo (HDL); a máquina só conhece HDLE.
    CONSULTA_HDL = {
        "message": "ok",
        "data": {
            "tag_id": "Barcode001",
            "patient": {"name": "Maria", "age": 30, "gender": "F"},
            "exams": [
                {"exam_code": "COL", "materials": ["SORO"], "attributes": [
                    {"code": "HDL", "name": "HDL Colesterol", "unit": "mg/dL"},
                    {"code": "URE", "name": "Ureia", "unit": "mg/dL"},
                ]},
            ],
        },
    }

    # Resultado: a máquina manda o código dela (HDLE); o URE é igual nos dois lados.
    ORU_HDLE = quadro(
        "MSH|^~\\&|URIT|CA-XXX|||20210701105518||ORU^R01|202107010001|p|2.3.1||||0||ANSI|||",
        "OBR|1|Barcode001|202107010001|URIT^CA-XXX|0||2021/07/01||||||||Serum||||||||||||||||||||",
        "OBX|1|NM|0|HDLE|45.0|mg/dL|40-60|N|||F||0.5|2021/07/01||Server||",
        "OBX|2|NM|1|URE|30.0|mg/dL|10-50|N|||F||0.4|2021/07/01||Server||",
    )

    def test_consulta_traduz_codigo_do_vida_para_o_da_maquina(self) -> None:
        self.servidor.corpo_consulta = self.CONSULTA_HDL
        analisador, porta = self.criar_serial(depara_codigos={"HDLE": "HDL"})

        analisador._responder_consulta(QRY)

        _, dsr = quadros(porta.escrito)
        dsp = dsr.rstrip(EB + CR).split(CR)[6:-1]
        # HDL (Vida) vira HDLE (máquina); URE é igual nos dois lados e não muda.
        self.assertEqual(dsp[-2:], ["DSP|18||1^HDLE^^^^|||", "DSP|19||2^URE^^^^|||"])

    def test_resultado_traduz_codigo_da_maquina_para_o_do_vida(self) -> None:
        # A tradução do envio é da base (aplicada no loop de envio).
        analisador = self.criar(depara_codigos={"HDLE": "HDL"})
        caminho = os.path.join(analisador.GERADOS_DIR, "exame.hl7")
        with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(self.ORU_HDLE)

        payloads = analisador.process_file(caminho, "exame.hl7")
        analisador.aplicar_depara_resultado(payloads[0])

        codigos = [r["code"] for r in payloads[0]["results"]]
        self.assertEqual(codigos, ["HDL", "URE"])

    def test_sem_depara_mantem_os_codigos(self) -> None:
        analisador = self.criar()
        caminho = os.path.join(analisador.GERADOS_DIR, "exame.hl7")
        with open(caminho, "w", encoding="utf-8", newline="") as arquivo:
            arquivo.write(self.ORU_HDLE)

        payloads = analisador.process_file(caminho, "exame.hl7")
        analisador.aplicar_depara_resultado(payloads[0])

        self.assertEqual([r["code"] for r in payloads[0]["results"]], ["HDLE", "URE"])

    def test_depara_cobre_o_campo_test_e_nao_toca_exam_code(self) -> None:
        # Formato do PKL: results com exam_code + test (o código do atributo é test).
        analisador = self.criar(depara_codigos={"FIB2": "FIB"})
        payload = {"tag_id": "x", "results": [{"exam_code": "COAG", "test": "FIB2", "value": "300"}]}

        analisador.aplicar_depara_resultado(payload)

        self.assertEqual(payload["results"][0]["test"], "FIB")
        self.assertEqual(payload["results"][0]["exam_code"], "COAG")


ACK_Q03 = quadro(
    "MSH|^~\\&|URIT|CA-XXX|||20210701110316||ACK^Q03|20210701110314|p|2.3.1||||0||ANSI|||",
    "MSA|AA|20210701110314|Message accepted|||0|",
)


class TestLog(BaseCA150):
    """Cada etapa, com sucesso ou falha, deixa uma linha no analisador_ca150.log."""

    def _log(self) -> str:
        for handler in logging.getLogger().handlers:
            handler.flush()
        with open(self.analisador.LOG_FILE, encoding="utf-8") as arquivo:
            return arquivo.read()

    def test_abertura_registra_a_versao(self) -> None:
        from shared.versao import VERSAO

        self.analisador = self.criar()
        self.analisador._banner()

        self.assertIn(f"Analisador CA150 - Serviço de Integração - versão {VERSAO}", self._log())

    def test_health_check_informa_a_versao(self) -> None:
        from shared.health_server import HealthServer
        from shared.versao import VERSAO

        self.analisador = self.criar()
        self.analisador._health = HealthServer(port=0, machine_name="CA150")
        self.analisador._update_health_stats()

        self.assertEqual(self.analisador._health._get_stats_snapshot()["version"], VERSAO)

    def test_consulta_com_sucesso_registra_cada_etapa(self) -> None:
        self.analisador, _ = self.criar_serial()

        self.analisador._responder_consulta(QRY)
        self.analisador._on_serial_message(ACK_Q03)

        log = self._log()
        self.assertIn("Consulta da máquina para a etiqueta Barcode001.", log)
        self.assertIn("✓ Etiqueta Barcode001 encontrada: exames HEP, REN; itens por interface: ALB, ALT, CRE.", log)
        self.assertIn("✓ Etiqueta Barcode001: enviados 3 item(ns) à máquina: ALB, ALT, CRE.", log)
        self.assertIn("✓ Máquina confirmou a mensagem 20210701110314 (ACK^Q03, AA).", log)

    def test_consulta_sem_exames_de_interface_registra_o_motivo(self) -> None:
        self.analisador, _ = self.criar_serial()
        self.servidor.corpo_consulta = {"message": "", "data": {"tag_id": "Barcode001", "patient": {}, "exams": []}}

        self.analisador._responder_consulta(QRY)

        log = self._log()
        self.assertIn("Etiqueta Barcode001 encontrada, mas sem exames pendentes por interfaceamento", log)
        self.assertIn("Etiqueta Barcode001: respondido QAK|SR|NF (máquina não recebe exames).", log)

    def test_resposta_que_nao_chega_na_maquina_e_registrada(self) -> None:
        self.analisador, porta = self.criar_serial()
        porta.is_open = False

        self.analisador._responder_consulta(QRY)

        log = self._log()
        self.assertIn("✗ Porta serial fechada: resposta descartada.", log)
        self.assertIn("✗ Etiqueta Barcode001: QAK|SR|OK não chegou à máquina.", log)
        self.assertNotIn("enviados 3 item(ns)", log)

    def test_maquina_recusando_a_lista_de_exames_e_registrada(self) -> None:
        self.analisador, _ = self.criar_serial()

        self.analisador._on_serial_message(ACK_Q03.replace("MSA|AA|", "MSA|AE|"))

        self.assertIn("✗ Máquina recusou a mensagem 20210701110314 (ACK^Q03, AE)", self._log())

    def test_resultado_recebido_e_ack_sao_registrados(self) -> None:
        self.analisador, _ = self.criar_serial()

        self.analisador._on_serial_message(ORU_PACIENTE)

        log = self._log()
        self.assertIn("Resultado recebido da máquina: etiqueta Barcode001, itens: ALB=0.0 g/L", log)
        self.assertIn("✓ ACK^R01 enviado à máquina.", log)

    def test_envio_registra_o_que_foi_gravado_e_o_que_ficou_de_fora(self) -> None:
        self.analisador = self.criar()
        self.servidor.corpo_post = {"message": "ok", "data": {
            "tag_id": "Barcode001",
            "saved": [{"exam_code": "HDL", "code": "HDL", "value": "45"}],
            "ignored": ["GLI"],
        }}

        payload = {"tag_id": "Barcode001", "results": [{"code": "HDL", "value": "45", "unit": "mg/dL"}]}
        self.assertTrue(self.analisador._enviar_payload_webhook(payload, "exame.hl7", "Barcode001"))

        log = self._log()
        self.assertIn("✓ Gravado no laudo: etiqueta Barcode001, exame HDL, HDL=45.", log)
        self.assertIn("Itens da etiqueta Barcode001 NÃO gravados: GLI.", log)

    def test_envio_recusado_registra_a_mensagem_do_lab_apoio(self) -> None:
        self.analisador = self.criar()
        self.servidor.status_post = 400
        self.servidor.corpo_post = {"message": "HDL: procedimento já liberado", "data": None}

        payload = {"tag_id": "Barcode001", "results": [{"code": "HDL", "value": "45", "unit": ""}]}
        self.assertFalse(self.analisador._enviar_payload_webhook(payload, "exame.hl7", "Barcode001"))

        self.assertIn("✗ lab-apoio respondeu 400 para a etiqueta Barcode001: HDL: procedimento já liberado", self._log())

    def test_erro_inesperado_vai_para_o_log(self) -> None:
        self.analisador, _ = self.criar_serial()

        with mock.patch.object(ca150, "gerar_dsr", side_effect=RuntimeError("quebrou")):
            self.analisador._responder_consulta(QRY)

        log = self._log()
        self.assertIn("✗ Erro inesperado ao responder a consulta da etiqueta Barcode001.", log)
        self.assertIn("RuntimeError: quebrou", log)


class TestTCP(BaseCA150):
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

    def _iniciar_tcp(self) -> AnalisadorCA150:
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

    def _conectar(self, analisador: AnalisadorCA150) -> socket.socket:
        cliente = socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10)
        self._esperar(lambda: analisador._conexao is not None, "máquina não conectou")
        return cliente

    def test_consulta_e_resultado_por_tcp(self) -> None:
        analisador = self._iniciar_tcp()

        with socket.create_connection(("127.0.0.1", analisador.TCP_PORT), timeout=10) as cliente:
            cliente.sendall(QRY.encode("cp1252"))
            qck, dsr = self._receber(cliente, 2)
            self.assertIn("QAK|SR|OK|", qck)
            self.assertIn("DSP|17||3|||", dsr)

            cliente.sendall(ORU_PACIENTE.encode("cp1252"))
            ack, = self._receber(cliente, 1)
            self.assertIn("MSA|AA|202107010001|", ack)

        self.assertEqual(len(os.listdir(analisador.GERADOS_DIR)) + len(os.listdir(analisador.ENVIADOS_DIR)) >= 1, True)

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
        self.assertEqual(titulo, "Conexão perdida - CA150")
        self.assertIn("foi perdida e não voltou", mensagem)
        self.assertIn(f"Conexão: TCP, porta {analisador.TCP_PORT}", mensagem)
        self.assertIn("enviados durante a queda: 1", mensagem)
        self.assertIn("ainda pendentes (serão enviados ao reabrir): 0", mensagem)

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
        with mock.patch.object(AnalisadorCA150, "_show_popup", side_effect=lambda *a: liberar.wait(5)) as popup:
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
