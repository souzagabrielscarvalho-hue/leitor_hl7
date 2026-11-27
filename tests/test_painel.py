"""
Testes do leitor universal: registro das máquinas, config pelo painel, portas
seriais, processos e a API HTTP do painel (só em 127.0.0.1).
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "universal"))

import leitor  # noqa: E402
from shared import maquinas  # noqa: E402
from shared.health_server import HealthServer  # noqa: E402
from shared.painel import acesso, atualizacao, config, inicializacao, portas, processos, raiz  # noqa: E402
from shared.config_loader import VARIAVEL_PASTA_CONFIG, VARIAVEL_PASTA_DADOS  # noqa: E402
from shared.painel.processos import Gerenciador  # noqa: E402
from shared.painel.nucleo import Painel  # noqa: E402

UUID_TESTE = "00000000-0000-4000-8000-000000000000"
SENHA_TESTE = "senha-de-teste-123"
# Poucas iterações: o teste não precisa da lentidão do PBKDF2 real.
HASH_TESTE = acesso.gerar_hash(SENHA_TESTE, iteracoes=1000)


def painel_admin(gerenciador, pasta):
    """Painel com a sessão de administrador aberta (senha de teste)."""
    painel = Painel(gerenciador, pasta, sessao=acesso.Sessao(HASH_TESTE))
    assert painel.entrar(SENHA_TESTE)["ok"]
    return painel


def ler(caminho, binario=False):
    with open(caminho, "rb" if binario else "r", **({} if binario else {"encoding": "utf-8"})) as f:
        return f.read()


def setUpModule():
    # As linhas ✓/✗ do painel não interessam aqui; só poluiriam a saída.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def valores_ca150(**extra):
    v = config.ler("ca150", os.path.join(tempfile.gettempdir(), "painel_sem_config"))["valores"]
    v.update(
        franchise_credential_id=UUID_TESTE,
        webhook_url="http://localhost:8039/api/integration/ca-150?franchise_credential_id={franchise_credential_id}",
        consult_url="http://localhost:8039/api/interfacing/tag?franchise_credential_id={franchise_credential_id}",
    )
    v.update(extra)
    return v


class ComPastas(unittest.TestCase):
    """Pasta de config e HOME temporários (o Desktop das máquinas vai para lá)."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="painel_cfg_")
        self.home = tempfile.mkdtemp(prefix="painel_home_")
        patcher = mock.patch.dict(os.environ, {"HOME": self.home, "USERPROFILE": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.pasta, True)
        self.addCleanup(shutil.rmtree, self.home, True)


class TestRegistro(unittest.TestCase):
    def test_todas_as_maquinas_importam_com_os_dados_do_modulo(self):
        for m in maquinas.MAQUINAS:
            mod = maquinas.modulo(m.id)
            self.assertEqual(mod.MACHINE_ID, m.id)
            self.assertTrue(callable(mod.criar_analisador))
            self.assertIn("webhook_url", mod.CONFIG_DEFAULTS)

    def test_health_e_rota_proprias_por_maquina(self):
        ids = [m.id for m in maquinas.MAQUINAS]
        portas_health = [maquinas.health_port(i) for i in ids]
        self.assertEqual(len(set(portas_health)), len(ids))

        # Regra 6: cada máquina com a sua rota no lab-apoio.
        rotas = [maquinas.rota_webhook(i) for i in ids]
        self.assertEqual(len(set(rotas)), len(ids))
        for rota in rotas:
            self.assertTrue(rota.startswith("/api/integration/"), rota)

    def test_maquina_desconhecida(self):
        with self.assertRaises(KeyError):
            maquinas.obter("xyz")
        self.assertEqual(leitor.main(["--maquina", "xyz"]), leitor.SAIDA_MAQUINA_DESCONHECIDA)

    def test_comando_em_desenvolvimento_chama_o_leitor(self):
        cmd = leitor.comando_maquina("ca150")
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("leitor.py"))
        self.assertEqual(cmd[2:], ["--maquina", "ca150"])

    def test_health_informa_pid(self):
        self.assertEqual(HealthServer(port=0)._get_stats_snapshot()["pid"], os.getpid())


class TestConexaoAtiva(unittest.TestCase):
    """port_open do /health: em TCP, só a máquina conectada agora."""

    def test_tcp_antes_e_depois_de_conectar(self):
        # As três com TCP próprio (as outras usam o TCP genérico da base).
        nomes = {"ca150": "AnalisadorCA150", "urit8021": "AnalisadorURIT8021", "bh5100": "AnalisadorBH5100"}
        for mid, nome in nomes.items():
            classe = getattr(maquinas.modulo(mid), nome)
            a = classe.__new__(classe)
            a.MODO, a._ja_conectou, a._conexao = "tcp", False, None
            self.assertTrue(a._serial_is_open())   # watchdog: ainda não é queda
            self.assertFalse(a._conexao_ativa())   # painel: não conectada
            a._ja_conectou, a._conexao = True, object()
            self.assertTrue(a._conexao_ativa())


class TestConfig(ComPastas):
    def test_sem_arquivo_nao_sugere_credencial_de_outra_unidade(self):
        for m in maquinas.MAQUINAS:
            dados = config.ler(m.id, self.pasta)
            self.assertFalse(dados["existe"])
            self.assertEqual(dados["valores"]["franchise_credential_id"], "")
            self.assertIn("franchise_credential_id", dados["erros"])
        ok, motivo = config.pronta("ca150", self.pasta)
        self.assertFalse(ok)
        self.assertIn("Configure", motivo)

    def test_salva_e_fica_pronta(self):
        r = config.salvar("ca150", valores_ca150(), self.pasta)
        self.assertTrue(r["ok"], r)
        with open(r["caminho"], encoding="utf-8") as f:
            gravado = json.load(f)
        self.assertEqual(gravado["franchise_credential_id"], UUID_TESTE)
        self.assertEqual(gravado["tcp_port"], 5150)
        self.assertEqual(config.pronta("ca150", self.pasta), (True, ""))

    def test_placeholder_e_uuid_invalido_sao_recusados(self):
        for valor in ("XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX", "abc", ""):
            r = config.salvar("ca150", valores_ca150(franchise_credential_id=valor), self.pasta)
            self.assertFalse(r["ok"])
            self.assertIn("franchise_credential_id", r["erros"])
        self.assertFalse(os.path.exists(config.caminho_config("ca150", self.pasta)))

    def test_webhook_so_pode_mudar_o_host(self):
        outra = valores_ca150(webhook_url="http://localhost:8039/api/integration/bh5100")
        r = config.salvar("ca150", outra, self.pasta)
        self.assertFalse(r["ok"])
        self.assertIn("/api/integration/ca-150", r["erros"]["webhook_url"])

        sem_esquema = valores_ca150(webhook_url="localhost:8039/api/integration/ca-150")
        self.assertIn("webhook_url", config.salvar("ca150", sem_esquema, self.pasta)["erros"])

        producao = valores_ca150(
            webhook_url="https://apoio.internal.vidaexame.com/api/integration/ca-150"
                        "?franchise_credential_id={franchise_credential_id}"
        )
        self.assertTrue(config.salvar("ca150", producao, self.pasta)["ok"])

    def test_modo_decide_o_que_e_obrigatorio(self):
        tcp = valores_ca150(modo="tcp", com_port="")
        self.assertTrue(config.salvar("ca150", tcp, self.pasta)["ok"])
        serial = valores_ca150(modo="serial", com_port="")
        self.assertIn("com_port", config.salvar("ca150", serial, self.pasta)["erros"])
        self.assertIn("tcp_port", config.salvar("ca150", valores_ca150(tcp_port=70000), self.pasta)["erros"])
        self.assertIn("modo", config.salvar("ca150", valores_ca150(modo="usb"), self.pasta)["erros"])

    def test_chave_que_a_maquina_nao_usa(self):
        v = valores_ca150(exam_images_machine_token="abc")
        self.assertIn("exam_images_machine_token", config.salvar("ca150", v, self.pasta)["erros"])

    def test_mantem_chaves_desconhecidas_do_arquivo(self):
        caminho = config.caminho_config("ca150", self.pasta)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"webhook_url_teste_local": "http://localhost:8039/x", "tcp_port": 1}, f)
        self.assertTrue(config.salvar("ca150", valores_ca150(), self.pasta)["ok"])
        with open(caminho, encoding="utf-8") as f:
            gravado = json.load(f)
        self.assertEqual(gravado["webhook_url_teste_local"], "http://localhost:8039/x")
        self.assertEqual(gravado["tcp_port"], 5150)

    def test_arquivo_corrompido_nao_e_sobrescrito(self):
        caminho = config.caminho_config("ca150", self.pasta)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("{quebrado")
        r = config.salvar("ca150", valores_ca150(), self.pasta)
        self.assertFalse(r["ok"])
        with open(caminho, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{quebrado")
        self.assertFalse(config.pronta("ca150", self.pasta)[0])

    def test_bh5100_com_token_e_modo_serial_ou_tcp(self):
        dados = config.ler("bh5100", self.pasta)
        chaves = [c["chave"] for c in dados["campos"]]
        self.assertIn("exam_images_machine_token", chaves)
        self.assertNotIn("consult_url", chaves)
        self.assertEqual(dados["valores"]["modo"], "serial")

    def test_todas_as_maquinas_tem_serial_e_tcp(self):
        portas_tcp = []
        for m in maquinas.MAQUINAS:
            chaves = [c["chave"] for c in config.ler(m.id, self.pasta)["campos"]]
            for chave in ("modo", "com_port", "baud_rate", "tcp_host", "tcp_port"):
                self.assertIn(chave, chaves, m.id)
            portas_tcp.append(maquinas.config_defaults(m.id)["tcp_port"])
        # Portas TCP padrão diferentes: duas máquinas no mesmo PC não disputam a porta.
        self.assertEqual(len(set(portas_tcp)), len(portas_tcp))

    def test_tcp_sem_com_port_e_valido(self):
        valores = dict(valores_ca150(), modo="tcp", com_port="")
        self.assertEqual(config.validar("ca150", valores)[1], {})
        bh = dict(config.ler("bh5100", self.pasta)["valores"], franchise_credential_id=UUID_TESTE,
                  modo="tcp", com_port="", tcp_port=5100)
        self.assertEqual(config.validar("bh5100", bh)[1], {})


def porta_fake(porta, vid=None, pid=None, serie="", local="", descricao=""):
    return {"porta": porta, "descricao": descricao, "fabricante": "", "vid": vid, "pid": pid,
            "serie": serie, "local": local, "usb": vid is not None}


class TestPortas(ComPastas):
    def test_marca_maquinas_e_porta_em_uso(self):
        sistema = [porta_fake("COM1"), porta_fake("COM3", 0x1A86, 0x7523)]
        configs = {
            "bh5100": {"com_port": "COM3"},
            "ca150": {"modo": "tcp", "com_port": "COM3"},  # TCP: a COM não conta
            "vidas1600": {"com_port": "COM1"},
        }
        lista = portas.listar(configs, rodando=lambda mid: mid == "bh5100", sistema=sistema)
        com1, com3 = lista
        self.assertEqual(com3["maquinas"], ["bh5100"])
        self.assertEqual(com3["em_uso_por"], "bh5100")
        self.assertEqual(com1["situacao"], "nao_testada")

    def test_testar_nao_abre_a_porta_de_maquina_aberta(self):
        sistema = [porta_fake("COM3", 1, 2), porta_fake("COM4", 1, 2)]
        with mock.patch.object(portas, "testar_abertura", return_value="Acesso negado") as testar:
            lista = portas.listar({"bh5100": {"com_port": "COM3"}}, rodando=lambda m: True,
                                  testar=True, sistema=sistema)
        testar.assert_called_once_with("COM4")
        self.assertEqual(lista[1]["situacao"], "ocupada")
        self.assertEqual(lista[1]["erro"], "Acesso negado")

    def test_sugere_a_com_nova_do_mesmo_adaptador(self):
        antes = [porta_fake("COM6", 0x0403, 0x6001, serie="A50285BI", descricao="FTDI")]
        portas.lembrar_adaptador(self.pasta, "mek7300", "COM6", antes)

        depois = [porta_fake("COM1"), porta_fake("COM9", 0x0403, 0x6001, serie="A50285BI", descricao="FTDI")]
        s = portas.sugerir_porta(self.pasta, "mek7300", "COM6", depois)
        self.assertEqual(s, {"porta_antiga": "COM6", "porta_nova": "COM9", "descricao": "FTDI"})

        # Mesmo modelo, outra série: não é o mesmo adaptador.
        outro = [porta_fake("COM9", 0x0403, 0x6001, serie="OUTRO")]
        self.assertIsNone(portas.sugerir_porta(self.pasta, "mek7300", "COM6", outro))
        # COM do config ainda existe: nada a sugerir.
        self.assertIsNone(portas.sugerir_porta(self.pasta, "mek7300", "COM6", antes))

    def test_sem_serie_usa_a_posicao_usb(self):
        antes = [porta_fake("COM5", 0x1A86, 0x7523, local="1-1.2")]
        portas.lembrar_adaptador(self.pasta, "pkl", "COM5", antes)
        mesma_posicao = [porta_fake("COM7", 0x1A86, 0x7523, local="1-1.2"),
                         porta_fake("COM8", 0x1A86, 0x7523, local="1-1.3")]
        self.assertEqual(portas.sugerir_porta(self.pasta, "pkl", "COM5", mesma_posicao)["porta_nova"], "COM7")

    def test_porta_da_placa_mae_nao_e_lembrada(self):
        portas.lembrar_adaptador(self.pasta, "vidas1600", "COM1", [porta_fake("COM1")])
        self.assertEqual(portas.ler_estado(self.pasta), {})


class TestGerenciador(ComPastas):
    def setUp(self):
        super().setUp()
        # Sem health de verdade: o estado vem só do processo.
        patcher = mock.patch.object(processos, "health", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.comando = [sys.executable, "-c", "import time; time.sleep(30)"]
        self.g = Gerenciador(lambda mid: self.comando, os.path.join(self.home, "saida"), self.pasta)

    def test_nao_inicia_sem_config(self):
        r = self.g.iniciar("ca150")
        self.assertFalse(r["ok"])
        self.assertIn("Configure", r["mensagem"])

    def test_inicia_e_para(self):
        config.salvar("ca150", valores_ca150(), self.pasta)
        r = self.g.iniciar("ca150")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.g.estado("ca150")["estado"], "iniciando")
        self.assertFalse(self.g.iniciar("ca150")["ok"])  # já aberta

        r = self.g.parar("ca150")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.g.estado("ca150")["estado"], "parada")
        self.assertFalse(self.g.parar("ca150")["ok"])

    def test_explica_a_saida_pela_queda(self):
        config.salvar("ca150", valores_ca150(), self.pasta)
        self.comando = [sys.executable, "-c", "import sys; print('saindo'); sys.exit(1)"]
        self.assertTrue(self.g.iniciar("ca150")["ok"])
        limite = time.time() + 10
        while self.g.estado("ca150").get("codigo_saida") is None and time.time() < limite:
            time.sleep(0.1)
        e = self.g.estado("ca150")
        self.assertEqual(e["estado"], "parada")
        self.assertEqual(e["codigo_saida"], 1)
        self.assertIn("conexão", e["mensagem"])
        with open(self.g.arquivo_saida("ca150"), encoding="utf-8") as f:
            self.assertIn("saindo", f.read())


class TestNucleo(ComPastas):
    """O que a janela chama, sem Qt."""

    def setUp(self):
        super().setUp()
        for alvo, valor in ((processos, "health"), (portas, "listar_sistema")):
            patcher = mock.patch.object(alvo, valor, return_value=None if valor == "health" else [])
            patcher.start()
            self.addCleanup(patcher.stop)
        self.comando = [sys.executable, "-c", "import time; time.sleep(30)"]
        self.g = Gerenciador(lambda mid: self.comando, os.path.join(self.home, "saida"), self.pasta)
        self.painel = painel_admin(self.g, self.pasta)

    def test_lista_so_as_maquinas_adicionadas(self):
        e = self.painel.estado()
        self.assertEqual(e["maquinas"], [])
        self.assertEqual([d["id"] for d in e["disponiveis"]], [m.id for m in maquinas.MAQUINAS])

        r = self.painel.adicionar("ca150", self.pasta, valores_ca150())
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["existente"])
        e = self.painel.estado()
        self.assertEqual([m["id"] for m in e["maquinas"]], ["ca150"])
        self.assertEqual(e["maquinas"][0]["conexao"], "TCP 5150")
        self.assertNotIn("ca150", [d["id"] for d in e["disponiveis"]])
        # Cada modelo uma vez por computador.
        self.assertFalse(self.painel.adicionar("ca150", self.pasta, valores_ca150())["ok"])

    def test_adicionar_valida_antes_de_gravar(self):
        r = self.painel.adicionar("ca150", self.pasta, valores_ca150(franchise_credential_id=""))
        self.assertFalse(r["ok"])
        self.assertIn("franchise_credential_id", r["erros"])
        self.assertEqual(self.painel.adicionadas(), [])
        self.assertFalse(os.path.exists(config.caminho_config("ca150", self.pasta)))

    def test_config_existente_na_pasta_nao_e_sobrescrito(self):
        outra = os.path.join(self.home, "configs_lab")
        os.makedirs(outra)
        caminho = config.caminho_config("ca150", outra)
        original = {"modo": "tcp", "tcp_port": 7777, "franchise_credential_id": UUID_TESTE, "chave_da_unidade": 1}
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(original, f)
        antes = ler(caminho)

        r = self.painel.adicionar("ca150", outra, valores_ca150(tcp_port=5150))
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["existente"])
        self.assertEqual(ler(caminho), antes)
        self.assertEqual(self.painel.pasta_de("ca150"), outra)
        self.assertEqual(self.painel.ler_config("ca150")["valores"]["tcp_port"], 7777)
        # A pasta escolhida fica no registro do painel.
        registro = portas.ler_estado(self.pasta)
        self.assertEqual(registro["maquinas"]["ca150"]["pasta"], outra)

    def test_config_existente_ilegivel_nao_e_adicionado(self):
        with open(config.caminho_config("ca150", self.pasta), "w", encoding="utf-8") as f:
            f.write("{quebrado")
        # Ao lado do programa ele seria descoberto; aqui o teste é o adicionar explícito.
        dados = portas.ler_estado(self.pasta)
        dados["maquinas"] = {}
        portas.gravar_estado(self.pasta, dados)
        r = self.painel.adicionar("ca150", self.pasta, valores_ca150())
        self.assertFalse(r["ok"])

    def test_descobre_configs_ao_lado_do_programa(self):
        config.salvar("mek7300", dict(config.ler("mek7300", self.pasta)["valores"],
                                      franchise_credential_id=UUID_TESTE), self.pasta)
        painel = Painel(self.g, self.pasta)
        self.assertEqual(painel.adicionadas(), ["mek7300"])
        self.assertEqual(portas.ler_estado(self.pasta)["maquinas"]["mek7300"]["pasta"], "")

    def test_remover_mantem_o_arquivo_e_nao_volta_sozinha(self):
        self.assertTrue(self.painel.adicionar("ca150", self.pasta, valores_ca150())["ok"])
        caminho = config.caminho_config("ca150", self.pasta)
        r = self.painel.remover("ca150")
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.exists(caminho))
        self.assertEqual(self.painel.adicionadas(), [])
        self.assertEqual(Painel(self.g, self.pasta).adicionadas(), [])  # não redescobre
        # Adicionar de novo usa o arquivo que ficou.
        r = self.painel.adicionar("ca150", self.pasta)
        self.assertTrue(r["ok"] and r["existente"], r)

    def test_nao_remove_maquina_aberta(self):
        self.assertTrue(self.painel.adicionar("ca150", self.pasta, valores_ca150())["ok"])
        self.assertTrue(self.painel.iniciar("ca150")["ok"])
        self.addCleanup(self.painel.parar, "ca150")
        self.assertFalse(self.painel.remover("ca150")["ok"])
        self.assertEqual(self.painel.adicionadas(), ["ca150"])

    def test_maquina_abre_com_a_pasta_do_config_dela(self):
        outra = os.path.join(self.home, "configs_lab")
        self.assertTrue(self.painel.adicionar("ca150", outra, valores_ca150())["ok"])
        saida = os.path.join(self.home, "env.txt")
        self.comando = [sys.executable, "-c",
                        f"import os; open({saida!r}, 'w').write(os.environ.get('LEITOR_HL7_CONFIG_DIR', ''))"]
        self.assertTrue(self.painel.iniciar("ca150")["ok"])
        fim = time.time() + 10
        while not os.path.exists(saida) and time.time() < fim:
            time.sleep(0.05)
        time.sleep(0.2)
        self.assertEqual(ler(saida), outra)

    def test_config_loader_usa_a_pasta_do_painel(self):
        from shared.config_loader import _get_app_dir
        with mock.patch.dict(os.environ, {"LEITOR_HL7_CONFIG_DIR": self.pasta}):
            self.assertEqual(_get_app_dir("ca150"), self.pasta)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEITOR_HL7_CONFIG_DIR", None)
            self.assertNotEqual(_get_app_dir("ca150"), self.pasta)

    def test_config_iniciar_reiniciar_parar(self):
        self.assertFalse(self.painel.iniciar("ca150")["ok"])  # sem config
        self.assertTrue(self.painel.adicionar("ca150", self.pasta, valores_ca150())["ok"])
        r = self.painel.salvar_config("ca150", valores_ca150(tcp_port=5151))
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["rodando"])
        self.assertTrue(self.painel.iniciar("ca150")["ok"])
        self.assertTrue(self.painel.reiniciar("ca150")["ok"])
        self.assertTrue(self.painel.parar("ca150")["ok"])

    def test_log_da_maquina(self):
        caminho = maquinas.arquivo_log("vidas1600")
        os.makedirs(os.path.dirname(caminho))
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("".join(f"linha {i} ✓\n" for i in range(500)))
        r = self.painel.log("vidas1600", 3)
        self.assertEqual(r["linhas"], ["linha 497 ✓", "linha 498 ✓", "linha 499 ✓"])
        self.assertEqual(r["caminho"], caminho)


class PortaPyserial:
    def __init__(self, device, description="", hwid="", vid=None, pid=None, manufacturer=None):
        self.device, self.description, self.hwid = device, description, hwid
        self.vid, self.pid, self.manufacturer = vid, pid, manufacturer
        self.serial_number = self.location = None


class TestAcesso(ComPastas):
    """Senha mestra (hash), sessão de administrador e recusa no núcleo."""

    def setUp(self):
        super().setUp()
        self.relogio = [1000.0]
        self.sessao = acesso.Sessao(HASH_TESTE, duracao=60, relogio=lambda: self.relogio[0])
        patcher = mock.patch.object(processos, "health", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        g = Gerenciador(lambda mid: [sys.executable, "-c", "pass"], os.path.join(self.home, "saida"), self.pasta)
        self.painel = Painel(g, self.pasta, sessao=self.sessao)

    def test_hash_confere_so_a_senha_certa(self):
        h = acesso.gerar_hash("abc-123-xyz", iteracoes=1000)
        self.assertTrue(acesso.conferir("abc-123-xyz", h))
        self.assertFalse(acesso.conferir("abc-123-xyZ", h))
        self.assertFalse(acesso.conferir("abc-123-xyz", "lixo"))
        self.assertNotIn("abc-123-xyz", h)

    def test_hash_embutido_nunca_e_senha_em_texto(self):
        # Repositório público: só o hash PBKDF2 pode estar no código.
        h = acesso.HASH_SENHA_MESTRA
        if h:
            partes = h.split("$")
            self.assertEqual(partes[0], acesso.ALGORITMO)
            self.assertGreaterEqual(int(partes[1]), 600_000)
            self.assertEqual(len(partes[3]), 64)

    def test_nucleo_recusa_alterar_sem_admin(self):
        r = self.painel.adicionar("ca150", self.pasta, valores_ca150())
        self.assertFalse(r["ok"])
        self.assertTrue(r["login"])
        self.assertFalse(os.path.exists(config.caminho_config("ca150", self.pasta)))
        self.assertFalse(self.painel.salvar_config("ca150", valores_ca150())["ok"])

        self.assertTrue(self.painel.entrar(SENHA_TESTE)["ok"])
        self.assertTrue(self.painel.adicionar("ca150", self.pasta, valores_ca150())["ok"])
        self.painel.sair()
        r = self.painel.remover("ca150")
        self.assertFalse(r["ok"])
        self.assertTrue(r["login"])
        self.assertEqual(self.painel.adicionadas(), ["ca150"])

    def test_sessao_expira_sem_uso_e_renova_ao_alterar(self):
        self.assertTrue(self.painel.entrar(SENHA_TESTE)["ok"])
        self.relogio[0] += 50
        self.assertTrue(self.painel.adicionar("ca150", self.pasta, valores_ca150())["ok"])  # renova
        self.relogio[0] += 50
        self.assertTrue(self.painel.admin())
        self.relogio[0] += 11
        self.assertFalse(self.painel.admin())
        self.assertFalse(self.painel.estado()["admin"])

    def test_tentativas_erradas_bloqueiam_por_um_tempo(self):
        for _ in range(acesso.MAX_TENTATIVAS):
            self.assertFalse(self.painel.entrar("errada")["ok"])
        r = self.painel.entrar(SENHA_TESTE)
        self.assertFalse(r["ok"])
        self.assertIn("Tente de novo", r["mensagem"])
        self.relogio[0] += acesso.BLOQUEIO + 1
        self.assertTrue(self.painel.entrar(SENHA_TESTE)["ok"])

    def test_sem_senha_no_build_nada_libera(self):
        painel = Painel(self.painel.gerenciador, self.pasta, sessao=acesso.Sessao(""))
        self.assertFalse(painel.entrar("")["ok"])
        self.assertFalse(painel.entrar("qualquer")["ok"])
        self.assertFalse(painel.estado()["senha_definida"])

    def test_gravar_hash_troca_so_a_linha(self):
        copia = os.path.join(self.pasta, "acesso.py")
        shutil.copy(acesso.__file__, copia)
        acesso._gravar_hash("pbkdf2_sha256$600000$aa$bb", copia)
        texto = ler(copia)
        self.assertIn('HASH_SENHA_MESTRA = "pbkdf2_sha256$600000$aa$bb"', texto)
        import re
        self.assertEqual(len(re.findall(r"^HASH_SENHA_MESTRA = ", texto, re.M)), 1)

    def test_preferencia_de_tema(self):
        self.assertEqual(self.painel.preferencia("tema"), "sistema")
        self.assertFalse(self.painel.definir_preferencia("tema", "roxo"))
        self.assertTrue(self.painel.definir_preferencia("tema", "claro"))
        self.assertEqual(Painel(self.painel.gerenciador, self.pasta).preferencia("tema"), "claro")
        # Sem login: tema não é configuração de equipamento.
        self.assertFalse(self.painel.admin())


class RegistroFalso:
    """HKCU\\...\\Run em memória."""

    def __init__(self):
        self.valor = None

    def ler(self):
        return self.valor

    def gravar(self, valor):
        self.valor = valor

    def apagar(self):
        self.valor = None


class TestRaizEInicio(ComPastas):
    """Pasta raiz do programa, iniciar com o Windows e máquinas ao ligar."""

    def setUp(self):
        super().setUp()
        for var in (VARIAVEL_PASTA_CONFIG, VARIAVEL_PASTA_DADOS):
            os.environ.pop(var, None)  # o patch.dict do ComPastas restaura no fim
        patcher = mock.patch.object(processos, "health", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.registro = RegistroFalso()
        self.exe = os.path.join(self.pasta, "leitor.exe")
        g = Gerenciador(lambda mid: [sys.executable, "-c", "pass"], os.path.join(self.home, "saida"), self.pasta)
        self.painel = Painel(g, self.pasta, sessao=acesso.Sessao(HASH_TESTE), pasta_programa=self.pasta,
                             exe=self.exe, registro_inicio=self.registro)
        self.painel.entrar(SENHA_TESTE)
        self.nova = os.path.join(self.home, "Leitor Raiz")

    def adicionar(self, mid):
        valores = dict(config.ler(mid, self.pasta)["valores"], franchise_credential_id=UUID_TESTE)
        self.assertTrue(self.painel.adicionar(mid, self.pasta, valores)["ok"])

    def test_padrao_e_desktop_e_ao_lado_do_programa(self):
        self.assertEqual(self.painel.raiz(), "")
        self.assertEqual(maquinas.pasta_base("ca150"), os.path.join(self.home, "Desktop", "AnalisadorCA150"))
        self.assertEqual(raiz.pasta_painel(""), os.path.join(self.home, "Desktop", "LeitorHL7"))

    def test_trocar_raiz_copia_configs_e_leva_os_pendentes(self):
        self.adicionar("ca150")
        gerados = os.path.join(self.home, "Desktop", "AnalisadorCA150", "gerados")
        os.makedirs(gerados)
        with open(os.path.join(gerados, "exame.hl7"), "w", encoding="utf-8") as f:
            f.write("MSH")

        r = self.painel.trocar_raiz(self.nova)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["movidos"], 1)
        self.assertIn("config_ca150.json", r["copiados"])
        self.assertEqual(self.painel.raiz(), self.nova)
        self.assertEqual(self.painel.pasta_config, self.nova)
        self.assertEqual(self.painel.adicionadas(), ["ca150"])
        self.assertEqual(self.painel.pasta_de("ca150"), self.nova)
        self.assertTrue(os.path.exists(os.path.join(self.nova, "config_ca150.json")))
        self.assertTrue(os.path.exists(os.path.join(self.pasta, "config_ca150.json")))  # original fica
        self.assertEqual(os.listdir(gerados), [])
        self.assertEqual(os.listdir(maquinas.pasta_gerados("ca150")), ["exame.hl7"])
        self.assertEqual(maquinas.pasta_base("ca150"), os.path.join(self.nova, "AnalisadorCA150"))
        self.assertEqual(os.environ[VARIAVEL_PASTA_DADOS], self.nova)
        # Painel aberto de novo (outro processo) lê a mesma raiz.
        self.assertEqual(raiz.ler(self.pasta), self.nova)

    def test_config_que_ja_existe_na_raiz_nova_nao_e_sobrescrito(self):
        self.adicionar("ca150")
        os.makedirs(self.nova)
        destino = os.path.join(self.nova, "config_ca150.json")
        with open(destino, "w", encoding="utf-8") as f:
            json.dump({"tcp_port": 9999}, f)
        self.assertTrue(self.painel.trocar_raiz(self.nova)["ok"])
        self.assertEqual(json.loads(ler(destino)), {"tcp_port": 9999})

    def test_voltar_ao_padrao(self):
        self.assertTrue(self.painel.trocar_raiz(self.nova)["ok"])
        self.assertTrue(self.painel.trocar_raiz("")["ok"])
        self.assertEqual(self.painel.raiz(), "")
        self.assertEqual(self.painel.pasta_config, self.pasta)
        self.assertNotIn(VARIAVEL_PASTA_DADOS, os.environ)

    def test_trocar_raiz_exige_admin_e_maquinas_paradas(self):
        self.adicionar("ca150")
        with mock.patch.object(self.painel.gerenciador, "estado", return_value={"estado": "rodando"}):
            r = self.painel.trocar_raiz(self.nova)
        self.assertFalse(r["ok"])
        self.assertIn("Pare", r["mensagem"])
        self.painel.sair()
        self.assertTrue(self.painel.trocar_raiz(self.nova)["login"])
        self.assertEqual(self.painel.raiz(), "")
        self.assertFalse(os.path.exists(self.nova))

    def test_maquina_aberta_fora_do_painel_usa_a_raiz_sem_passar_por_cima(self):
        os.environ[VARIAVEL_PASTA_CONFIG] = "/pasta/da/maquina"
        raiz.aplicar_se_ausente(self.nova)
        self.assertEqual(os.environ[VARIAVEL_PASTA_CONFIG], "/pasta/da/maquina")
        self.assertEqual(os.environ[VARIAVEL_PASTA_DADOS], self.nova)

    def test_iniciar_com_o_windows(self):
        self.assertEqual(self.painel.inicio_automatico(), {"suportado": True, "ativo": False, "outro_exe": ""})
        self.assertTrue(self.painel.definir_inicio_automatico(True)["ok"])
        self.assertEqual(self.registro.valor, f'"{self.exe}" --ao-ligar')
        self.assertTrue(self.painel.inicio_automatico()["ativo"])
        # Valor de outro leitor.exe (programa movido): não conta como ativo.
        self.registro.valor = '"C:\\outro\\leitor.exe" --ao-ligar'
        self.assertFalse(self.painel.inicio_automatico()["ativo"])
        self.painel.sair()
        self.assertTrue(self.painel.definir_inicio_automatico(False)["login"])
        self.painel.entrar(SENHA_TESTE)
        self.assertTrue(self.painel.definir_inicio_automatico(False)["ok"])
        self.assertIsNone(self.registro.valor)

    def test_fora_do_windows_nao_registra(self):
        with mock.patch.object(inicializacao, "suportado", return_value=False):
            self.assertFalse(inicializacao.estado(self.exe)["suportado"])
            self.assertFalse(inicializacao.definir(True, self.exe)["ok"])

    def test_iniciar_ao_ligar_so_as_marcadas_e_prontas(self):
        self.adicionar("ca150")
        self.adicionar("bh5100")
        self.adicionar("vidas1600")
        self.assertTrue(self.painel.ao_ligar("ca150"))  # padrão: marcada
        self.assertTrue(self.painel.definir_ao_ligar("bh5100", False)["ok"])
        # VIDAS sem credencial: não está pronta.
        caminho = config.caminho_config("vidas1600", self.pasta)
        dados = json.loads(ler(caminho))
        dados["franchise_credential_id"] = ""
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(dados, f)
        with mock.patch.object(self.painel.gerenciador, "iniciar", return_value={"ok": True}) as iniciar:
            resultados = self.painel.iniciar_ao_ligar()
        iniciar.assert_called_once_with("ca150")
        self.assertEqual({r["id"]: r["ok"] for r in resultados}, {"ca150": True, "vidas1600": False})
        self.painel.sair()
        self.assertTrue(self.painel.definir_ao_ligar("ca150", False)["login"])
        self.assertTrue(self.painel.ao_ligar("ca150"))


class TestPortasVirtuais(unittest.TestCase):
    """O pyserial (SetupAPI) não enxerga com0com/VSPE: o registro SERIALCOMM e o QueryDosDevice completam."""

    def test_completa_com_registro_e_dos(self):
        sistema = [PortaPyserial("COM3", "USB-SERIAL CH340 (COM3)", "USB VID:PID=1A86:7523", 0x1A86, 0x7523)]
        registro = {"COM3": "\\Device\\USBSER000", "COM10": "\\Device\\com0com10", "COM12": "\\Device\\Serial2"}
        with mock.patch.object(portas.list_ports, "comports", return_value=sistema), \
                mock.patch.object(portas, "_portas_do_registro", return_value=registro), \
                mock.patch.object(portas, "_portas_dos", return_value=["COM3", "COM10", "COM11"]):
            lista = portas.listar_sistema()
        por_porta = {p["porta"]: p for p in lista}
        self.assertEqual(list(por_porta), ["COM3", "COM10", "COM11", "COM12"])
        self.assertTrue(por_porta["COM3"]["usb"])
        self.assertFalse(por_porta["COM3"]["virtual"])
        self.assertTrue(por_porta["COM10"]["virtual"])
        self.assertIn("com0com", por_porta["COM10"]["descricao"])
        self.assertTrue(por_porta["COM11"]["virtual"])   # só o Windows conhece o nome
        self.assertFalse(por_porta["COM12"]["virtual"])  # porta física sem SetupAPI

    def test_fora_do_windows_so_pyserial(self):
        self.assertEqual(portas._portas_do_registro(), {} if os.name != "nt" else portas._portas_do_registro())
        if os.name != "nt":
            self.assertEqual(portas._portas_dos(), [])


class RespostaFalsa:
    def __init__(self, status=200, dados=None, corpo=b"", texto=""):
        self.status_code, self._dados, self._corpo, self.text = status, dados, corpo, texto
        self.headers = {"Content-Length": str(len(corpo))}

    def json(self):
        if self._dados is None:
            raise ValueError("sem json")
        return self._dados

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._corpo), 7):
            yield self._corpo[i:i + 7]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SessaoFalsa:
    def __init__(self, rotas):
        self.rotas, self.pedidos = rotas, []

    def get(self, url, **kwargs):
        self.pedidos.append(url)
        r = self.rotas[url]
        if isinstance(r, Exception):
            raise r
        return r


class TestAtualizacao(ComPastas):
    """Releases do GitHub simuladas: nada sai da máquina."""

    def release(self, tag="v7.3.0", digest=None, corpo=b"novo exe", anexos=True):
        import hashlib
        digest = digest if digest is not None else "sha256:" + hashlib.sha256(corpo).hexdigest()
        assets = [{"name": "leitor.exe", "size": len(corpo), "digest": digest,
                   "browser_download_url": "https://exemplo/leitor.exe"}] if anexos else []
        return {"tag_name": tag, "body": "- novidade", "html_url": "https://exemplo/release", "assets": assets}

    def sessao(self, dados, corpo=b"novo exe", status=200):
        return SessaoFalsa({
            atualizacao.URL_ULTIMA: RespostaFalsa(status, dados),
            "https://exemplo/leitor.exe": RespostaFalsa(200, corpo=corpo),
        })

    def test_compara_versoes_como_numeros(self):
        self.assertTrue(atualizacao.mais_nova("v7.10.0", "7.9.9"))
        self.assertFalse(atualizacao.mais_nova("v7.2.0", "7.2.0"))
        self.assertFalse(atualizacao.mais_nova("v7.1.9", "7.2.0"))
        self.assertFalse(atualizacao.mais_nova("beta", "7.2.0"))

    def test_verificar(self):
        info = atualizacao.verificar(self.sessao(self.release()), "7.2.0")
        self.assertTrue(info["disponivel"])
        self.assertEqual(info["versao"], "7.3.0")
        self.assertEqual(len(info["sha256"]), 64)
        self.assertFalse(atualizacao.verificar(self.sessao(self.release("v7.2.0")), "7.2.0")["disponivel"])
        self.assertTrue(atualizacao.verificar(self.sessao(None, status=404), "7.2.0")["sem_release"])
        self.assertIn("erro", atualizacao.verificar(self.sessao(None, status=500), "7.2.0"))
        sem_rede = SessaoFalsa({atualizacao.URL_ULTIMA: atualizacao.requests.ConnectionError("off")})
        self.assertIn("erro", atualizacao.verificar(sem_rede, "7.2.0"))
        sem_anexo = atualizacao.verificar(self.sessao(self.release(anexos=False)), "7.2.0")
        self.assertIn("erro_anexo", sem_anexo)

    def test_baixar_confere_sha_e_instala(self):
        exe = os.path.join(self.pasta, "leitor.exe")
        with open(exe, "wb") as f:
            f.write(b"exe antigo")
        sessao = self.sessao(self.release())
        info = atualizacao.verificar(sessao, "7.2.0")
        progresso = []
        r = atualizacao.baixar(info, exe, progresso=lambda a, b: progresso.append((a, b)), sessao=sessao)
        self.assertTrue(r["ok"], r)
        self.assertEqual(progresso[-1], (8, 8))
        r = atualizacao.instalar(exe, r["arquivo"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(ler(exe, binario=True), b"novo exe")
        self.assertEqual(ler(exe + ".anterior", binario=True), b"exe antigo")
        self.assertFalse(os.path.exists(exe + ".novo"))

    def test_sha_errado_descarta_e_mantem_o_atual(self):
        exe = os.path.join(self.pasta, "leitor.exe")
        with open(exe, "wb") as f:
            f.write(b"exe antigo")
        sessao = self.sessao(self.release(digest="sha256:" + "0" * 64))
        info = atualizacao.verificar(sessao, "7.2.0")
        r = atualizacao.baixar(info, exe, sessao=sessao)
        self.assertFalse(r["ok"])
        self.assertFalse(os.path.exists(exe + ".novo"))
        self.assertEqual(ler(exe, binario=True), b"exe antigo")

    def test_sem_sha_publicado_nao_baixa(self):
        sessao = self.sessao(self.release(digest=""))
        info = atualizacao.verificar(sessao, "7.2.0")
        r = atualizacao.baixar(info, os.path.join(self.pasta, "leitor.exe"), sessao=sessao)
        self.assertFalse(r["ok"])
        self.assertEqual(sessao.pedidos, [atualizacao.URL_ULTIMA])  # nem tentou o download

    def test_notas_da_versao_do_changelog(self):
        caminho = os.path.join(self.pasta, "CHANGELOG.md")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("# Changelog\n\n## 7.2.0 - 2026-11-26\n\n- leitor universal\n\n## 7.1.1 - x\n\n- antiga\n")
        self.assertEqual(atualizacao.notas_da_versao(caminho, "7.2.0"), "- leitor universal")
        self.assertEqual(atualizacao.notas_da_versao(caminho, "9.9.9"), "")


try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    TEM_QT = True
except ImportError:
    TEM_QT = False


@unittest.skipUnless(TEM_QT, "PySide6 não instalado")
class TestJanelaQt(ComPastas):
    """A janela de verdade, renderizada fora da tela (QT_QPA_PLATFORM=offscreen)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        for alvo, valor in ((processos, "health"), (portas, "listar_sistema")):
            patcher = mock.patch.object(alvo, valor, return_value=None if valor == "health" else [])
            patcher.start()
            self.addCleanup(patcher.stop)
        from shared.painel import tema
        from shared.painel.janela import JanelaPrincipal
        tema.aplicar_paleta(self.app, tema.CLARO)
        g = Gerenciador(lambda mid: [sys.executable, "-c", "pass"], os.path.join(self.home, "saida"), self.pasta)
        self.painel = painel_admin(g, self.pasta)
        self.trocas_de_tema = []
        self.janela = JanelaPrincipal(self.painel, ao_trocar_tema=self.trocas_de_tema.append)
        self.addCleanup(self.janela.close)
        self.janela.show()
        self.esperar(lambda: self.janela.itens)

    def esperar(self, condicao, limite=10.0):
        fim = time.time() + limite
        while not condicao() and time.time() < fim:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertTrue(condicao(), "a janela não chegou ao estado esperado")

    def adicionar(self, *ids):
        for mid in ids:
            valores = dict(config.ler(mid, self.pasta)["valores"], franchise_credential_id=UUID_TESTE)
            self.assertTrue(self.painel.adicionar(mid, self.pasta, valores)["ok"])
        self.janela.atualizar()
        self.esperar(lambda: all(mid in self.janela.itens for mid in ids))

    def test_sem_maquinas_abre_o_adicionar(self):
        self.assertEqual(set(self.janela.itens), {"__adicionar__", "__portas__"})
        self.assertIs(self.janela.paginas.currentWidget(), self.janela.pagina_adicionar)
        self.janela.nav.setCurrentItem(self.janela.itens["__portas__"])
        self.assertIs(self.janela.paginas.currentWidget(), self.janela.pagina_portas)

    def test_adicionar_pela_janela(self):
        pagina = self.janela.pagina_adicionar
        pagina.modelo.setCurrentIndex(pagina.modelo.findData("ca150"))
        form = pagina.form
        self.assertEqual(form.maquina_id, "ca150")
        self.assertTrue(form.modo_adicionar and not form.existente)
        # Modo TCP: campos da serial escondidos.
        self.assertTrue(form.editores["com_port"]["widgets"][1].isHidden())
        self.assertFalse(form.editores["tcp_port"]["widgets"][1].isHidden())

        form.salvar()  # credencial vazia: recusado, com o erro no campo
        self.esperar(lambda: form.retorno.isVisible())
        self.assertFalse(form.editores["franchise_credential_id"]["erro"].isHidden())
        self.assertEqual(self.painel.adicionadas(), [])

        form.editores["franchise_credential_id"]["editor"].setText(UUID_TESTE)
        form.salvar()
        self.esperar(lambda: "ca150" in self.janela.itens)
        self.assertTrue(os.path.exists(config.caminho_config("ca150", self.pasta)))
        self.esperar(lambda: self.janela.pagina_maquina.m.get("id") == "ca150")
        self.assertIs(self.janela.paginas.currentWidget(), self.janela.pagina_maquina)
        self.esperar(lambda: self.janela.pagina_maquina.bt_iniciar.isEnabled())
        # O modelo sai da lista de adicionar.
        self.assertEqual(pagina.modelo.findData("ca150"), -1)

    def test_adicionar_com_arquivo_existente_e_so_leitura(self):
        outra = os.path.join(self.home, "outra")
        os.makedirs(outra)
        caminho = config.caminho_config("pkl", outra)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"com_port": "COM8", "franchise_credential_id": UUID_TESTE}, f)
        antes = ler(caminho)
        pagina = self.janela.pagina_adicionar
        pagina.pasta.setText(outra)
        pagina.modelo.setCurrentIndex(pagina.modelo.findData("pkl"))
        form = pagina.form
        self.assertTrue(form.existente)
        self.assertEqual(form.bt_salvar.text(), "Adicionar usando este arquivo")
        self.assertFalse(form.editores["com_port"]["widgets"][1].isEnabled())
        self.assertEqual(form.valores()["com_port"], "COM8")
        form.salvar()
        self.esperar(lambda: "pkl" in self.janela.itens)
        self.assertEqual(ler(caminho), antes)
        self.assertEqual(self.painel.pasta_de("pkl"), outra)

    def test_remover_pela_janela(self):
        self.adicionar("ca150")
        self.janela.selecionar_maquina("ca150")
        from PySide6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "exec", lambda self_: None), \
                mock.patch.object(QMessageBox, "clickedButton",
                                  lambda self_: [b for b in self_.buttons() if b.text() == "Remover"][0]):
            self.janela.pagina_maquina._remover()
        self.esperar(lambda: "ca150" not in self.janela.itens)
        self.assertTrue(os.path.exists(config.caminho_config("ca150", self.pasta)))

    def test_menu_da_versao(self):
        from shared.painel import janela_versao
        self.janela.abrir_versao(verificar=False)
        d = self.janela.dialogo_versao
        self.assertIsInstance(d, janela_versao.DialogoVersao)
        self.addCleanup(d.close)
        resposta = {"disponivel": True, "versao": "9.0.0", "notas": "- nova", "pagina": "https://x",
                    "url": "https://x/leitor.exe", "sha256": "a" * 64}
        with mock.patch.object(janela_versao.atualizacao, "verificar", return_value=resposta), \
                mock.patch.object(janela_versao.atualizacao, "pode_atualizar_sozinho", return_value=True):
            d.verificar()
            self.esperar(lambda: d.bt_verificar.isEnabled())
        self.assertIn("9.0.0", d.situacao.text())
        self.assertFalse(d.bt_baixar.isHidden())

    def test_nenhuma_janela_solta_pisca(self):
        # Widget sem pai que recebe show() vira janela própria por um instante
        # (o usuário viu caixinhas piscando ao trocar de máquina).
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QWidget

        self.adicionar("ca150", "bh5100", "pkl", "urit8021")
        extras = []
        janela = self.janela

        class Filtro(QObject):
            def eventFilter(self, obj, ev):
                if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget)
                        and obj.isWindow() and obj is not janela):
                    extras.append(f"{type(obj).__name__} {obj.objectName()!r}")
                return False

        filtro = Filtro()
        self.app.installEventFilter(filtro)
        self.addCleanup(self.app.removeEventFilter, filtro)
        for mid in ("ca150", "bh5100", "pkl", "urit8021"):
            self.janela.selecionar_maquina(mid)
            for aba in (0, 1, 2):
                self.janela.pagina_maquina.abas.setCurrentIndex(aba)
                self.app.processEvents()
        self.janela.nav.setCurrentItem(self.janela.itens["__adicionar__"])
        pagina = self.janela.pagina_adicionar
        for i in range(pagina.modelo.count()):
            pagina.modelo.setCurrentIndex(i)
            self.app.processEvents()
        self.janela.nav.setCurrentItem(self.janela.itens["__portas__"])
        self.app.processEvents()
        self.assertEqual(extras, [])

    def test_caminhos_clicaveis_abrem_a_pasta(self):
        from PySide6.QtTest import QTest
        from shared.painel import janela as modulo_janela

        self.adicionar("ca150")
        with mock.patch.object(modulo_janela, "abrir_no_explorador") as abrir:
            QTest.mouseClick(self.janela.st_configs, Qt.MouseButton.LeftButton)
            abrir.assert_called_with(self.pasta)
            self.janela.selecionar_maquina("ca150")
            form = self.janela.pagina_maquina.form
            QTest.mouseClick(form.arquivo, Qt.MouseButton.LeftButton)
            abrir.assert_called_with(config.caminho_config("ca150", self.pasta))

    def test_abrir_caminho_que_ainda_nao_existe(self):
        # Log de máquina que nunca rodou: abre a pasta existente mais próxima.
        from shared.painel import janela as modulo_janela

        inexistente = os.path.join(self.home, "Desktop", "AnalisadorCA150", "analisador_ca150.log")
        with mock.patch.object(modulo_janela.QDesktopServices, "openUrl", return_value=True) as abrir:
            self.assertTrue(modulo_janela.abrir_no_explorador(inexistente))
        self.assertEqual(os.path.normpath(abrir.call_args[0][0].toLocalFile()), os.path.normpath(self.home))

    def test_sem_admin_a_configuracao_fica_bloqueada(self):
        self.adicionar("ca150")
        self.janela.selecionar_maquina("ca150")
        form = self.janela.pagina_maquina.form
        self.assertTrue(form.editores["tcp_port"]["widgets"][1].isEnabled())
        self.assertTrue(form.bloqueio.isHidden())
        self.assertEqual(self.janela.bt_acesso.text(), "Administrador")

        self.janela.sair_admin()
        self.assertFalse(form.editores["tcp_port"]["widgets"][1].isEnabled())
        self.assertFalse(form.bt_salvar.isEnabled())
        self.assertFalse(form.bloqueio.isHidden())
        self.assertFalse(self.janela.pagina_adicionar.form.bt_salvar.isEnabled())
        self.assertEqual(self.janela.bt_acesso.text(), "Somente leitura")

        # Remover pede a senha; cancelado, nada acontece.
        with mock.patch.object(self.janela, "entrar", return_value=False) as entrar:
            self.janela.pagina_maquina.bt_remover.click()
        entrar.assert_called_once()
        self.assertIn("ca150", self.painel.adicionadas())

    def test_dialogo_de_login(self):
        from shared.painel.janela import DialogoLogin
        self.janela.sair_admin()
        d = DialogoLogin(self.janela, self.painel, self.janela.ponte, self.janela.c)
        self.addCleanup(d.deleteLater)
        d.senha.setText("errada")
        d.entrar()
        self.esperar(lambda: not d.erro.isHidden())
        self.assertIn("incorreta", d.erro.text())
        self.assertFalse(self.painel.admin())
        d.senha.setText(SENHA_TESTE)
        d.entrar()
        self.esperar(lambda: d.result() == 1)
        self.assertTrue(self.painel.admin())

    def test_login_bloqueado_sem_senha_no_build(self):
        from shared.painel.janela import DialogoLogin
        painel = Painel(self.painel.gerenciador, self.pasta, sessao=acesso.Sessao(""))
        d = DialogoLogin(self.janela, painel, self.janela.ponte, self.janela.c)
        self.addCleanup(d.deleteLater)
        self.assertFalse(d.bt_entrar.isEnabled())
        self.assertIn("sem senha mestra", d.erro.text())

    def test_marcar_iniciar_ao_ligar_pela_janela(self):
        self.adicionar("ca150")
        self.janela.selecionar_maquina("ca150")
        ck = self.janela.pagina_maquina.ck_ao_ligar
        self.esperar(lambda: ck.isChecked())
        ck.setChecked(False)
        self.esperar(lambda: not self.painel.ao_ligar("ca150"))
        self.janela.sair_admin()
        self.assertFalse(ck.isEnabled())

    def test_dialogo_da_pasta_raiz(self):
        from shared.painel.janela import DialogoRaiz
        programa = tempfile.mkdtemp(prefix="painel_prog_")
        self.addCleanup(shutil.rmtree, programa, True)
        self.painel.pasta_programa = programa
        nova = os.path.join(self.home, "raiz")
        with mock.patch.dict(os.environ):
            d = DialogoRaiz(self.janela, self.painel, self.janela.ponte, self.janela.c)
            self.addCleanup(d.deleteLater)
            d.pasta.setText(nova)
            d.aplicar()
            self.esperar(lambda: not d.retorno.isHidden())
            self.assertEqual(self.painel.raiz(), nova)
            self.assertEqual(self.painel.pasta_config, nova)
        self.painel.pasta_config = self.pasta

    def _assistente(self):
        from shared.painel.janela import DialogoPrimeiroUso
        programa = tempfile.mkdtemp(prefix="painel_prog_")
        self.addCleanup(shutil.rmtree, programa, True)
        self.painel.pasta_programa = programa
        self.addCleanup(setattr, self.painel, "pasta_config", self.pasta)
        self.painel.sair()
        d = DialogoPrimeiroUso(None, self.painel, self.janela.ponte, self.janela.c)
        self.addCleanup(d.deleteLater)
        return d, programa

    def test_primeiro_uso_pede_senha_e_cria_a_pasta_raiz(self):
        d, programa = self._assistente()
        self.assertFalse(raiz.existe(programa))
        self.assertEqual(d.paginas.currentIndex(), 0)
        d.senha.setText("errada")
        d._avancar()
        self.esperar(lambda: not d.retorno.isHidden())
        self.assertEqual(d.paginas.currentIndex(), 0)
        d.senha.setText(SENHA_TESTE)
        d._avancar()
        self.esperar(lambda: d.paginas.currentIndex() == 1)

        d.pasta.setText(self.home)
        from shared.painel import janela as modulo_janela
        with mock.patch.object(modulo_janela.QInputDialog, "getText", return_value=("Raiz Nova", True)):
            d._criar_pasta()
        nova = os.path.join(self.home, "Raiz Nova")
        self.assertTrue(os.path.isdir(nova))
        self.assertEqual(d.pasta.text(), nova)
        with mock.patch.dict(os.environ):
            d._avancar()
            self.esperar(lambda: d.result() == 1)
            self.assertTrue(raiz.existe(programa))
            self.assertEqual(self.painel.raiz(), nova)

    def test_primeiro_uso_sem_pasta_raiz_nao_pergunta_de_novo(self):
        d, programa = self._assistente()
        self.painel.entrar(SENHA_TESTE)
        d._mostrar_passo(2)
        d.padrao.setChecked(True)
        self.assertFalse(d.pasta.isEnabled())
        with mock.patch.dict(os.environ):
            d._avancar()
            self.esperar(lambda: d.result() == 1)
        self.assertTrue(raiz.existe(programa))
        self.assertEqual(raiz.ler(programa), "")

    def test_criar_pasta_recusa_nome_invalido(self):
        d, _ = self._assistente()
        d.pasta.setText(self.home)
        from shared.painel import janela as modulo_janela
        with mock.patch.object(modulo_janela.QInputDialog, "getText", return_value=("a/b", True)):
            d._criar_pasta()
        self.assertFalse(os.path.exists(os.path.join(self.home, "a")))
        self.assertIn("não pode", d.retorno.texto.text())

    def test_trocar_tema_grava_e_avisa(self):
        self.janela.trocar_tema("escuro")
        self.assertEqual(self.trocas_de_tema, ["escuro"])
        self.assertEqual(Painel(self.painel.gerenciador, self.pasta).preferencia("tema"), "escuro")
        self.janela.trocar_tema("escuro")  # já é o atual: nada
        self.assertEqual(self.trocas_de_tema, ["escuro"])

    def test_captura_da_janela(self):
        # Garante que a janela inteira desenha sem erro (e serve de prévia).
        destino = os.environ.get("CAPTURA_JANELA")
        imagem = self.janela.grab()
        self.assertFalse(imagem.isNull())
        if destino:
            imagem.save(destino)


if __name__ == "__main__":
    unittest.main()
