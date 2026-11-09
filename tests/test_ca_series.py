"""
Testes do leitor e do simulador do URIT CA Series (HL7/MLLP).

As mensagens de referência são as da seção 4 do manual "CA Series Automatic
Chemistry Analyzer HL7 Interface Development Reference Manual" V1.01.

Rode na raiz do projeto: python -m unittest discover -s tests -v
"""

import json
import os
import sys
import threading
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from ca_series import COM_simulator as sim
from ca_series.ca_series import (
    AnalisadorCASeries, barcode_da_consulta, ler_msh, montar_ack_r01,
    montar_qck_q02, parse_hl7_to_dict, parse_hl7_to_txt,
)
try:
    from test_interfaceamento import BaseTeste  # discover -s tests
except ImportError:
    from tests.test_interfaceamento import BaseTeste

SB, EB, CR = "\x0b", "\x1c", "\r"


def mllp(*segmentos: str) -> str:
    return SB + CR.join(segmentos) + CR + EB + CR


# Manual, seção 4.1 (cliente envia resultado de amostra)
ORU_MANUAL = mllp(
    "MSH|^~\\&|URIT|CA-XXX|||20210701105518||ORU^R01|202107010001|p|2.3.1||||0||ANSI|||",
    "PID|1|OutPatient001|InPatient001|Bed001|Name001||10|M||||Year||||||||||||||N0001|1||||",
    "OBR|1|Barcode001|202107010001|URIT^CA-XXX|0||2021/07/01||||||ClinicalInfo001||"
    "Serum|Doctor001|Dep001|||||||||||||||||||||||||||||",
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

# Manual, seção 4.2 (CQ)
ORU_QC_MANUAL = mllp(
    "MSH|^~\\&|URIT|CA-XXX|||20210701161031||ORU^R01||p|2.3.1||||2||ANSI|||",
    "PID|||||||||||||||||||||||||||||||",
    "OBR|1|7|ALT|URIT^CA-XXX|||20210629092159||||1|1292UN||1292UN|20210522||"
    "Level 2|34.0000|3.0000|0.0000|||||T14,T2|||||||||||||||||||||",
)

# Manual, seção 4.3 (consulta)
QRY_MANUAL = mllp(
    "MSH|^~\\&|URIT|CA-XXX|||20210701110314||QRY^Q02|20210701110314|p|2.3.1||||||ANSI|||",
    "QRD|20210701110314|R|D|1|||RD|Barcode001|OTH|||T|",
    "QRF|CA-XXX|||||RCT|COR|ALL||",
)

ACK_Q03_MANUAL = mllp(
    "MSH|^~\\&|URIT|CA-XXX|||20210701110316||ACK^Q03|202107010001|p|2.3.1||||||ANSI|||",
    "MSA|AA|202107010001|Message accepted|||0|",
    "ERR|0|",
)


def segs(resposta: bytes):
    texto = resposta.decode("latin-1")
    assert texto.startswith(SB) and texto.endswith(EB + CR), repr(texto)
    return [s.split("|") for s in texto.strip(SB + EB + CR).split(CR) if s]


class TestParser(unittest.TestCase):
    def test_exemplo_do_manual(self) -> None:
        campos = parse_hl7_to_dict(ORU_MANUAL)
        self.assertEqual(campos["FileName"], "Barcode001")  # OBR.2
        self.assertEqual(campos["ALB"], "0.0")               # OBX.5, não OBX.13
        self.assertEqual(campos["TB"], "187.3")
        self.assertEqual(campos["IBIL"], "187.3")
        self.assertEqual(campos["A/G"], "10000000.00")
        por_exame = campos["results_by_exam"]
        self.assertEqual(set(por_exame), {"HEP"})
        self.assertEqual(len(por_exame["HEP"]), 11)

    def test_flag_anormal_vai_junto_do_valor(self) -> None:
        msg = ORU_MANUAL.replace("ALT|44286|U/L|0-50|N", "ALT|62|U/L|0-50|H")
        self.assertEqual(parse_hl7_to_dict(msg)["ALT"], "62H")

    def test_sem_barcode_usa_sample_id(self) -> None:
        msg = ORU_MANUAL.replace("OBR|1|Barcode001|", "OBR|1||")
        self.assertEqual(parse_hl7_to_dict(msg)["FileName"], "202107010001")

    def test_item_desconhecido_vai_para_quim_e_mapeamento_ignora_caixa(self) -> None:
        msg = ORU_MANUAL.replace("|ALB|", "|XYZ|").replace("|TP|", "|tp|")
        por_exame = parse_hl7_to_dict(msg)["results_by_exam"]
        self.assertEqual(por_exame["QUIM"], {"XYZ": "0.0"})
        self.assertIn("TP", por_exame["HEP"])  # nome normalizado para maiúsculas

    def test_txt(self) -> None:
        txt = parse_hl7_to_txt(ORU_MANUAL)
        self.assertIn("FileName: Barcode001", txt)
        self.assertIn("Rack/Tubo: N0001/1", txt)
        self.assertIn("ALB: 0.0 g/L [ref 35.0-55.0]", txt)


class TestRespostas(unittest.TestCase):
    def test_ack_r01_paciente(self) -> None:
        s = segs(montar_ack_r01(ORU_MANUAL))
        self.assertEqual(s[0][2:4], ["URIT", "CA-XXX"])
        self.assertEqual(s[0][8], "ACK^R01")
        self.assertEqual(s[0][9], "202107010001")
        self.assertEqual(s[0][10], "P")
        self.assertEqual(s[0][15], "0")
        self.assertEqual(s[0][17], "ANSI")
        self.assertEqual(s[1][:4], ["MSA", "AA", "202107010001", "Message accepted"])

    def test_ack_r01_qc_repete_msh16(self) -> None:
        s = segs(montar_ack_r01(ORU_QC_MANUAL))
        self.assertEqual(s[0][15], "2")
        self.assertEqual(s[1][2], "")

    def test_qck_q02(self) -> None:
        s = segs(montar_qck_q02(QRY_MANUAL))
        self.assertEqual([x[0] for x in s], ["MSH", "MSA", "ERR", "QAK"])
        self.assertEqual(s[0][8], "QCK^Q02")
        self.assertEqual(s[1][2], "20210701110314")
        self.assertEqual(s[3][:3], ["QAK", "SR", "NF"])
        self.assertEqual(segs(montar_qck_q02(QRY_MANUAL, encontrou=True))[3][2], "OK")

    def test_barcode_da_consulta(self) -> None:
        self.assertEqual(barcode_da_consulta(QRY_MANUAL), "Barcode001")
        self.assertEqual(ler_msh(QRY_MANUAL)["message_type"], "QRY^Q02")


class TestDeteccao(unittest.TestCase):
    def setUp(self) -> None:
        self.det = AnalisadorCASeries.detect_complete_message.__get__(object())

    def test_mensagem_em_pedacos(self) -> None:
        msg, resto = self.det(ORU_MANUAL[:50])
        self.assertIsNone(msg)
        msg, resto = self.det(resto + ORU_MANUAL[50:-1])  # sem o CR final
        self.assertIsNone(msg)
        msg, resto = self.det(resto + ORU_MANUAL[-1])
        self.assertEqual(msg, ORU_MANUAL)
        self.assertEqual(resto, "")

    def test_duas_mensagens_e_lixo_antes(self) -> None:
        msg, resto = self.det("lixo" + QRY_MANUAL + ORU_MANUAL)
        self.assertEqual(msg, QRY_MANUAL)
        msg, resto = self.det(resto)
        self.assertEqual(msg, ORU_MANUAL)
        self.assertEqual(resto, "")


class LadoSerial:
    """Uma ponta de um par serial em memória (estilo com0com)."""

    def __init__(self, entrada: bytearray, saida: bytearray, trava: threading.Lock) -> None:
        self._entrada, self._saida, self._trava = entrada, saida, trava
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        with self._trava:
            return len(self._entrada)

    def read(self, tamanho: int = 1) -> bytes:
        with self._trava:
            dados = bytes(self._entrada[:tamanho])
            del self._entrada[:tamanho]
            return dados

    def write(self, dados: bytes) -> int:
        with self._trava:
            self._saida.extend(dados)
        return len(dados)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        with self._trava:
            self._entrada.clear()

    def close(self) -> None:
        self.is_open = False


def par_serial():
    a_para_b, b_para_a, trava = bytearray(), bytearray(), threading.Lock()
    return LadoSerial(b_para_a, a_para_b, trava), LadoSerial(a_para_b, b_para_a, trava)


class TestSimuladorComLeitor(BaseTeste):
    """Simulador (analisador) ↔ leitor (LIS) pela serial, com webhook falso."""

    def setUp(self) -> None:
        super().setUp()
        self.lado_leitor, self.lado_sim = par_serial()
        patch = mock.patch("serial.Serial", lambda *a, **k: self.lado_leitor)
        patch.start()
        self.addCleanup(patch.stop)
        self.analisador = self.criar("ca_series")
        self.iniciar_conectado(self.analisador)

    def test_resultado_de_paciente_chega_ao_webhook(self) -> None:
        frame = sim.msg_paciente("151020262929")
        self.assertTrue(sim.enviar_resultado(self.lado_sim, frame, timeout=5))

        self.esperar(lambda: self.servidor.caminhos("/integration/ca_series"))
        self.esperar(lambda: len(self.servidor.caminhos("/integration/ca_series")) >= 4)
        payloads = [json.loads(r["corpo"]) for r in self.servidor.caminhos("/integration/ca_series")]
        por_exame = {p["ExamCode"]: p for p in payloads}
        self.assertEqual(set(por_exame), {"HEP", "GLI", "REN", "LIP"})
        self.assertTrue(all(p["FileName"] == "151020262929" for p in payloads))
        self.assertEqual(por_exame["HEP"]["ALT"], "62H")
        self.assertEqual(por_exame["HEP"]["ALB"], "42.3")
        self.assertEqual(por_exame["LIP"]["TC"], "6.12H")
        self.assertEqual(por_exame["GLI"]["GLU"], "5.42")

    def test_qc_recebe_ack_e_nao_vai_ao_webhook(self) -> None:
        self.assertTrue(sim.enviar_resultado(self.lado_sim, sim.msg_qc(), timeout=5))
        pasta_qc = os.path.join(self.analisador.ENVIADOS_DIR, "qc")
        self.esperar(lambda: os.path.isdir(pasta_qc) and os.listdir(pasta_qc))
        self.assertEqual(os.listdir(self.analisador.GERADOS_DIR), [])
        self.assertFalse(self.servidor.caminhos("/integration/ca_series"))

    def test_consulta_recebe_qck_nf(self) -> None:
        frame = sim.msg_consulta("151020262929")
        self.assertTrue(sim.fluxo_consulta(self.lado_sim, frame, timeout=5))
        self.assertEqual(os.listdir(self.analisador.GERADOS_DIR), [])

    def test_ack_q03_nao_gera_resposta(self) -> None:
        self.lado_sim.write(ACK_Q03_MANUAL.encode())
        self.assertIsNone(sim.esperar_frame(self.lado_sim, timeout=0.5))


class TestSimuladorDryRun(unittest.TestCase):
    def test_dry_run_nao_abre_serial(self) -> None:
        with mock.patch("serial.Serial", side_effect=AssertionError("abriu a serial")), \
                mock.patch("builtins.print"):
            for modo in ("paciente", "qc", "consulta"):
                self.assertEqual(
                    sim.main(["--modo", modo, "--etiqueta", "123", "--dry-run"]), 0
                )

    def test_json_de_apoio_define_porta_e_itens(self) -> None:
        import tempfile
        pasta = tempfile.mkdtemp()
        cfg = os.path.join(pasta, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"port": "COM42", "baud_rate": 19200, "etiqueta": "999",
                       "itens": [{"nome": "GGT", "valor": "80", "flag": "H"}]}, f)
        aberturas = []

        def serial_falsa(porta, baud, timeout=1):
            aberturas.append((porta, baud))
            raise __import__("serial").SerialException("sem porta")

        saida = []
        with mock.patch("serial.Serial", serial_falsa), \
                mock.patch("builtins.print", lambda *a, **k: saida.append(" ".join(map(str, a)))), \
                mock.patch("ca_series.COM_simulator.listar_portas"):
            codigo = sim.main(["--config", cfg, "--modo", "paciente", "--etiqueta", ""])
        self.assertEqual(codigo, 2)
        self.assertEqual(aberturas, [("COM42", 19200)])
        texto = "\n".join(saida)
        self.assertIn("OBR|1|999|", texto)
        self.assertIn("|GGT|80|", texto)
        self.assertIn("1 resultado(s)", texto)

    def test_json_invalido_usa_padrao(self) -> None:
        import tempfile
        cfg = os.path.join(tempfile.mkdtemp(), "cfg.json")
        with open(cfg, "w") as f:
            f.write("{ quebrado")
        config, situacao = sim.carregar_config(cfg)
        self.assertIn("ERRO", situacao)
        self.assertEqual(config["port"], "COM8")

    def test_exe_espera_enter_antes_de_fechar(self) -> None:
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", os.path.join(RAIZ, "nao_existe", "x.exe")), \
                mock.patch("builtins.print"), \
                mock.patch("builtins.input", return_value="") as entrada:
            sim.main(["--modo", "qc", "--dry-run"])
            entrada.assert_called_once()
            entrada.reset_mock()
            sim.main(["--modo", "qc", "--dry-run", "--sem-pausa"])
            entrada.assert_not_called()

    def test_mensagens_do_simulador_sao_lidas_pelo_parser(self) -> None:
        msg = sim.msg_paciente("ABC123").decode("latin-1")
        campos = parse_hl7_to_dict(msg)
        self.assertEqual(campos["FileName"], "ABC123")
        self.assertEqual(len(campos) - 3, len(sim.ITENS_PACIENTE))
        self.assertEqual(ler_msh(msg)["result_type"], "0")
        self.assertEqual(ler_msh(sim.msg_qc().decode())["result_type"], "2")


if __name__ == "__main__":
    unittest.main()
