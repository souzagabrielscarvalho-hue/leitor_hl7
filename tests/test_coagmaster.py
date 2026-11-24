"""
Testes da Coagmaster: leitura do texto da máquina e um arquivo por teste.

A mesma etiqueta (tubo de citrato) recebe TP e TTPA; um não pode apagar o outro.
Rode na raiz do projeto: python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Coagmaster.coagmaster import parse_coagmaster_exam, split_exams_from_log
from test_interfaceamento import BaseTeste


def exame(numero: str, teste: str, linhas: str, etiqueta: str = "171120260591") -> str:
    return (
        "VIDA EXAME\n"
        f"({numero})\n"
        "17/11/2026\n"
        "CANAL 1\n"
        "09:30:00\n"
        "NOME: \n"
        f"Exame: {teste}\n"
        f"{linhas}\n"
        f"ID({etiqueta})\n"
        "OPERADOR (ADM)\n"
        "N. SERIE(123)\n"
        + "*" * 40 + "\n\n"
    )


TP = exame("0001", "TP", "Tempo de Protrombina\nTEMPO: 16,6 s\nRELAÇÃO: 1,25\n%\n81,4%\nINR 1,28\nCONTROLE 100%: 14,2s")
TTPA = exame("0002", "TTPA", "Tempo de Tromboplastina\nTEMPO: 28,5 s\nRELAÇÃO: 0,95\n%\n95,0%\nCONTROLE 100%: 30,0s")


# Exemplo de impressão do manual (MANUAL_DO_USUÁRIO_COAGMASTER 2.0, p. 24).
TP_MANUAL = (
    "NOME DO LAB\n"
    "**********************************************\n"
    "(0001)                CANAL  1\n"
    "18/01/2018             14:45:12\n"
    "\n"
    "NOME: Joao Pedro\n"
    "\n"
    "Exame:              TP\n"
    "          Tempo de Protrombina\n"
    "\n"
    "TEMPO:              16,6 s\n"
    "RELAÇÃO:             1,25\n"
    "%:                  81,4 %\n"
    "INR:                1,28\n"
    "CONTROLE 100%:       14,2 s\n"
    "\n"
    "ID(201801210001)\n"
    "OPERADOR (CARLOS)\n"
    "**********************************************\n"
    "\n"
)


class TestParser(unittest.TestCase):
    def test_formato_do_manual(self) -> None:
        dados = parse_coagmaster_exam(split_exams_from_log(TP_MANUAL)[0])

        self.assertEqual(dados["FileName"], "201801210001")
        self.assertEqual(dados["ExamType"], "TP")
        self.assertEqual(dados["TimeValue"], "16,6 s")
        self.assertEqual(dados["Percentage"], "81,4")
        self.assertEqual(dados["INR"], "1,28")
        self.assertEqual(dados["Control"], "14,2 s")
        self.assertEqual(dados["Operator"], "CARLOS")

    def test_tp_mantem_virgula_decimal(self) -> None:
        dados = parse_coagmaster_exam(split_exams_from_log(TP)[0])

        self.assertEqual(dados["FileName"], "171120260591")
        self.assertEqual(dados["ExamType"], "TP")
        self.assertEqual(dados["ExamCode"], "TAP")
        self.assertEqual(dados["TimeValue"], "16,6 s")
        self.assertEqual(dados["Percentage"], "81,4")
        self.assertEqual(dados["INR"], "1,28")
        self.assertEqual(dados["Status"], "SUCCESS")

    def test_ttpa_vai_para_kptt(self) -> None:
        dados = parse_coagmaster_exam(split_exams_from_log(TTPA)[0])

        self.assertEqual(dados["ExamCode"], "KPTT")
        self.assertEqual(dados["TimeValue"], "28,5 s")

    def test_tt_e_hep_nao_viram_outro_exame(self) -> None:
        tt = parse_coagmaster_exam(split_exams_from_log(exame("0003", "TT", "TEMPO: 18,0 s"))[0])
        hep = parse_coagmaster_exam(split_exams_from_log(exame("0004", "Hep", "TEMPO: 20,0 s"))[0])

        # Antes, TT ia para TCO (Tempo de Coagulação) e o resto caía em COAGU.
        self.assertEqual(tt["ExamCode"], "TT")
        self.assertEqual(hep["ExamCode"], "HEP")

    def test_sem_id_nao_usa_numero_do_exame_como_etiqueta(self) -> None:
        dados = parse_coagmaster_exam(split_exams_from_log(exame("0005", "TP", "TEMPO: 16,0 s", etiqueta=""))[0])

        self.assertEqual(dados["FileName"], "")


class TestArquivoPorTeste(BaseTeste):
    def arquivos(self, analisador) -> list:
        return sorted(os.listdir(analisador.GERADOS_DIR))

    def ler(self, analisador, nome: str) -> dict:
        with open(os.path.join(analisador.GERADOS_DIR, nome), encoding="utf-8") as arquivo:
            return json.load(arquivo)

    def test_tp_e_ttpa_da_mesma_etiqueta_geram_dois_arquivos(self) -> None:
        analisador = self.criar("coagmaster")

        analisador._on_serial_message(TP + TTPA)

        nomes = self.arquivos(analisador)
        self.assertEqual(nomes, ["171120260591_TP_0001.txt", "171120260591_TTPA_0002.txt"])
        self.assertEqual(self.ler(analisador, nomes[0])["FileName"], "171120260591")
        self.assertEqual(self.ler(analisador, nomes[1])["ExamType"], "TTPA")

    def test_duas_etiquetas_do_mesmo_exame_geram_envios_separados(self) -> None:
        analisador = self.criar("coagmaster")
        self.servidor.corpo_webhook = {"message": "ok", "data": {"saved": [{"code": "TAP"}], "ignored": []}}

        # Mesmo procedimento com 2 etiquetas: a máquina pode ler qualquer uma delas
        analisador._on_serial_message(
            exame("0010", "TP", "TEMPO: 16,6 s", etiqueta="171120260591")
            + exame("0011", "TP", "TEMPO: 17,0 s", etiqueta="171120260592")
        )
        self.assertEqual(
            self.arquivos(analisador), ["171120260591_TP_0010.txt", "171120260592_TP_0011.txt"]
        )

        self.enviar_uma_vez(analisador)

        enviados = sorted(
            (json.loads(r["corpo"])["FileName"], json.loads(r["corpo"])["TimeValue"])
            for r in self.servidor.requisicoes
        )
        self.assertEqual(enviados, [("171120260591", "16,6 s"), ("171120260592", "17,0 s")])
        self.assertEqual(
            sorted(os.listdir(analisador.ENVIADOS_DIR)),
            ["171120260591_TP_0010.txt", "171120260592_TP_0011.txt"],
        )

    def test_repeticao_nao_sobrescreve_arquivo_pendente(self) -> None:
        analisador = self.criar("coagmaster")

        analisador._on_serial_message(TP)
        analisador._on_serial_message(TP)

        self.assertEqual(
            self.arquivos(analisador), ["171120260591_TP_0001.txt", "171120260591_TP_0001_2.txt"]
        )

    def test_sem_id_e_falha_nao_geram_arquivo(self) -> None:
        analisador = self.criar("coagmaster")

        with self.assertLogs(level="WARNING") as logs:
            analisador._on_serial_message(
                exame("0006", "TP", "TEMPO: 16,0 s", etiqueta="")
                + exame("0007", "TP", "TEMPO:     FALHOU!")
            )

        self.assertEqual(self.arquivos(analisador), [])
        texto = "\n".join(logs.output)
        self.assertIn("sem ID/etiqueta", texto)
        self.assertIn("FALHOU na máquina", texto)

    def test_envio_registra_o_que_foi_gravado(self) -> None:
        analisador = self.criar("coagmaster")
        self.servidor.corpo_webhook = {
            "message": "ok",
            "data": {
                "tag_id": "171120260591",
                "saved": [{"exam_code": "TAP", "code": "TAP", "value": "16,6"}],
                "ignored": ["COAGU.Control"],
            },
        }
        analisador._on_serial_message(TP)

        with self.assertLogs(level="INFO") as logs:
            self.enviar_uma_vez(analisador)

        texto = "\n".join(logs.output)
        self.assertIn("✓ Gravado no laudo: etiqueta 171120260591, exame TAP, TAP=16,6.", texto)
        self.assertIn("NÃO gravados: COAGU.Control", texto)
        enviado = json.loads(self.servidor.requisicoes[-1]["corpo"])
        self.assertEqual(enviado["FileName"], "171120260591")
        self.assertEqual(enviado["ExamType"], "TP")

    def test_recusa_do_lab_apoio_vai_para_o_log(self) -> None:
        analisador = self.criar("coagmaster")
        self.servidor.status_webhook = 400
        self.servidor.corpo_webhook = {"message": "Teste 'TT' da Coagmaster não tem exame/atributo", "data": None}
        analisador._on_serial_message(exame("0008", "TT", "TEMPO: 18,0 s"))

        with self.assertLogs(level="ERROR") as logs:
            self.enviar_uma_vez(analisador)

        self.assertIn("✗ lab-apoio respondeu 400 para a etiqueta 171120260591", "\n".join(logs.output))
        self.assertEqual(os.listdir(analisador.REQUISICOES_NAO_ENVIADAS_DIR), ["171120260591_TT_0008.txt"])


if __name__ == "__main__":
    unittest.main()
