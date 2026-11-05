"""
Testes do interfaceamento: token só nas máquinas com imagem e queda da COM.

Usam serial simulada e um servidor HTTP local; nada sai da máquina.
Rode na raiz do projeto: python -m unittest discover -s tests -v
"""

import base64
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import serial

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from Coagmaster.coagmaster import AnalisadorCoagmaster
from bh5100.bh5100 import AnalisadorBH5100
from mek7300.mek7300 import AnalisadorMEK7300
from pkl.pkl import AnalisadorPKL125
from shared import base_analisador
from shared.base_analisador import BaseAnalisador
from shared.file_cleanup import rotate_daily_folders
from vidas1600.vidas1600 import AnalisadorVIDAS1600

try:
    from PIL import Image
except ImportError:
    Image = None

PNG_FALSO = b"\x89PNG\r\n\x1a\nconteudo"
HEADER_TOKEN = "X-Machine-Token"
TOKEN_TESTE = "token-de-teste"
ERRO_QUEDA = "ClearCommError failed (cabo removido)"
ERRO_ABERTURA = "could not open port 'COM9': FileNotFoundError"


def hl7_bh5100(formato: str = "PNG", imagem: bytes = PNG_FALSO) -> str:
    dados = base64.b64encode(imagem).decode()
    segmentos = [
        "MSH|^~\\&|BH5100|X|LIS||20261105||ORU^R01|1|P|2.3.1",
        "OBR|1||151020262929|",
        "OBX|1|NM|WBC^White||7.64|10^9/L|4-10|N|||F",
        "OBX|2|NM|RBC^Red||4.5|10^12/L|4-5|N|||F",
        f"OBX|3|ED|HISTO^WBCHistogram||LIS^Image^{formato}^Base64^{dados}||||||F",
    ]
    return "\x0b" + "\r".join(segmentos) + "\r\x1c\r"


HL7_VIDAS = "\x0b" + "\r".join([
    "MSH|^~\\&|E-LAB|ES|LIS||20261105||ORU^R01|1|P|2.3.1||||0",
    "OBR|1|151020262929|",
    "OBX|1|NM|Gli||90|mg/dL||N",
]) + "\r\x1c\r"

TXT_MEK = "FileName: 151020262929\r\nWBC: 5.800\r\n"
JSON_COAG = json.dumps({"FileName": "151020262929", "ExamCode": "TAP"})
JSON_PKL = json.dumps({
    "franchise_credential_id": "",
    "tag_id": "151020262929",
    "pkl_machine_id": "",
    "results": [{"exam_code": "HEMO", "test": "GLI", "value": "1"}],
})

# id: (classe, nome, kwargs, arquivo em gerados, conteúdo, defaults extras)
MAQUINAS = {
    "mek7300": (
        AnalisadorMEK7300, "MEK7300",
        {"use_serial_listener": True, "enable_keepalive": True},
        "exame.txt", TXT_MEK, {},
    ),
    "coagmaster": (AnalisadorCoagmaster, "Coagmaster", {}, "exame.txt", JSON_COAG, {}),
    "vidas1600": (AnalisadorVIDAS1600, "VIDAS1600", {}, "exame.hl7", HL7_VIDAS, {}),
    "pkl": (
        AnalisadorPKL125, "PKL125", {"use_serial_listener": True},
        "exame.astm", JSON_PKL, {"pkl_machine_id": ""},
    ),
    "bh5100": (
        AnalisadorBH5100, "BH5100", {},
        "exame.hl7", hl7_bh5100(),
        {"exam_images_url": "", "exam_images_machine_token": ""},
    ),
}
MAQUINAS_SEM_IMAGEM = [nome for nome in MAQUINAS if nome != "bh5100"]


class ServidorFalso:
    """Webhook e rotas de imagem do lab-apoio, mais o PUT do S3."""

    def __init__(self) -> None:
        self.requisicoes = []
        self.status_webhook = 200
        dono = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _registrar(self):
                tamanho = int(self.headers.get("Content-Length") or 0)
                corpo = self.rfile.read(tamanho)
                dono.requisicoes.append({
                    "metodo": self.command,
                    "caminho": self.path,
                    "token": self.headers.get(HEADER_TOKEN),
                    "corpo": corpo,
                })
                return corpo

            def _responder(self, status, dados=None):
                corpo = json.dumps(dados or {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def do_POST(self):
                corpo = self._registrar()
                if self.path.endswith("/images/initiate"):
                    imagens = [
                        {
                            "code": item["code"],
                            "image_id": f"id-{item['code']}",
                            "upload_url": f"{dono.url}/s3/{item['code']}",
                            "headers": {"Content-Type": item["mime_type"]},
                        }
                        for item in json.loads(corpo)["images"]
                    ]
                    self._responder(201, {"data": {"images": imagens}})
                elif self.path.endswith("/images/confirm"):
                    ids = json.loads(corpo)["image_ids"]
                    self._responder(200, {"data": {
                        "completed": ids, "missing": [], "failed": [], "not_found": [],
                    }})
                else:
                    self._responder(dono.status_webhook, {"ok": True})

            def do_PUT(self):
                self._registrar()
                self._responder(200)

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}"
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._thread.start()

    def parar(self) -> None:
        self._http.shutdown()
        self._http.server_close()
        self._thread.join(timeout=5)

    def caminhos(self, trecho: str = "") -> list:
        return [r for r in self.requisicoes if trecho in r["caminho"]]


class PortaFalsa:
    """Estado físico da porta, controlado pelo teste."""

    def __init__(self, disponivel: bool = True) -> None:
        self.disponivel = disponivel
        self.leitura_ok = True
        self.aberturas = 0

    def cair(self) -> None:
        self.disponivel = False
        self.leitura_ok = False

    def voltar(self) -> None:
        self.disponivel = True
        self.leitura_ok = True


class SerialFalsa:
    def __init__(self, porta: PortaFalsa) -> None:
        if not porta.disponivel:
            raise serial.SerialException(ERRO_ABERTURA)
        porta.aberturas += 1
        self._porta = porta
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        if not self._porta.leitura_ok:
            raise serial.SerialException(ERRO_QUEDA)
        return 0

    def read(self, tamanho: int = 1) -> bytes:
        return b""

    def write(self, dados: bytes) -> int:
        return len(dados)

    def close(self) -> None:
        self.is_open = False


class BaseTeste(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="leitor_hl7_")
        self.servidor = ServidorFalso()
        self.porta = PortaFalsa()
        self.eventos = []
        self.encerrou = threading.Event()
        self._parar = threading.Event()
        self._sleep_real = time.sleep
        self._threads_antes = set(threading.enumerate())

        def sleep_curto(segundos):
            # Todos os laços do programa passam por aqui: encerra as threads no
            # fim do teste e deixa os intervalos curtos.
            if self._parar.is_set():
                raise SystemExit
            self._sleep_real(min(segundos, 0.01))

        def popup(_analisador, titulo, mensagem):
            self.eventos.append(("popup", titulo, mensagem))

        def sair(codigo):
            self.eventos.append(("exit", codigo))
            self.encerrou.set()
            raise SystemExit

        ambiente = {"HOME": self.tmp, "USERPROFILE": self.tmp, "NO_PROXY": "127.0.0.1"}
        for patch in (
            mock.patch.dict(os.environ, ambiente),
            mock.patch.object(sys, "argv", [os.path.join(self.tmp, "app.py")]),
            mock.patch.object(base_analisador, "start_cleanup_thread"),
            mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 0.2),
            mock.patch.object(base_analisador, "DRAIN_STALL_TIMEOUT", 0.4),
            mock.patch.object(BaseAnalisador, "_show_popup", popup),
            mock.patch("time.sleep", sleep_curto),
            mock.patch("os._exit", sair),
            mock.patch("serial.Serial", lambda *a, **k: SerialFalsa(self.porta)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self._parar.set()
        for thread in set(threading.enumerate()) - self._threads_antes:
            if thread is not self.servidor._thread:
                thread.join(timeout=5)
        self.servidor.parar()
        raiz = logging.getLogger()
        for handler in raiz.handlers[:]:
            handler.close()
            raiz.removeHandler(handler)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def criar(self, maquina: str, **defaults_extras):
        classe, nome, kwargs, _, _, extras = MAQUINAS[maquina]
        defaults = {
            "com_port": "COM9",
            "baud_rate": 9600,
            "franchise_credential_id": "unidade-teste",
            "webhook_url": (
                f"{self.servidor.url}/api/integration/{maquina}"
                "?franchise_credential_id={franchise_credential_id}"
            ),
            **extras,
            **defaults_extras,
        }
        return classe(
            machine_id=maquina,
            machine_name=nome,
            config_defaults=defaults,
            health_port=0,
            **kwargs,
        )

    def gravar_exame(self, analisador, nome: str, conteudo: str) -> None:
        with open(
            os.path.join(analisador.GERADOS_DIR, nome), "w", encoding="utf-8", newline=""
        ) as arquivo:
            arquivo.write(conteudo)

    def enviar_uma_vez(self, analisador) -> None:
        threading.Thread(target=analisador._task_sender_to_webhook, daemon=True).start()
        self.esperar(lambda: not os.listdir(analisador.GERADOS_DIR))

    def esperar(self, condicao, timeout: float = 15) -> None:
        limite = time.monotonic() + timeout
        while not condicao():
            if time.monotonic() > limite:
                self.fail(f"Condição não atendida em {timeout}s. Eventos: {self.eventos}")
            self._sleep_real(0.01)

    def iniciar(self, analisador) -> None:
        threading.Thread(target=analisador.start, daemon=True).start()

    def iniciar_conectado(self, analisador) -> None:
        self.iniciar(analisador)
        self.esperar(analisador._serial_is_open)

    def mensagem_popup(self) -> str:
        popups = [evento for evento in self.eventos if evento[0] == "popup"]
        self.assertEqual(len(popups), 1, self.eventos)
        return popups[0][2]


class TestToken(BaseTeste):
    def test_maquinas_sem_imagem_enviam_sem_token(self) -> None:
        for maquina in MAQUINAS_SEM_IMAGEM:
            with self.subTest(maquina=maquina):
                analisador = self.criar(maquina)
                _, _, _, nome, conteudo, _ = MAQUINAS[maquina]
                self.gravar_exame(analisador, nome, conteudo)

                self.enviar_uma_vez(analisador)

                self.assertEqual(os.listdir(analisador.REQUISICOES_NAO_ENVIADAS_DIR), [])
                self.assertIn(nome, os.listdir(analisador.ENVIADOS_DIR))
                enviados = self.servidor.caminhos(f"/integration/{maquina}")
                self.assertTrue(enviados)
                self.assertTrue(all(r["token"] is None for r in enviados))

        self.assertEqual(self.servidor.caminhos("/images"), [])

    def test_config_de_maquina_sem_imagem_nao_tem_chaves_de_imagem(self) -> None:
        for maquina in MAQUINAS_SEM_IMAGEM:
            with self.subTest(maquina=maquina):
                analisador = self.criar(maquina)
                caminho = os.path.join(self.tmp, f"config_{maquina}.json")
                with open(caminho, encoding="utf-8") as arquivo:
                    config = json.load(arquivo)
                self.assertNotIn("exam_images_url", config)
                self.assertNotIn("exam_images_machine_token", config)
                self.assertEqual(analisador.EXAM_IMAGES_URL, "")

    def test_log_de_maquina_sem_imagem_nao_cita_token(self) -> None:
        analisador = self.criar("mek7300")
        with self.assertLogs(level="INFO") as logs:
            analisador._banner()
        self.assertFalse(any("Imagens de exame" in linha for linha in logs.output))

    def test_config_antigo_com_chaves_de_imagem_e_ignorado(self) -> None:
        with open(os.path.join(self.tmp, "config_mek7300.json"), "w", encoding="utf-8") as arquivo:
            json.dump({"com_port": "COM9", "exam_images_machine_token": TOKEN_TESTE}, arquivo)
        analisador = self.criar("mek7300")
        self.assertEqual(analisador.EXAM_IMAGES_MACHINE_TOKEN, "")
        self.assertEqual(analisador.EXAM_IMAGES_URL, "")

    def test_bh5100_envia_token_so_nas_rotas_de_imagem(self) -> None:
        analisador = self.criar("bh5100", exam_images_machine_token=TOKEN_TESTE)
        self.gravar_exame(analisador, "exame.hl7", hl7_bh5100())

        self.enviar_uma_vez(analisador)

        webhook = self.servidor.caminhos("/integration/bh5100")
        self.assertEqual(len(webhook), 1)
        self.assertIsNone(webhook[0]["token"])
        self.assertNotIn(b"_exam_images", webhook[0]["corpo"])

        for rota in ("/images/initiate", "/images/confirm"):
            chamadas = self.servidor.caminhos(rota)
            self.assertEqual(len(chamadas), 1, rota)
            self.assertEqual(chamadas[0]["token"], TOKEN_TESTE)

        put = self.servidor.caminhos("/s3/WBC")
        self.assertEqual(len(put), 1)
        self.assertIsNone(put[0]["token"])
        self.assertEqual(put[0]["corpo"], PNG_FALSO)
        self.assertIn("exame.hl7", os.listdir(analisador.ENVIADOS_DIR))

    def test_bh5100_sem_token_envia_hemograma_e_ignora_imagens(self) -> None:
        analisador = self.criar("bh5100", exam_images_machine_token="")
        self.gravar_exame(analisador, "exame.hl7", hl7_bh5100())

        self.enviar_uma_vez(analisador)

        self.assertEqual(len(self.servidor.caminhos("/integration/bh5100")), 1)
        self.assertEqual(self.servidor.caminhos("/images"), [])
        self.assertEqual(self.servidor.caminhos("/s3"), [])
        self.assertIn("exame.hl7", os.listdir(analisador.ENVIADOS_DIR))

    @unittest.skipIf(Image is None, "Pillow não instalado")
    def test_bh5100_converte_bmp_para_png(self) -> None:
        bmp = io.BytesIO()
        Image.new("RGB", (4, 4), "red").save(bmp, "BMP")
        analisador = self.criar("bh5100", exam_images_machine_token=TOKEN_TESTE)
        self.gravar_exame(analisador, "exame.hl7", hl7_bh5100("BMP", bmp.getvalue()))

        self.enviar_uma_vez(analisador)

        put = self.servidor.caminhos("/s3/WBC")
        self.assertEqual(len(put), 1)
        self.assertTrue(put[0]["corpo"].startswith(b"\x89PNG"))


class TestQuedaCOM(BaseTeste):
    def _verificar_queda(self, maquina: str) -> None:
        analisador = self.criar(maquina)
        _, _, _, nome, conteudo, _ = MAQUINAS[maquina]
        self.iniciar_conectado(analisador)

        self.porta.cair()
        self.esperar(lambda: not analisador._serial_is_open())
        self.gravar_exame(analisador, nome, conteudo)
        self.assertTrue(self.encerrou.wait(15), self.eventos)

        self.assertTrue(self.servidor.caminhos(f"/integration/{maquina}"))
        self.assertIn(nome, os.listdir(analisador.ENVIADOS_DIR))

        self.assertEqual([evento[0] for evento in self.eventos], ["popup", "exit"])
        self.assertEqual(self.eventos[1], ("exit", 1))
        self.assertIn(analisador.machine_name, self.eventos[0][1])

        mensagem = self.mensagem_popup()
        self.assertIn("foi perdida", mensagem)
        self.assertIn("Porta: COM9", mensagem)
        self.assertIn(f"Erro: SerialException: {ERRO_QUEDA}", mensagem)
        self.assertIn(f"reconexão: SerialException: {ERRO_ABERTURA}", mensagem)
        self.assertIn("enviados durante a queda: 1", mensagem)
        self.assertIn("ainda pendentes (serão enviados ao reabrir): 0", mensagem)

    def test_queda_bh5100(self) -> None:
        self._verificar_queda("bh5100")

    def test_queda_coagmaster(self) -> None:
        self._verificar_queda("coagmaster")

    def test_queda_vidas1600(self) -> None:
        self._verificar_queda("vidas1600")

    def test_queda_mek7300(self) -> None:
        self._verificar_queda("mek7300")

    def test_queda_pkl(self) -> None:
        self._verificar_queda("pkl")

    def _verificar_reconexao(self, maquina: str) -> None:
        analisador = self.criar(maquina)
        with mock.patch.object(base_analisador, "CONNECTION_DEADLINE", 1.0):
            self.iniciar_conectado(analisador)

            self.porta.cair()
            self.esperar(lambda: not analisador._serial_is_open())
            self.porta.voltar()
            self.esperar(analisador._serial_is_open)

            self._sleep_real(1.3)

        self.assertEqual(self.eventos, [])
        self.assertIsNone(analisador._erro_queda)
        self.assertIsNone(analisador._erro_reconexao)

    def test_reconexao_em_ate_5_min_nao_encerra_modo_direto(self) -> None:
        self._verificar_reconexao("coagmaster")

    def test_reconexao_em_ate_5_min_nao_encerra_modo_listener(self) -> None:
        self._verificar_reconexao("mek7300")

    def _verificar_porta_que_nunca_abriu(self, maquina: str) -> None:
        self.porta = PortaFalsa(disponivel=False)
        analisador = self.criar(maquina)
        self.iniciar(analisador)

        self.assertTrue(self.encerrou.wait(15), self.eventos)

        mensagem = self.mensagem_popup()
        self.assertIn(f"Erro: SerialException: {ERRO_ABERTURA}", mensagem)
        self.assertNotIn("Última tentativa", mensagem)

    def test_porta_que_nunca_abriu_mostra_erro_modo_direto(self) -> None:
        self._verificar_porta_que_nunca_abriu("vidas1600")

    def test_porta_que_nunca_abriu_mostra_erro_modo_listener(self) -> None:
        self._verificar_porta_que_nunca_abriu("pkl")

    def test_arquivo_preso_nao_impede_popup(self) -> None:
        analisador = self.criar("coagmaster")

        def preso(*_args):
            while True:
                time.sleep(1)

        analisador.process_file = preso
        self.iniciar_conectado(analisador)
        self.gravar_exame(analisador, "exame.txt", JSON_COAG)
        self.porta.cair()

        self.assertTrue(self.encerrou.wait(15), self.eventos)

        self.assertIn(
            "ainda pendentes (serão enviados ao reabrir): 1", self.mensagem_popup()
        )
        self.assertIn("exame.txt", os.listdir(analisador.GERADOS_DIR))

    def test_espera_o_envio_enquanto_gerados_diminui(self) -> None:
        analisador = self.criar("coagmaster")
        original = analisador._enviar_payload_webhook

        def envio_lento(*args):
            self._sleep_real(0.25)
            return original(*args)

        analisador._enviar_payload_webhook = envio_lento
        self.iniciar_conectado(analisador)
        self.porta.cair()
        self.esperar(lambda: not analisador._serial_is_open())
        for numero in range(3):
            self.gravar_exame(analisador, f"exame{numero}.txt", JSON_COAG)

        # O prazo (0,2s) e o limite sem progresso (0,4s) passam durante o envio
        # total (0,75s), mas cada arquivo enviado conta como progresso.
        self.assertTrue(self.encerrou.wait(15), self.eventos)

        mensagem = self.mensagem_popup()
        self.assertIn("enviados durante a queda: 3", mensagem)
        self.assertIn("ainda pendentes (serão enviados ao reabrir): 0", mensagem)

    def test_exame_recusado_aparece_no_popup_como_nao_enviado(self) -> None:
        self.servidor.status_webhook = 400
        analisador = self.criar("coagmaster")
        self.iniciar_conectado(analisador)
        self.porta.cair()
        self.esperar(lambda: not analisador._serial_is_open())
        self.gravar_exame(analisador, "exame.txt", JSON_COAG)

        self.assertTrue(self.encerrou.wait(15), self.eventos)

        mensagem = self.mensagem_popup()
        self.assertIn("enviados durante a queda: 0", mensagem)
        self.assertIn('não enviados (pasta "requisições não enviadas"): 1', mensagem)
        self.assertIn("exame.txt", os.listdir(analisador.REQUISICOES_NAO_ENVIADAS_DIR))

    def _verificar_para_de_reconectar(self, maquina: str) -> None:
        analisador = self.criar(maquina)
        self.iniciar_conectado(analisador)
        self.porta.cair()
        self.assertTrue(self.encerrou.wait(15), self.eventos)

        # Popup aberto e cabo de volta: o programa não pode retomar a porta,
        # senão o usuário não consegue reabri-lo.
        aberturas = self.porta.aberturas
        self.porta.voltar()
        self._sleep_real(0.2)

        self.assertEqual(self.porta.aberturas, aberturas)
        self.assertFalse(analisador._serial_is_open())

    def test_para_de_reconectar_ao_encerrar_modo_direto(self) -> None:
        self._verificar_para_de_reconectar("coagmaster")

    def test_para_de_reconectar_ao_encerrar_modo_listener(self) -> None:
        self._verificar_para_de_reconectar("mek7300")


class TestGerados(BaseTeste):
    def test_rotacao_diaria_nao_tira_exame_de_gerados(self) -> None:
        analisador = self.criar("coagmaster")
        antigo = time.time() - 3600
        pendente = os.path.join(analisador.GERADOS_DIR, "pendente.txt")
        enviado = os.path.join(analisador.ENVIADOS_DIR, "enviado.txt")
        for caminho in (pendente, enviado):
            with open(caminho, "w", encoding="utf-8") as arquivo:
                arquivo.write(JSON_COAG)
            os.utime(caminho, (antigo, antigo))

        rotate_daily_folders(analisador._cleanup_config)

        self.assertTrue(os.path.exists(pendente))
        self.assertFalse(os.path.exists(enviado))

    def test_erro_em_process_file_nao_prende_a_fila(self) -> None:
        analisador = self.criar("coagmaster")

        def com_erro(*_args):
            raise ValueError("arquivo inesperado")

        analisador.process_file = com_erro
        self.gravar_exame(analisador, "exame.txt", JSON_COAG)

        self.enviar_uma_vez(analisador)

        self.assertIn("exame.txt", os.listdir(analisador.REQUISICOES_NAO_ENVIADAS_DIR))

    def test_porta_de_health_ocupada_nao_derruba_o_programa(self) -> None:
        analisador = self.criar("coagmaster")
        analisador._health = mock.Mock()
        analisador._health.start.side_effect = OSError("porta em uso")

        analisador._start_health_server()


if __name__ == "__main__":
    unittest.main()
