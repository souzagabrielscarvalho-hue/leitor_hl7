"""
Analisador URIT-8021A (bioquímica): HL7 v2.3.1 por TCP/IP ou serial.

Segue o "Urit HL7 LIS Interface Dev Reference Manual" (LISHL7Interface.dll,
usado pelas URIT 8030/8060/8021A/8031). O software da URIT, no PC que controla
o analisador, é o cliente; este programa é o servidor do sistema de laboratório:

- Consulta por etiqueta: o software manda QRY^Q02 com a etiqueta em QRD.8. O
  programa pergunta ao lab-apoio (GET /api/interfacing/tag) e responde QCK^Q02
  e, ~1 s depois, DSR^Q03 com o paciente e os itens (DSC|-1|: última amostra).
  O software confirma com ACK^Q03. Etiqueta inexistente, sem itens por
  interface ou consulta por período (sem etiqueta): só QCK^Q02 com QAK|SR|NF.
- Resultado: o software manda ORU^R01. O programa responde ACK^R01 na hora
  (MSA.2 = Sample ID, como o ACKmsgHL7 do manual) e guarda a mensagem em
  gerados/; a thread de envio manda para POST /api/integration/urit-8021.
- Valores: o HL7 usa ponto decimal ("11.8"); no sistema o ponto nunca é
  decimal, então o valor vai com vírgula ("11,8").
- Imagens: o manual não prevê imagem (os OBX são NM). Se chegar um OBX com
  base64 (tipo ED), ele fica fora dos resultados e o log registra.

Conexão escolhida no config_urit8021.json:
- "modo": "tcp" escuta em tcp_host:tcp_port e o software da URIT conecta no IP
  deste PC (127.0.0.1 se rodar no mesmo PC);
- "modo": "serial" usa com_port e baud_rate, com o mesmo enquadramento MLLP.
"""

import os
import re
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
from shared import base_analisador
from shared.base_analisador import BaseAnalisador

# ═══════════════════════════════════════════════════════════
# PROTOCOLO
# ═══════════════════════════════════════════════════════════

SB = chr(0x0B)
EB = chr(0x1C)
CR = chr(0x0D)

# O manual declara o conjunto de caracteres ASCII (MSH.18). A leitura aceita
# cp1252 por segurança; o que o programa escreve vai sem acento.
CODIFICACAO = "cp1252"

# Itens calculados sem valor válido chegam com este número na família URIT
# (ex.: A/G sem albumina).
VALORES_INVALIDOS = {"10000000"}

# A DLL da URIT escreve "null" em campo vazio (OBR.2 e PID.5 no exemplo).
VAZIOS = {"", "NULL"}

# Material do sistema -> tipo de amostra da máquina (DSP|3).
TIPOS_DE_AMOSTRA = {
    "SORO": "Serum",
    "PLASMA": "Plasma",
    "URINA": "Urine",
}

# Número com ponto decimal, com sinal ou comparador opcionais: "11.8", "-0.2",
# "<0.05", ">=1.5".
NUMERO_COM_PONTO = re.compile(r"^([<>]=?)?\s*-?\d+\.\d+$")

# Tipos de OBX que carregam imagem/arquivo em base64.
TIPOS_IMAGEM = {"ED", "RP"}

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
    """Campo sem espaços; "null" da DLL da URIT vira vazio."""
    valor = campos[indice].strip() if len(campos) > indice else ""
    return "" if valor.upper() in VAZIOS else valor


def tipo_mensagem(mensagem: str) -> str:
    """Tipo do MSH.9, ex.: "ORU^R01"."""
    return campo(campos_do_segmento(mensagem, "MSH"), 8).upper()


def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def texto_hl7(valor: Any) -> str:
    """Texto seguro para um campo: ASCII, sem separadores HL7 nem quebras de linha."""
    texto = sem_acento("" if valor is None else str(valor))
    for caractere in "|^~\\&\r\n":
        texto = texto.replace(caractere, " ")
    return texto.encode("ascii", errors="ignore").decode("ascii").strip()


def enquadrar(linhas: List[str]) -> bytes:
    """Monta <SB>seg<CR>seg<CR>...<EB><CR>."""
    corpo = "".join(linha + CR for linha in linhas)
    return (SB + corpo + EB + CR).encode(CODIFICACAO, errors="replace")


def agora() -> str:
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


def msh(mensagem_recebida: str, tipo: str, control_id: str) -> str:
    """MSH de resposta (manual 7.2), repetindo o MSH.3/MSH.4 que o software mandou."""
    recebido = campos_do_segmento(mensagem_recebida, "MSH")
    aplicacao = campo(recebido, 2) or "urit"
    instalacao = campo(recebido, 3) or "8021A"
    return (
        f"MSH|^~\\&|{aplicacao}|{instalacao}|||{agora()}||{tipo}|{control_id}|P|2.3.1"
        f"||||0||ASCII|||"
    )


def gerar_ack_resultado(mensagem: str) -> bytes:
    """
    ACK^R01 (manual 7.2 e 8.1): ACKmsgHL7("R01", sam->ID, ...). O MSA.2 leva o
    Sample ID (OBR.3), que o software usa para marcar a amostra como enviada.
    Sem Sample ID, repete o MSH.10.
    """
    control_id = campo(campos_do_segmento(mensagem, "MSH"), 9)
    sample_id = campo(campos_do_segmento(mensagem, "OBR"), 3) or control_id
    return enquadrar([
        msh(mensagem, "ACK^R01", control_id),
        f"MSA|AA|{sample_id}|Message accepted|||0|",
    ])


def etiqueta_da_consulta(mensagem: str) -> str:
    """QRD.8: código de barras da amostra (vazio na consulta por período)."""
    return campo(campos_do_segmento(mensagem, "QRD"), 8)


def gerar_qck(mensagem: str, status: str) -> bytes:
    """QCK^Q02 (manual 7.4). status: OK (vem DSR), NF (não encontrado) ou AE (erro)."""
    control_id = campo(campos_do_segmento(mensagem, "MSH"), 9)
    return enquadrar([
        msh(mensagem, "QCK^Q02", control_id),
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


def traduzir_codigo(codigo: str, mapa: Optional[Dict[str, str]]) -> str:
    """Troca o código pelo equivalente do mapa; sem mapa ou sem entrada, devolve o próprio."""
    return mapa.get(codigo, codigo) if mapa else codigo


def gerar_dsr(mensagem: str, dados: Dict[str, Any], depara_vida_maq: Optional[Dict[str, str]] = None) -> bytes:
    """
    DSR^Q03 (manual 7.5) com uma amostra só, terminando em DSC|-1|.
    DSP 1..17 seguem a ordem do manual; os itens vão como
    ItemIndex^ItemName^Abs^Result^Unit^Range, só com índice e nome.
    Campos que o sistema não tem (prontuário, leito, setor, médico) vão vazios.

    depara_vida_maq: mapa código do Vida -> código da máquina. A máquina só
    reconhece o exame pelo código DELA, então o código do atributo vindo do
    lab-apoio é traduzido antes de ir no DSR (config: depara_codigos).
    """
    control_id = campo(campos_do_segmento(mensagem, "MSH"), 9)
    qrd = campos_do_segmento(mensagem, "QRD")
    query_id = campo(qrd, 4) or "1"
    qrf = next((s for s in segmentos(mensagem) if s.startswith("QRF|")), "QRF|8021A|||||RCT|COR|ALL||")
    etiqueta = texto_hl7(dados.get("tag_id")) or etiqueta_da_consulta(mensagem)
    paciente = dados.get("patient") or {}
    exames = dados.get("exams") or []
    itens = [traduzir_codigo(c, depara_vida_maq) for c in itens_da_consulta(exames)]

    idade = paciente.get("age")
    sexo = str(paciente.get("gender") or "").strip().upper()

    linhas = [
        msh(mensagem, "DSR^Q03", control_id),
        f"MSA|AA|{control_id}|Message accepted|||0|",
        "ERR|0|",
        "QAK|SR|OK|",
        f"QRD|{agora()}|R|D|{query_id}|||RD|{etiqueta}|OTH|||T|",
        qrf,
    ]
    dsp = [
        "",                                   # 1 Sample ID (o software numera)
        etiqueta,                             # 2 código de barras
        tipo_de_amostra(exames),              # 3 tipo de amostra
        texto_hl7(paciente.get("name")),      # 4 nome
        sexo if sexo in ("M", "F") else "",   # 5 sexo
        "" if idade is None else str(idade),  # 6 idade
        "0" if idade is not None else "",     # 7 unidade da idade (0 = anos, como no exemplo)
        "", "", "", "", "", "", "",           # 8..14 prontuários, leito, setor, médico, operador, diagnóstico
        datetime.date.today().isoformat(),    # 15 data do teste (AAAA-MM-DD, como no exemplo)
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


def valor_com_virgula(valor: str) -> str:
    """"11.8" -> "11,8". Texto que não é número com ponto fica como está."""
    if NUMERO_COM_PONTO.match(valor):
        return valor.replace(".", ",")
    return valor


def codigo_do_obx(campos: List[str]) -> str:
    """OBX.4 é o nome do item (ALB, GLU...); OBX.3 é só o índice na máquina."""
    return campo(campos, 4) or campo(campos, 3)


def parse_resultado(mensagem: str) -> Dict[str, Any]:
    """
    ORU^R01 -> {"tag_id", "results": [{"code", "value", "unit"}]}.
    Etiqueta em OBR.2, código do item em OBX.4, valor em OBX.5, unidade em OBX.6.
    """
    tag_id = campo(campos_do_segmento(mensagem, "OBR"), 2)
    resultados: List[Dict[str, str]] = []
    descartados: List[str] = []
    imagens: List[str] = []

    for seg in segmentos(mensagem):
        if not seg.startswith("OBX|"):
            continue
        campos = seg.split("|")
        codigo = codigo_do_obx(campos)
        if campo(campos, 2).upper() in TIPOS_IMAGEM:
            imagens.append(codigo or "?")
            continue
        valor = campo(campos, 5)
        if not codigo or not valor:
            continue
        if valor_invalido(valor):
            descartados.append(codigo)
            continue
        resultados.append({"code": codigo, "value": valor_com_virgula(valor), "unit": campo(campos, 6)})

    if descartados:
        logging.info(f"Itens sem valor válido descartados ({tag_id or 'sem etiqueta'}): {descartados}")
    if imagens:
        logging.warning(
            f"✗ OBX com imagem/base64 ignorado(s) ({tag_id or 'sem etiqueta'}): {imagens}. "
            f"O manual da 8021A não prevê imagem e este leitor não envia imagens."
        )

    return {"tag_id": tag_id, "results": resultados}


def resumo_dos_itens(mensagem: str) -> List[str]:
    """Itens do ORU para o log, ex.: ["ALB=11.8 g/l", "GLU=5.4"]."""
    itens = []
    for seg in segmentos(mensagem):
        if seg.startswith("OBX|"):
            campos = seg.split("|")
            unidade = campo(campos, 6)
            valor = campo(campos, 5)
            if campo(campos, 2).upper() in TIPOS_IMAGEM:
                valor = f"(imagem, {len(valor)} caracteres)"
            itens.append(f"{codigo_do_obx(campos)}={valor}" + (f" {unidade}" if unidade else ""))
    return itens


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorURIT8021(BaseAnalisador):
    """URIT-8021A por HL7/MLLP, com consulta por etiqueta no centralizador."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.MODO = str(self.CONFIG.get("modo") or "tcp").strip().lower()
        self.TCP_HOST = str(self.CONFIG.get("tcp_host") or "0.0.0.0")
        porta = self.CONFIG.get("tcp_port")
        self.TCP_PORT = int(porta) if porta not in (None, "") else 8021

        bruta = self.CONFIG.get("consult_url") or ""
        try:
            self.CONSULT_URL = bruta.format(franchise_credential_id=self.FRANCHISE_CREDENTIAL_ID)
        except (KeyError, IndexError, ValueError):
            self.CONSULT_URL = bruta

        self._lock_escrita = threading.Lock()
        self._lock_consulta = threading.Lock()
        self._conexao: Optional[socket.socket] = None
        self._servidor_tcp: Optional[socket.socket] = None
        self._popup_aberto = threading.Event()
        # Antes da primeira conexão não é queda: o software pode estar fechado.
        self._ja_conectou = False
        self._endereco_maquina = ""

        if not self.CONSULT_URL:
            logging.error("✗ consult_url vazio no config: as consultas da máquina vão responder erro (AE).")

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
        try:
            self._tratar_mensagem(raw_message)
        except Exception:
            # No .exe não há console: sem isto o erro sumiria sem deixar rastro.
            logging.exception(f"✗ Erro inesperado ao tratar mensagem da máquina: {raw_message[:200]!r}")

    def _tratar_mensagem(self, raw_message: str) -> None:
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
            obr = campos_do_segmento(raw_message, "OBR")
            etiqueta = campo(obr, 2)
            logging.info(
                f"Resultado recebido da máquina: etiqueta {etiqueta or '(vazia)'}, "
                f"amostra {campo(obr, 3) or '(sem ID)'}, "
                f"itens: {', '.join(resumo_dos_itens(raw_message)) or '(nenhum)'}."
            )
            # Grava em gerados/ e responde o ACK (generate_ack).
            super()._on_serial_message(raw_message)
            return

        if tipo.startswith("ACK"):
            msa = campos_do_segmento(raw_message, "MSA")
            codigo, control_id = campo(msa, 1).upper(), campo(msa, 2)
            if codigo in ("AA", "CA"):
                logging.info(f"✓ Máquina confirmou a mensagem {control_id} ({tipo}, {codigo}).")
            else:
                logging.error(
                    f"✗ Máquina recusou a mensagem {control_id} ({tipo}, {codigo or 'sem MSA'}): "
                    f"{campo(msa, 3) or 'sem motivo informado'}."
                )
            return

        logging.warning(f"Mensagem {tipo or 'sem MSH'} recebida e ignorada: {raw_message[:200]!r}")

    def _send_ack(self, ack_data: bytes) -> None:
        if self._escrever(ack_data):
            logging.info("✓ ACK^R01 enviado à máquina.")
        else:
            logging.error("✗ ACK^R01 não chegou à máquina: ela deve reenviar o resultado.")

    def _on_webhook_response(self, resp: Any, nome_arquivo: str, tag_identifier: str) -> None:
        """Registra o que o lab-apoio gravou e o que ficou de fora."""
        try:
            corpo = resp.json()
        except ValueError:
            corpo = None
        mensagem = str(corpo.get("message") or "") if isinstance(corpo, dict) else resp.text[:300]
        dados = corpo.get("data") if isinstance(corpo, dict) else None

        if resp.status_code not in (200, 201):
            logging.error(
                f"✗ lab-apoio respondeu {resp.status_code} para a etiqueta {tag_identifier}: "
                f"{mensagem or resp.text[:300]}"
            )
            return
        if not isinstance(dados, dict):
            return

        for item in dados.get("saved") or []:
            if isinstance(item, dict):
                logging.info(
                    f"✓ Gravado no laudo: etiqueta {tag_identifier}, exame {item.get('exam_code')}, "
                    f"{item.get('code')}={item.get('value')}."
                )
        ignorados = dados.get("ignored") or []
        if ignorados:
            logging.warning(
                f"Itens da etiqueta {tag_identifier} NÃO gravados: {', '.join(map(str, ignorados))}. "
                f"Motivo provável: sem atributo com origem Interface na unidade, código diferente do "
                f"atributo, ou procedimento já liberado."
            )

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
            sample_id = campo(campos_do_segmento(conteudo, "OBR"), 3)
            logging.error(
                f"✗ {nome_arquivo}: etiqueta (OBR.2) vazia na amostra {sample_id or '(sem ID)'}. "
                f"Nada enviado: cadastre a amostra com o código de barras no software da URIT."
            )
            return []
        if not payload["results"]:
            logging.warning(f"✗ {nome_arquivo}: nenhum item com valor (etiqueta {payload['tag_id']}). Nada enviado.")
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
            logging.warning(f"✗ Etiqueta {etiqueta} não encontrada no sistema.")
            return "NF", None
        if resp.status_code != 200:
            logging.error(f"✗ Consulta da etiqueta {etiqueta}: status {resp.status_code}: {resp.text[:300]}")
            return "AE", None

        try:
            dados = resp.json().get("data") or {}
        except ValueError:
            logging.error(f"✗ Consulta da etiqueta {etiqueta}: resposta sem JSON.")
            return "AE", None

        itens = itens_da_consulta(dados.get("exams") or [])
        if not itens:
            logging.warning(
                f"✗ Etiqueta {etiqueta} encontrada, mas sem exames pendentes por interfaceamento "
                f"(confira a origem Interface dos atributos na unidade)."
            )
            return "NF", None
        exames = [str(e.get("exam_code") or "?") for e in dados.get("exams") or []]
        logging.info(
            f"✓ Etiqueta {etiqueta} encontrada: exames {', '.join(exames)}; "
            f"itens por interface: {', '.join(itens)}."
        )
        return "OK", dados

    def _responder_consulta(self, mensagem: str) -> None:
        etiqueta = etiqueta_da_consulta(mensagem)
        try:
            with self._lock_consulta:
                if not etiqueta:
                    qrf = campos_do_segmento(mensagem, "QRF")
                    logging.warning(
                        f"✗ Consulta sem etiqueta (por período {campo(qrf, 2) or '?'} a {campo(qrf, 3) or '?'}): "
                        f"não suportada, respondido QAK|SR|NF. Use a consulta por código de barras."
                    )
                    if not self._escrever(gerar_qck(mensagem, "NF")):
                        logging.error("✗ Consulta sem etiqueta: QAK|SR|NF não chegou à máquina.")
                    return

                logging.info(f"Consulta da máquina para a etiqueta {etiqueta}.")
                status, dados = self.consultar_etiqueta(etiqueta)
                if not self._escrever(gerar_qck(mensagem, status)):
                    logging.error(f"✗ Etiqueta {etiqueta}: QAK|SR|{status} não chegou à máquina.")
                    return
                if status != "OK" or dados is None:
                    logging.info(f"Etiqueta {etiqueta}: respondido QAK|SR|{status} (máquina não recebe exames).")
                    return
                time.sleep(INTERVALO_DSR)
                itens = itens_da_consulta(dados.get("exams") or [])
                itens_maquina = [traduzir_codigo(c, self.DEPARA_VIDA_MAQ) for c in itens]
                if self._escrever(gerar_dsr(mensagem, dados, self.DEPARA_VIDA_MAQ)):
                    enviados = ", ".join(
                        f"{maq} (={vida})" if maq != vida else maq
                        for vida, maq in zip(itens, itens_maquina)
                    )
                    logging.info(
                        f"✓ Etiqueta {etiqueta}: enviados {len(itens_maquina)} item(ns) à máquina: {enviados}. "
                        f"Aguardando a confirmação (ACK^Q03)."
                    )
                else:
                    logging.error(f"✗ Etiqueta {etiqueta}: lista de exames (DSR) não chegou à máquina.")
        except Exception:
            logging.exception(f"✗ Erro inesperado ao responder a consulta da etiqueta {etiqueta}.")

    # ── TRANSPORTE ────────────────────────────────────────────

    def _escrever(self, dados: bytes) -> bool:
        """Escreve na conexão ativa (serial ou TCP), uma mensagem por vez. True se escreveu."""
        with self._lock_escrita:
            try:
                if self.MODO == "tcp":
                    if self._conexao is None:
                        logging.error("✗ Sem máquina conectada por TCP: resposta descartada.")
                        return False
                    self._conexao.sendall(dados)
                else:
                    ser = getattr(self, "_serial", None)
                    if ser is None or not ser.is_open:
                        logging.error("✗ Porta serial fechada: resposta descartada.")
                        return False
                    ser.write(dados)
                return True
            except Exception as e:
                logging.error(f"✗ Erro ao escrever para a máquina: {type(e).__name__}: {e}")
                return False

    def _serial_is_open(self) -> bool:
        """
        Em TCP, "conectado" é ter o software da URIT ligado ao programa.
        Enquanto ele nunca conectou, não conta como queda (watchdog não encerra).
        """
        if self.MODO == "tcp":
            return not self._ja_conectou or self._conexao is not None
        return super()._serial_is_open()

    def _nome_conexao(self, maiuscula: bool = False) -> str:
        if self.MODO != "tcp":
            return super()._nome_conexao(maiuscula)
        return f"{'M' if maiuscula else 'a m'}áquina na porta TCP {self.TCP_PORT}"

    def _mensagem_queda(self, pendentes: int) -> str:
        if self.MODO != "tcp":
            return super()._mensagem_queda(pendentes)
        erro = self._erro_queda or "não informado"
        return "\n".join([
            f"A conexão com o analisador {self.machine_name} foi perdida e não voltou "
            f"em {base_analisador.CONNECTION_DEADLINE // 60} min.",
            "",
            f"Conexão: TCP, porta {self.TCP_PORT} (máquina: {self._endereco_maquina or 'não informada'})",
            f"Motivo: {erro}",
            "",
            "Exames da pasta gerados:",
            f"- enviados durante a queda: {self._enviados_na_queda}",
            f'- não enviados (pasta "requisições não enviadas"): {self._nao_enviados_na_queda}',
            f"- ainda pendentes (serão enviados ao reabrir): {pendentes}",
            "",
            "O programa será encerrado. Abra o urit8021.exe de novo e, no software da URIT,",
            "conecte o LIS outra vez.",
        ])

    def _die_no_connection(self, pendentes: int) -> None:
        if self.MODO == "tcp":
            # Solta a porta antes do popup, para o programa poder ser reaberto.
            for sock in (self._conexao, self._servidor_tcp):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        super()._die_no_connection(pendentes)

    def start(self) -> None:
        if self.MODO != "tcp":
            super().start()
            return

        self._banner()
        logging.info(f"Modo TCP: escutando em {self.TCP_HOST}:{self.TCP_PORT}")
        logging.info(f"Consulta: {self.CONSULT_URL}")
        Thread(target=self._task_sender_to_webhook, daemon=True).start()
        self._start_health_server()
        # Mesmo watchdog da serial: queda sem reconexão em CONNECTION_DEADLINE
        # espera gerados/ ser enviada, mostra o popup e encerra.
        self._start_connection_watchdog()
        self._servir_tcp()

    def _popup_sem_travar(self, titulo: str, mensagem: str) -> None:
        """
        Popup que não encerra nem trava o programa (abre numa thread própria).
        Com um aviso já aberto, não empilha outro (fica só no log).
        """
        if self._popup_aberto.is_set():
            logging.info("Aviso já aberto na tela; novo aviso só no log.")
            return
        self._popup_aberto.set()

        def _mostrar() -> None:
            try:
                self._show_popup(titulo, mensagem)
            finally:
                self._popup_aberto.clear()

        Thread(target=_mostrar, daemon=True, name="popup-aviso").start()

    def _abrir_servidor_tcp(self) -> socket.socket:
        avisado = False
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
                if not avisado:
                    avisado = True
                    self._popup_sem_travar(
                        f"Porta ocupada - {self.machine_name}",
                        f"O programa da {self.machine_name} não conseguiu abrir a porta {self.TCP_PORT}.\n\n"
                        f"Erro: {e}\n\n"
                        f"Provável causa: outro urit8021.exe (ou outro programa) já está usando a porta.\n"
                        f"Feche o programa duplicado. Esta janela tenta de novo a cada {TCP_RETRY_INTERVAL}s."
                    )
                time.sleep(TCP_RETRY_INTERVAL)

    def _servir_tcp(self) -> None:
        """
        Aceita o software da URIT. Uma conexão nova substitui a anterior: quando
        a rede cai sem aviso a conexão velha fica pendurada e o software reconecta.
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
            self._ja_conectou = True
            self._endereco_maquina = f"{endereco[0]}:{endereco[1]}"
            logging.info(f"✓ Máquina conectada por TCP: {self._endereco_maquina}")
            Thread(target=self._ler_tcp, args=(conexao, self._endereco_maquina), daemon=True).start()

    def _ler_tcp(self, conexao: socket.socket, endereco: str = "") -> None:
        buffer = ""
        motivo = "a máquina fechou a conexão"
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
            motivo = f"{type(e).__name__}: {e}"
            logging.warning(f"Conexão TCP encerrada: {e}")
        except Exception as e:
            motivo = f"erro inesperado: {type(e).__name__}: {e}"
            logging.exception("✗ Erro inesperado na leitura TCP.")
        finally:
            with self._lock_escrita:
                # Conexão substituída por uma nova não é queda.
                caiu = self._conexao is conexao
                if caiu:
                    self._conexao = None
            try:
                conexao.close()
            except OSError:
                pass
            if caiu:
                # O popup e o encerramento ficam com o watchdog, depois que
                # gerados/ for enviada (regra igual à da serial).
                self._erro_queda = motivo
                logging.warning(
                    f"⚠ Conexão com a máquina perdida ({endereco or 'sem endereço'}): {motivo}. "
                    f"Aguardando nova conexão."
                )
            else:
                logging.info("Conexão TCP antiga encerrada (substituída pela nova).")


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorURIT8021(
        machine_id="urit8021",
        machine_name="URIT8021",
        config_defaults={
            # O manual HL7 da URIT usa MLLP sobre TCP/IP; "serial" fica disponível.
            "modo": "tcp",
            "com_port": "COM7",
            "baud_rate": 9600,
            "tcp_host": "0.0.0.0",
            "tcp_port": 8021,
            "franchise_credential_id": "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX",
            # Endpoint próprio da máquina no lab-apoio (etiqueta + code/value/unit,
            # casando com o attribute_code).
            "webhook_url": f"{URL_BASE}/integration/urit-8021?franchise_credential_id={{franchise_credential_id}}",
            "consult_url": f"{URL_BASE}/interfacing/tag?franchise_credential_id={{franchise_credential_id}}",
        },
        health_port=8086,
    ).start()
