"""
Analisador URIT CA-150 (bioquímica): HL7 v2.3.1 por serial ou TCP/IP.

Segue o "CA Series Automatic Chemistry Analyzer HL7 Interface Development
Reference Manual" V1.01 (2023.03). A máquina é o cliente e este programa é o
servidor do sistema de laboratório:

- Consulta por etiqueta: a máquina manda QRY^Q02 com a etiqueta em QRD.8. O
  programa pergunta ao lab-apoio (GET /api/interfacing/tag, o centralizador do
  interfaceamento) e responde QCK^Q02 e, ~1 s depois, DSR^Q03 com o paciente e
  os itens. A máquina confirma com ACK^Q03. Etiqueta inexistente ou sem itens
  por interface: responde só o QCK^Q02 com QAK|SR|NF.
- Resultado: a máquina manda ORU^R01. O programa responde ACK^R01 na hora e
  guarda a mensagem em gerados/; a thread de envio manda para
  POST /api/integration/ca-150. Resultado de controle de qualidade
  (MSH.16 = 2) recebe ACK, mas não é enviado.

Conexão escolhida no config_ca150.json:
- "modo": "serial" usa com_port e baud_rate, como as outras máquinas;
- "modo": "tcp" escuta em tcp_host:tcp_port e a máquina conecta no IP do PC.
"""

import os
import sys
import json
import time
import socket
import logging
import datetime
import threading
import unicodedata
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple

import requests

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador

# ═══════════════════════════════════════════════════════════
# PROTOCOLO
# ═══════════════════════════════════════════════════════════

SB = chr(0x0B)
EB = chr(0x1C)
CR = chr(0x0D)

# O manual declara o conjunto de caracteres "ANSI" (MSH.18).
CODIFICACAO = "cp1252"

# Itens calculados sem valor válido chegam com este número (ex.: A/G, ALT/AST
# no exemplo do manual).
VALORES_INVALIDOS = {"10000000"}

# Material do sistema -> tipo de amostra do dicionário da máquina (DSP|3).
# Material sem correspondência vai como está cadastrado.
TIPOS_DE_AMOSTRA = {
    "SORO": "Serum",
    "PLASMA": "Plasma",
    "URINA": "Urine",
}

# Tempo entre o QCK^Q02 e o DSR^Q03 recomendado pelo manual.
INTERVALO_DSR = 1.0
TIMEOUT_CONSULTA = 10
TCP_RETRY_INTERVAL = 30

URL_BASE = "https://apoio.internal.vidaexame.com/api"


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE MENSAGEM (sem estado, testáveis isoladamente)
# ═══════════════════════════════════════════════════════════

def segmentos(mensagem: str) -> List[str]:
    limpa = mensagem.replace(SB, "").replace(EB, "")
    limpa = limpa.replace("\r\n", "\r").replace("\n", "\r")
    return [s for s in limpa.split("\r") if s.strip()]


def campos_do_segmento(mensagem: str, nome: str) -> List[str]:
    for seg in segmentos(mensagem):
        if seg.startswith(nome + "|"):
            return seg.split("|")
    return []


def campo(campos: List[str], indice: int) -> str:
    return campos[indice].strip() if len(campos) > indice else ""


def tipo_mensagem(mensagem: str) -> str:
    """Tipo do MSH.9, ex.: "ORU^R01"."""
    return campo(campos_do_segmento(mensagem, "MSH"), 8).upper()


def texto_hl7(valor: Any) -> str:
    """Texto seguro para um campo: sem separadores HL7 nem quebras de linha."""
    texto = "" if valor is None else str(valor)
    for caractere in "|^~\\&\r\n":
        texto = texto.replace(caractere, " ")
    return texto.strip()


def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def enquadrar(linhas: List[str]) -> bytes:
    """Monta <SB>seg<CR>seg<CR>...<EB><CR>."""
    corpo = "".join(linha + CR for linha in linhas)
    return (SB + corpo + EB + CR).encode(CODIFICACAO, errors="replace")


def agora() -> str:
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


def msh(mensagem_recebida: str, tipo: str, control_id: str, msh16: str) -> str:
    """MSH de resposta, repetindo o MSH.3/MSH.4 que a máquina mandou."""
    recebido = campos_do_segmento(mensagem_recebida, "MSH")
    aplicacao = campo(recebido, 2) or "URIT"
    instalacao = campo(recebido, 3) or "CA-150"
    return (
        f"MSH|^~\\&|{aplicacao}|{instalacao}|||{agora()}||{tipo}|{control_id}|P|2.3.1"
        f"||||{msh16}||ANSI|||"
    )


def gerar_ack_resultado(mensagem: str) -> bytes:
    """ACK^R01 (manual 4.1/4.2): MSH.16 e MSA.2 repetem os da mensagem."""
    recebido = campos_do_segmento(mensagem, "MSH")
    control_id = campo(recebido, 9)
    msh16 = campo(recebido, 15) or "0"
    return enquadrar([
        msh(mensagem, "ACK^R01", control_id, msh16),
        f"MSA|AA|{control_id}|Message accepted|||0|",
    ])


def etiqueta_da_consulta(mensagem: str) -> str:
    """QRD.8: código de barras da amostra."""
    return campo(campos_do_segmento(mensagem, "QRD"), 8)


def gerar_qck(mensagem: str, status: str) -> bytes:
    """QCK^Q02. status: OK (vem DSR), NF (não encontrado) ou AE (erro)."""
    control_id = campo(campos_do_segmento(mensagem, "MSH"), 9)
    return enquadrar([
        msh(mensagem, "QCK^Q02", control_id, "0"),
        f"MSA|AA|{control_id}|Message accepted|||0|",
        "ERR|0|",
        f"QAK|SR|{status}|",
    ])


def tipo_de_amostra(exames: List[Dict[str, Any]]) -> str:
    for exame in exames:
        for material in exame.get("materials") or []:
            chave = sem_acento(str(material)).strip().upper()
            return TIPOS_DE_AMOSTRA.get(chave, texto_hl7(material))
    return ""


def itens_da_consulta(exames: List[Dict[str, Any]]) -> List[str]:
    """Códigos dos atributos de todos os exames, sem repetir, na ordem recebida."""
    codigos: List[str] = []
    for exame in exames:
        for atributo in exame.get("attributes") or []:
            codigo = texto_hl7(atributo.get("code"))
            if codigo and codigo not in codigos:
                codigos.append(codigo)
    return codigos


def gerar_dsr(mensagem: str, dados: Dict[str, Any]) -> bytes:
    """
    DSR^Q03 (manual 3.10 e 4.3) com uma amostra só, terminando em DSC|-1|.
    Campos que o sistema não tem (prontuário, leito, setor, médico,
    urgência) vão vazios ou com o padrão do manual.
    """
    control_id = campo(campos_do_segmento(mensagem, "MSH"), 9)
    qrf = next((s for s in segmentos(mensagem) if s.startswith("QRF|")), "QRF|CA-150|||||RCT|COR|ALL||")
    etiqueta = texto_hl7(dados.get("tag_id")) or etiqueta_da_consulta(mensagem)
    paciente = dados.get("patient") or {}
    exames = dados.get("exams") or []
    itens = itens_da_consulta(exames)

    idade = paciente.get("age")
    sexo = str(paciente.get("gender") or "").strip().upper()

    linhas = [
        msh(mensagem, "DSR^Q03", control_id, "0"),
        f"MSA|AA|{control_id}|Message accepted|||0|",
        "ERR|0|",
        "QAK|SR|OK|",
        f"QRD|{agora()}|R|D|-1|||RD|{etiqueta}|OTH|||T|",
        qrf,
    ]
    dsp = [
        "",                                   # 1 Sample ID (a máquina numera)
        etiqueta,                             # 2 código de barras
        tipo_de_amostra(exames),              # 3 tipo de amostra
        texto_hl7(paciente.get("name")),      # 4 nome
        sexo if sexo in ("M", "F") else "",   # 5 sexo
        "" if idade is None else str(idade),  # 6 idade
        "0" if idade is not None else "",     # 7 unidade da idade (0 = anos, como no exemplo)
        "", "", "", "", "", "", "",           # 8..14 prontuário, leito, setor, médicos, clínica
        datetime.date.today().isoformat(),    # 15 data do teste
        "N",                                  # 16 urgência
        str(len(itens)),                      # 17 quantidade de itens
    ]
    dsp += [f"{i}^{codigo}^^^^" for i, codigo in enumerate(itens, start=1)]
    linhas += [f"DSP|{n}||{valor}|||" for n, valor in enumerate(dsp, start=1)]
    linhas.append("DSC|-1|")
    return enquadrar(linhas)


def valor_invalido(valor: str) -> bool:
    try:
        return f"{float(valor):.0f}" in VALORES_INVALIDOS
    except ValueError:
        return False


def parse_resultado(mensagem: str) -> Dict[str, Any]:
    """
    ORU^R01 de paciente -> {"tag_id", "results": [{"code", "value", "unit"}]}.
    Etiqueta em OBR.2, código do item em OBX.4, valor em OBX.5, unidade em OBX.6.
    """
    tag_id = campo(campos_do_segmento(mensagem, "OBR"), 2)
    resultados: List[Dict[str, str]] = []
    descartados: List[str] = []

    for seg in segmentos(mensagem):
        if not seg.startswith("OBX|"):
            continue
        campos = seg.split("|")
        codigo = campo(campos, 4) or campo(campos, 3)
        valor = campo(campos, 5)
        if not codigo or not valor:
            continue
        if valor_invalido(valor):
            descartados.append(codigo)
            continue
        resultados.append({"code": codigo, "value": valor, "unit": campo(campos, 6)})

    if descartados:
        logging.info(f"Itens sem valor válido descartados ({tag_id}): {descartados}")

    return {"tag_id": tag_id, "results": resultados}


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorCA150(BaseAnalisador):
    """URIT CA-150 por HL7/MLLP, com consulta por etiqueta no centralizador."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.MODO = str(self.CONFIG.get("modo") or "serial").strip().lower()
        self.TCP_HOST = str(self.CONFIG.get("tcp_host") or "0.0.0.0")
        self.TCP_PORT = int(self.CONFIG.get("tcp_port") or 5100)

        bruta = self.CONFIG.get("consult_url") or ""
        try:
            self.CONSULT_URL = bruta.format(franchise_credential_id=self.FRANCHISE_CREDENTIAL_ID)
        except (KeyError, IndexError, ValueError):
            self.CONSULT_URL = bruta

        self._lock_escrita = threading.Lock()
        self._lock_consulta = threading.Lock()
        self._conexao: Optional[socket.socket] = None
        self._servidor_tcp: Optional[socket.socket] = None

    # ── HOOKS DO BASEANALISADOR ───────────────────────────────

    def get_file_extension(self) -> str:
        return ".hl7"

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """Mensagem completa: <SB> ... <EB><CR>. Lixo antes do <SB> é descartado."""
        inicio = buffer.find(SB)
        if inicio < 0:
            return None, ""
        buffer = buffer[inicio:]
        fim = buffer.find(EB + CR)
        if fim < 0:
            return None, buffer
        return buffer[:fim + 2], buffer[fim + 2:]

    def generate_ack(self, raw_message: str) -> Optional[bytes]:
        return gerar_ack_resultado(raw_message)

    def _on_serial_message(self, raw_message: str) -> None:
        tipo = tipo_mensagem(raw_message)

        if tipo.startswith("QRY"):
            Thread(target=self._responder_consulta, args=(raw_message,), daemon=True).start()
            return

        if tipo.startswith("ORU"):
            msh16 = campo(campos_do_segmento(raw_message, "MSH"), 15) or "0"
            if msh16 != "0":
                logging.info(f"Resultado de controle de qualidade (MSH.16={msh16}): só ACK, não enviado.")
                self._send_ack(gerar_ack_resultado(raw_message))
                return
            # Grava em gerados/ e responde o ACK (generate_ack).
            super()._on_serial_message(raw_message)
            return

        logging.debug(f"Mensagem {tipo or 'sem MSH'} recebida e ignorada.")

    def _send_ack(self, ack_data: bytes) -> None:
        self._escrever(ack_data)

    def process_file(self, filepath: str, nome_arquivo: str) -> Optional[List[Dict[str, Any]]]:
        try:
            with open(filepath, "r", encoding="utf-8", newline="") as f:
                conteudo = f.read()
        except OSError as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        payload = parse_resultado(conteudo)

        try:
            pasta_json = os.path.join(self.ENVIADOS_DIR, "json")
            os.makedirs(pasta_json, exist_ok=True)
            with open(os.path.join(pasta_json, os.path.splitext(nome_arquivo)[0] + ".json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logging.warning(f"Erro ao salvar JSON de {nome_arquivo}: {e}")

        if not payload["tag_id"]:
            logging.error(f"✗ {nome_arquivo}: etiqueta (OBR.2) vazia. Nada enviado.")
            return []
        if not payload["results"]:
            logging.warning(f"{nome_arquivo}: nenhum item com valor (etiqueta {payload['tag_id']}).")
            return []

        logging.info(f"{nome_arquivo}: {len(payload['results'])} item(ns) da etiqueta {payload['tag_id']}.")
        return [payload]

    # ── CONSULTA POR ETIQUETA ─────────────────────────────────

    def consultar_etiqueta(self, etiqueta: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Pergunta ao lab-apoio. Devolve ("OK", data), ("NF", None) quando a
        etiqueta não existe ou não tem item por interface, ou ("AE", None) em erro.
        """
        if not etiqueta:
            return "NF", None
        try:
            resp = requests.get(self.CONSULT_URL, params={"tag_id": etiqueta}, timeout=TIMEOUT_CONSULTA)
        except requests.RequestException as e:
            logging.error(f"✗ Consulta da etiqueta {etiqueta} falhou: {type(e).__name__}: {e}")
            return "AE", None

        if resp.status_code == 404:
            logging.warning(f"Etiqueta {etiqueta} não encontrada no sistema.")
            return "NF", None
        if resp.status_code != 200:
            logging.error(f"✗ Consulta da etiqueta {etiqueta}: status {resp.status_code}: {resp.text[:300]}")
            return "AE", None

        try:
            dados = resp.json().get("data") or {}
        except ValueError:
            logging.error(f"✗ Consulta da etiqueta {etiqueta}: resposta sem JSON.")
            return "AE", None

        if not itens_da_consulta(dados.get("exams") or []):
            logging.warning(f"Etiqueta {etiqueta} sem exames pendentes por interfaceamento.")
            return "NF", None
        return "OK", dados

    def _responder_consulta(self, mensagem: str) -> None:
        etiqueta = etiqueta_da_consulta(mensagem)
        with self._lock_consulta:
            logging.info(f"Consulta da máquina para a etiqueta {etiqueta or '(vazia)'}.")
            status, dados = self.consultar_etiqueta(etiqueta)
            self._escrever(gerar_qck(mensagem, status))
            if status != "OK" or dados is None:
                logging.info(f"Etiqueta {etiqueta}: respondido QAK|SR|{status}.")
                return
            time.sleep(INTERVALO_DSR)
            self._escrever(gerar_dsr(mensagem, dados))
            logging.info(
                f"Etiqueta {etiqueta}: enviados {len(itens_da_consulta(dados.get('exams') or []))} item(ns) à máquina."
            )

    # ── TRANSPORTE ────────────────────────────────────────────

    def _escrever(self, dados: bytes) -> None:
        """Escreve na conexão ativa (serial ou TCP), uma mensagem por vez."""
        with self._lock_escrita:
            try:
                if self.MODO == "tcp":
                    if self._conexao is None:
                        logging.error("✗ Sem máquina conectada por TCP: resposta descartada.")
                        return
                    self._conexao.sendall(dados)
                else:
                    ser = getattr(self, "_serial", None)
                    if ser is None or not ser.is_open:
                        logging.error("✗ Porta serial fechada: resposta descartada.")
                        return
                    ser.write(dados)
            except Exception as e:
                logging.error(f"✗ Erro ao escrever para a máquina: {type(e).__name__}: {e}")

    def _serial_is_open(self) -> bool:
        if self.MODO == "tcp":
            return self._servidor_tcp is not None
        return super()._serial_is_open()

    def start(self) -> None:
        if self.MODO != "tcp":
            super().start()
            return

        self._banner()
        logging.info(f"Modo TCP: escutando em {self.TCP_HOST}:{self.TCP_PORT}")
        logging.info(f"Consulta: {self.CONSULT_URL}")
        Thread(target=self._task_sender_to_webhook, daemon=True).start()
        self._start_health_server()
        self._servir_tcp()

    def _abrir_servidor_tcp(self) -> socket.socket:
        while True:
            try:
                servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                servidor.bind((self.TCP_HOST, self.TCP_PORT))
                servidor.listen(1)
                return servidor
            except OSError as e:
                logging.error(
                    f"✗ Não foi possível escutar em {self.TCP_HOST}:{self.TCP_PORT}: {e}. "
                    f"Tentando de novo em {TCP_RETRY_INTERVAL}s..."
                )
                time.sleep(TCP_RETRY_INTERVAL)

    def _servir_tcp(self) -> None:
        """
        Aceita a máquina. Uma conexão nova substitui a anterior: quando o cabo
        cai sem aviso a conexão velha fica pendurada e a máquina reconecta.
        """
        self._servidor_tcp = self._abrir_servidor_tcp()
        self.TCP_PORT = self._servidor_tcp.getsockname()[1]
        logging.info(f"=== {self.machine_name} Service (Python) rodando em TCP {self.TCP_HOST}:{self.TCP_PORT} ===")

        while True:
            try:
                conexao, endereco = self._servidor_tcp.accept()
            except OSError as e:
                logging.error(f"✗ Erro ao aceitar conexão TCP: {e}")
                time.sleep(1)
                continue

            conexao.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            anterior = self._conexao
            with self._lock_escrita:
                self._conexao = conexao
            if anterior is not None:
                logging.info("Conexão TCP anterior substituída pela nova.")
                try:
                    anterior.close()
                except OSError:
                    pass
            logging.info(f"✓ Máquina conectada por TCP: {endereco[0]}:{endereco[1]}")
            Thread(target=self._ler_tcp, args=(conexao,), daemon=True).start()

    def _ler_tcp(self, conexao: socket.socket) -> None:
        buffer = ""
        try:
            while True:
                dados = conexao.recv(4096)
                if not dados:
                    break
                buffer += dados.decode(CODIFICACAO, errors="replace")
                mensagem, buffer = self.detect_complete_message(buffer)
                while mensagem is not None:
                    self._on_serial_message(mensagem)
                    mensagem, buffer = self.detect_complete_message(buffer)
        except OSError as e:
            logging.warning(f"Conexão TCP encerrada: {e}")
        finally:
            with self._lock_escrita:
                if self._conexao is conexao:
                    self._conexao = None
            try:
                conexao.close()
            except OSError:
                pass
            logging.info("Máquina desconectou do TCP; aguardando nova conexão.")


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorCA150(
        machine_id="ca150",
        machine_name="CA150",
        config_defaults={
            "modo": "serial",
            "com_port": "COM6",
            "baud_rate": 9600,
            "tcp_host": "0.0.0.0",
            "tcp_port": 5100,
            "franchise_credential_id": "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX",
            "webhook_url": f"{URL_BASE}/integration/ca-150?franchise_credential_id={{franchise_credential_id}}",
            "consult_url": f"{URL_BASE}/interfacing/tag?franchise_credential_id={{franchise_credential_id}}",
        },
        health_port=8085,
    ).start()
