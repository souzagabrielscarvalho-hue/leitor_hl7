"""
Núcleo do painel: o que a janela mostra e faz, sem nada de interface.

Junta o registro das máquinas, o config, os processos e as portas seriais. A
janela (shared/painel/janela.py) só chama estes métodos, numa thread à parte,
porque o estado consulta o /health de cada máquina.

Máquinas adicionadas: o painel só lista as máquinas adicionadas neste
computador. A lista e a pasta do config de cada uma ficam em
leitor_painel.json, ao lado do programa ("maquinas": {id: {"pasta": ...}}).
Pasta vazia quer dizer "ao lado do programa" (continua valendo se a pasta do
programa mudar). Configs que já existem ao lado do programa entram na lista
sozinhos, a não ser que a máquina tenha sido removida pelo painel.

Acesso: ver estado, log e portas, iniciar e parar são livres. Alterar a
configuração (salvar, adicionar, remover) exige a sessão de administrador
(shared/painel/acesso.py); a recusa é aqui, não só na tela.
"""

import logging
import os
import shutil
import sys
import threading
from typing import Any, Dict, List, Optional

from shared import maquinas
from shared.config_loader import pasta_dados
from shared.painel import acesso, config, inicializacao, portas, raiz
from shared.painel.processos import Gerenciador, contar_pendentes, ultimas_linhas
from shared.versao import VERSAO

ABERTA = ("rodando", "iniciando", "sem_resposta")
PREFERENCIAS_PADRAO = {"tema": "sistema"}
TEMAS = ("sistema", "claro", "escuro")
SEM_ACESSO = "Entre como administrador para alterar a configuração."


def descrever_conexao(valores: Dict[str, Any]) -> str:
    if valores.get("modo") == "tcp":
        return f"TCP {valores.get('tcp_port')}"
    return f"{valores.get('com_port') or '?'} · {valores.get('baud_rate')} baud"


def _mesma_pasta(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class Painel:
    def __init__(
        self,
        gerenciador: Gerenciador,
        pasta_config: Optional[str] = None,
        sessao: Optional[acesso.Sessao] = None,
        pasta_programa: Optional[str] = None,
        exe: Optional[str] = None,
        registro_inicio: Any = None,
    ) -> None:
        self.gerenciador = gerenciador
        self.sessao = sessao or acesso.Sessao()
        # Pasta do leitor.exe (leitor_raiz.json) e o .exe registrado para iniciar com o Windows.
        self.pasta_programa = pasta_programa or raiz.pasta_programa()
        self.exe = exe or sys.executable
        self._registro_inicio = registro_inicio
        # Pasta do programa: config padrão e leitor_painel.json.
        self.pasta_config = pasta_config or config.app_dir()
        self._lock = threading.Lock()
        self.gerenciador.definir_pastas(self.pasta_de)
        self._descobrir()

    # ── REGISTRO ────────────────────────────────────────────

    def _ler_registro(self) -> Dict[str, Any]:
        dados = portas.ler_estado(self.pasta_config)
        if not isinstance(dados.get("maquinas"), dict):
            dados["maquinas"] = {}
        if not isinstance(dados.get("removidas"), list):
            dados["removidas"] = []
        return dados

    def _descobrir(self) -> None:
        """Configs já existentes ao lado do programa entram na lista (instalação antiga)."""
        with self._lock:
            dados = self._ler_registro()
            novas = []
            for m in maquinas.MAQUINAS:
                if m.id in dados["maquinas"] or m.id in dados["removidas"]:
                    continue
                if os.path.exists(config.caminho_config(m.id, self.pasta_config)):
                    dados["maquinas"][m.id] = {"pasta": "", "ao_ligar": True}
                    novas.append(m.titulo)
            if novas and portas.gravar_estado(self.pasta_config, dados):
                logging.info(f"✓ Equipamentos com config ao lado do programa adicionados à lista: {', '.join(novas)}.")

    # ── ACESSO E PREFERÊNCIAS ───────────────────────────────

    def admin(self) -> bool:
        return self.sessao.ativa()

    def entrar(self, senha: str) -> Dict[str, Any]:
        return self.sessao.entrar(senha)

    def sair(self) -> None:
        self.sessao.sair()

    def _negado(self, acao: str) -> Optional[str]:
        """Mensagem de recusa se não houver administrador; renova a sessão se houver."""
        if self.sessao.renovar():
            return None
        logging.warning(f"✗ {acao} recusado: sem login de administrador.")
        return SEM_ACESSO

    def preferencia(self, chave: str) -> Any:
        prefs = self._ler_registro().get("preferencias")
        valor = prefs.get(chave) if isinstance(prefs, dict) else None
        return PREFERENCIAS_PADRAO.get(chave) if valor is None else valor

    def definir_preferencia(self, chave: str, valor: Any) -> bool:
        if chave == "tema" and valor not in TEMAS:
            return False
        with self._lock:
            dados = self._ler_registro()
            if not isinstance(dados.get("preferencias"), dict):
                dados["preferencias"] = {}
            dados["preferencias"][chave] = valor
            ok = portas.gravar_estado(self.pasta_config, dados)
        if ok:
            logging.info(f"✓ Preferência do painel: {chave} = {valor}.")
        return ok

    def adicionadas(self) -> List[str]:
        """Ids das máquinas adicionadas, na ordem do registro de máquinas."""
        registradas = self._ler_registro()["maquinas"]
        return [m.id for m in maquinas.MAQUINAS if m.id in registradas]

    def pasta_de(self, maquina_id: str) -> str:
        pasta = (self._ler_registro()["maquinas"].get(maquina_id) or {}).get("pasta") or ""
        return pasta or self.pasta_config

    def adicionar(self, maquina_id: str, pasta: str, valores: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Adiciona a máquina com o config em `pasta`. Se config_<id>.json já existir
        lá, ele é usado como está (nunca é sobrescrito) e `valores` é ignorado.
        """
        titulo = maquinas.obter(maquina_id).titulo
        negado = self._negado(f"Adicionar {titulo}")
        if negado:
            return {"ok": False, "erros": {"_arquivo": negado}, "login": True}
        pasta = os.path.abspath(pasta or self.pasta_config)
        if maquina_id in self.adicionadas():
            return {"ok": False, "erros": {"_arquivo": f"{titulo} já está na lista."}}
        try:
            os.makedirs(pasta, exist_ok=True)
        except OSError as e:
            logging.error(f"✗ {titulo} não adicionada: pasta {pasta} indisponível ({e}).")
            return {"ok": False, "erros": {"_arquivo": f"Pasta indisponível: {e}"}}

        existente = config.ler(maquina_id, pasta)
        if existente["existe"]:
            if existente["erro_leitura"]:
                logging.error(f"✗ {titulo} não adicionada: {existente['caminho']} ilegível ({existente['erro_leitura']}).")
                return {"ok": False, "erros": {"_arquivo": f"O arquivo existente não pôde ser lido: {existente['erro_leitura']}"}}
            resultado = {"ok": True, "erros": {}, "caminho": existente["caminho"], "existente": True}
        else:
            resultado = config.salvar(maquina_id, valores or {}, pasta)
            if not resultado["ok"]:
                return resultado
            resultado["existente"] = False

        with self._lock:
            dados = self._ler_registro()
            dados["maquinas"][maquina_id] = {
                "pasta": "" if _mesma_pasta(pasta, self.pasta_config) else pasta, "ao_ligar": True,
            }
            dados["removidas"] = [m for m in dados["removidas"] if m != maquina_id]
            if not portas.gravar_estado(self.pasta_config, dados):
                return {"ok": False, "erros": {"_arquivo": "Não foi possível gravar a lista de equipamentos."}}
        origem = "config existente, sem alterar" if resultado["existente"] else "config novo"
        logging.info(f"✓ {titulo} adicionada ({origem}: {resultado['caminho']}).")
        self._lembrar_adaptador(maquina_id)
        return resultado

    def remover(self, maquina_id: str) -> Dict[str, Any]:
        """Tira a máquina da lista. O config fica no disco."""
        titulo = maquinas.obter(maquina_id).titulo
        negado = self._negado(f"Remover {titulo}")
        if negado:
            return {"ok": False, "mensagem": negado, "login": True}
        if self.gerenciador.estado(maquina_id)["estado"] in ABERTA:
            logging.warning(f"✗ {titulo} não removida da lista: está em execução.")
            return {"ok": False, "mensagem": "Pare a máquina antes de removê-la da lista."}
        caminho = config.caminho_config(maquina_id, self.pasta_de(maquina_id))
        with self._lock:
            dados = self._ler_registro()
            dados["maquinas"].pop(maquina_id, None)
            if maquina_id not in dados["removidas"]:
                dados["removidas"].append(maquina_id)
            if not portas.gravar_estado(self.pasta_config, dados):
                return {"ok": False, "mensagem": "Não foi possível gravar a lista de equipamentos."}
        logging.info(f"✓ {titulo} removida da lista (config mantido em {caminho}).")
        return {"ok": True, "caminho": caminho}

    # ── INICIAR AO LIGAR ────────────────────────────────────

    def ao_ligar(self, maquina_id: str) -> bool:
        """Máquina iniciada sozinha quando o painel abre com o Windows (padrão: sim)."""
        item = self._ler_registro()["maquinas"].get(maquina_id) or {}
        return bool(item.get("ao_ligar", True))

    def definir_ao_ligar(self, maquina_id: str, ativar: bool) -> Dict[str, Any]:
        titulo = maquinas.obter(maquina_id).titulo
        negado = self._negado(f"Alterar \"iniciar ao ligar\" da {titulo}")
        if negado:
            return {"ok": False, "mensagem": negado, "login": True}
        with self._lock:
            dados = self._ler_registro()
            if maquina_id not in dados["maquinas"]:
                return {"ok": False, "mensagem": f"{titulo} não está na lista."}
            dados["maquinas"][maquina_id]["ao_ligar"] = bool(ativar)
            if not portas.gravar_estado(self.pasta_config, dados):
                return {"ok": False, "mensagem": "Não foi possível gravar a lista de equipamentos."}
        logging.info(f"✓ {titulo}: iniciar ao ligar {'ativado' if ativar else 'desativado'}.")
        return {"ok": True}

    def iniciar_ao_ligar(self) -> List[Dict[str, Any]]:
        """Painel aberto com o Windows: inicia as máquinas marcadas que estão paradas e prontas."""
        resultados = []
        for mid in self.adicionadas():
            titulo = maquinas.obter(mid).titulo
            if not self.ao_ligar(mid):
                logging.info(f"Ao ligar: {titulo} não marcada para iniciar.")
                continue
            if self.gerenciador.estado(mid)["estado"] in ABERTA:
                logging.info(f"Ao ligar: {titulo} já está em execução.")
                continue
            pronta, motivo = config.pronta(mid, self.pasta_de(mid))
            if not pronta:
                logging.error(f"✗ Ao ligar: {titulo} não iniciada ({motivo}).")
                resultados.append({"id": mid, "ok": False, "mensagem": motivo})
                continue
            r = self.gerenciador.iniciar(mid)
            resultados.append({"id": mid, **r})
        return resultados

    def inicio_automatico(self) -> Dict[str, Any]:
        return inicializacao.estado(self.exe, self._registro_inicio)

    def definir_inicio_automatico(self, ativar: bool) -> Dict[str, Any]:
        negado = self._negado("Alterar o início com o Windows")
        if negado:
            return {"ok": False, "mensagem": negado, "login": True}
        return inicializacao.definir(ativar, self.exe, self._registro_inicio)

    # ── PASTA RAIZ ──────────────────────────────────────────

    def raiz(self) -> str:
        return raiz.ler(self.pasta_programa)

    def trocar_raiz(self, nova: str) -> Dict[str, Any]:
        """
        Muda a pasta raiz ("" volta ao padrão). Copia para a nova os configs que
        seguem a raiz e o leitor_painel.json (sem sobrescrever o que já houver lá)
        e move os exames pendentes de gerados/; o histórico (enviados/, logs/)
        fica na pasta antiga. Exige as máquinas paradas.
        """
        negado = self._negado("Trocar a pasta raiz")
        if negado:
            return {"ok": False, "mensagem": negado, "login": True}
        abertas = [maquinas.obter(m).titulo for m in self.adicionadas()
                   if self.gerenciador.estado(m)["estado"] in ABERTA]
        if abertas:
            logging.warning(f"✗ Pasta raiz não trocada: máquinas em execução ({', '.join(abertas)}).")
            return {"ok": False, "mensagem": f"Pare antes as máquinas em execução: {', '.join(abertas)}."}

        nova = os.path.abspath(nova) if nova and nova.strip() else ""
        config_antiga, dados_antiga = self.pasta_config, pasta_dados()
        config_nova = nova or self.pasta_programa
        dados_nova = nova or os.path.join(os.path.expanduser("~"), "Desktop")
        if nova == self.raiz() and _mesma_pasta(config_antiga, config_nova):
            # Primeiro uso escolhendo o padrão: grava a escolha para não perguntar de novo.
            if not raiz.existe(self.pasta_programa) and not raiz.gravar(nova, self.pasta_programa):
                return {"ok": False, "mensagem": "Não foi possível gravar leitor_raiz.json ao lado do programa."}
            return {"ok": True, "copiados": [], "movidos": 0, "mensagem": "A pasta raiz já é essa."}
        try:
            os.makedirs(config_nova, exist_ok=True)
            os.makedirs(dados_nova, exist_ok=True)
        except OSError as e:
            logging.error(f"✗ Pasta raiz não trocada: {config_nova} indisponível ({e}).")
            return {"ok": False, "mensagem": f"Pasta indisponível: {e}"}

        registro = self._ler_registro()
        copiados: List[str] = []
        if not _mesma_pasta(config_antiga, config_nova):
            arquivos = [portas.ARQUIVO_PAINEL] + [
                os.path.basename(config.caminho_config(mid, config_antiga))
                for mid, item in registro["maquinas"].items() if not (item or {}).get("pasta")
            ]
            for nome in arquivos:
                origem, destino = os.path.join(config_antiga, nome), os.path.join(config_nova, nome)
                if not os.path.exists(origem):
                    continue
                if os.path.exists(destino):
                    logging.info(f"{nome} já existe em {config_nova}: usado como está.")
                    continue
                try:
                    shutil.copy2(origem, destino)
                    copiados.append(nome)
                    logging.info(f"✓ {nome} copiado para {config_nova}.")
                except OSError as e:
                    logging.error(f"✗ {nome} não copiado para {config_nova}: {e}")
                    return {"ok": False, "mensagem": f"Não foi possível copiar {nome}: {e}"}

        movidos, presos = 0, 0
        if not _mesma_pasta(dados_antiga, dados_nova):
            for mid in registro["maquinas"]:
                pasta = f"Analisador{maquinas.nome_maquina(mid)}"
                origem = os.path.join(dados_antiga, pasta, "gerados")
                destino = os.path.join(dados_nova, pasta, "gerados")
                try:
                    pendentes = os.listdir(origem)
                except OSError:
                    continue
                if pendentes:
                    os.makedirs(destino, exist_ok=True)
                for nome in pendentes:
                    if os.path.exists(os.path.join(destino, nome)):
                        presos += 1
                        logging.error(f"✗ {pasta}/gerados/{nome} não movido: já existe na pasta nova.")
                        continue
                    try:
                        shutil.move(os.path.join(origem, nome), os.path.join(destino, nome))
                        movidos += 1
                    except OSError as e:
                        presos += 1
                        logging.error(f"✗ {pasta}/gerados/{nome} não movido: {e}")
                if pendentes:
                    logging.info(f"✓ {pasta}: exames pendentes de gerados/ levados para {destino}.")

        if not raiz.gravar(nova, self.pasta_programa):
            return {"ok": False, "mensagem": "Não foi possível gravar leitor_raiz.json ao lado do programa."}
        raiz.aplicar(nova)
        with self._lock:
            self.pasta_config = config_nova
        self._descobrir()
        logging.info(f"✓ Pasta raiz: {nova or 'padrão (configs ao lado do programa, dados na Área de Trabalho)'}.")
        mensagem = "Pasta raiz alterada."
        if movidos:
            mensagem += f" {movidos} exame(s) pendente(s) levados para a pasta nova."
        if presos:
            mensagem += f" {presos} exame(s) ficaram na pasta antiga (veja o log do painel)."
        return {"ok": True, "copiados": copiados, "movidos": movidos, "presos": presos, "mensagem": mensagem}

    # ── ESTADO ──────────────────────────────────────────────

    def estado(self) -> Dict[str, Any]:
        try:
            sistema = portas.listar_sistema()
        except Exception as e:
            logging.error(f"✗ Não foi possível listar as portas seriais: {e}")
            sistema = []
        adicionadas = self.adicionadas()
        lista = []
        for mid in adicionadas:
            m = maquinas.obter(mid)
            pasta = self.pasta_de(mid)
            dados = config.ler(mid, pasta)
            valores = dados["valores"]
            pronta, motivo = config.pronta(mid, pasta)
            sugestao = None
            if valores.get("modo", "serial") == "serial" and sistema:
                sugestao = portas.sugerir_porta(self.pasta_config, mid, valores.get("com_port", ""), sistema)
            lista.append({
                "id": mid,
                "titulo": m.titulo,
                "tipo": m.tipo,
                "protocolo": m.protocolo,
                "sentido": m.sentido,
                "conexao": descrever_conexao(valores),
                "configurada": dados["existe"],
                "pasta_config": pasta,
                "pronta": pronta,
                "motivo": motivo,
                "pendentes": contar_pendentes(mid),
                "log": maquinas.arquivo_log(mid),
                "sugestao_porta": sugestao,
                "ao_ligar": self.ao_ligar(mid),
                **self.gerenciador.estado(mid),
            })
        disponiveis = [
            {"id": m.id, "titulo": m.titulo, "tipo": m.tipo, "protocolo": m.protocolo, "sentido": m.sentido}
            for m in maquinas.MAQUINAS if m.id not in adicionadas
        ]
        return {
            "versao": VERSAO, "pasta_config": self.pasta_config, "maquinas": lista, "disponiveis": disponiveis,
            "admin": self.sessao.ativa(), "senha_definida": self.sessao.definida,
            "raiz": self.raiz(),
        }

    # ── CONFIG ──────────────────────────────────────────────

    def ler_config(self, maquina_id: str, pasta: Optional[str] = None) -> Dict[str, Any]:
        return config.ler(maquina_id, pasta or self.pasta_de(maquina_id))

    def salvar_config(self, maquina_id: str, valores: Dict[str, Any]) -> Dict[str, Any]:
        negado = self._negado(f"Salvar a configuração de {maquinas.obter(maquina_id).titulo}")
        if negado:
            return {"ok": False, "erros": {"_arquivo": negado}, "login": True}
        resultado = config.salvar(maquina_id, valores, self.pasta_de(maquina_id))
        if resultado["ok"]:
            self._lembrar_adaptador(maquina_id)
            resultado["rodando"] = self.gerenciador.estado(maquina_id)["estado"] == "rodando"
        return resultado

    def _lembrar_adaptador(self, maquina_id: str) -> None:
        limpos = self.ler_config(maquina_id)["valores"]
        if limpos.get("modo", "serial") != "serial":
            return
        try:
            portas.lembrar_adaptador(self.pasta_config, maquina_id, limpos.get("com_port", ""), portas.listar_sistema())
        except Exception as e:
            logging.error(f"✗ Adaptador de {maquina_id} não lembrado: {e}")

    # ── PROCESSOS ───────────────────────────────────────────

    def iniciar(self, maquina_id: str) -> Dict[str, Any]:
        return self.gerenciador.iniciar(maquina_id)

    def parar(self, maquina_id: str) -> Dict[str, Any]:
        return self.gerenciador.parar(maquina_id)

    def reiniciar(self, maquina_id: str) -> Dict[str, Any]:
        if self.gerenciador.estado(maquina_id)["estado"] != "parada":
            r = self.gerenciador.parar(maquina_id)
            if not r["ok"]:
                return r
        return self.gerenciador.iniciar(maquina_id)

    # ── LOG E PORTAS ────────────────────────────────────────

    def log(self, maquina_id: str, quantidade: int = 500) -> Dict[str, Any]:
        caminho = maquinas.arquivo_log(maquina_id)
        return {
            "caminho": caminho,
            "linhas": ultimas_linhas(caminho, quantidade),
            "saida": ultimas_linhas(self.gerenciador.arquivo_saida(maquina_id), 50),
        }

    def listar_portas(self, testar: bool = False) -> List[Dict[str, Any]]:
        # Só máquinas adicionadas e com config salvo: sem ele, a COM é só o padrão do código.
        configs = {}
        for mid in self.adicionadas():
            dados = self.ler_config(mid)
            if dados["existe"]:
                configs[mid] = dados["valores"]
        return portas.listar(
            configs,
            rodando=lambda mid: self.gerenciador.estado(mid)["estado"] == "rodando",
            testar=testar,
        )
