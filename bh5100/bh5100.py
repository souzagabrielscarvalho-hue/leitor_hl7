"""
Analisador BH5100 — Integração HL7 por porta serial ou TCP/IP.

Herda de BaseAnalisador e implementa apenas os hooks específicos
do protocolo HL7/MLLP do equipamento BH5100 (Mindray).

Conexão escolhida no config_bh5100.json (default: serial, para não quebrar as
unidades atuais):
- "modo": "serial" usa com_port e baud_rate, como sempre;
- "modo": "tcp" escuta em tcp_host:tcp_port e a máquina conecta no IP do PC
  (mesmo padrão da CA-150 e da URIT-8021).
"""

import os
import sys
import time
import socket
import logging
import datetime
import threading
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import base_analisador
from shared.base_analisador import BaseAnalisador, format_thousands, extrair_imagens_de_hl7_com_metadados
from shared.exam_images_uploader import ExamImageUploader

# ═══════════════════════════════════════════════════════════
# CONSTANTES DE PROTOCOLO HL7/MLLP
# ═══════════════════════════════════════════════════════════

SB = chr(0x0B)   # Start Block (VT)
EB = chr(0x1C)   # End Block (FS)
CR = chr(0x0D)   # Carriage Return

# A BH5100 usa utf-8 em todo o resto (process_file lê utf-8, generate_ack
# codifica em utf-8); o transporte TCP segue a mesma codificação.
CODIFICACAO = "utf-8"

# Intervalo entre tentativas de reabrir a porta TCP quando ela está ocupada.
TCP_RETRY_INTERVAL = 30


# ═══════════════════════════════════════════════════════════
# MAPEAMENTO DE CAMPOS (BH5100 → webhook)
# ═══════════════════════════════════════════════════════════

MAPEAMENTO = {
    'WBC':  'WBC',
    'NEU#': 'NE',
    'NEU':  'NE_Percent',
    'NEU%': 'NE_Percent',
    'LYM#': 'LY',
    'LYM':  'LY_Percent',
    'LYM%': 'LY_Percent',
    'MON#': 'MO',
    'MON':  'MO_Percent',
    'MON%': 'MO_Percent',
    'EOS#': 'EO',
    'EOS':  'EO_Percent',
    'EOS%': 'EO_Percent',
    'BASO#':'BA',
    'BASO': 'BA_Percent',
    'BASO%':'BA_Percent',
    # Populações adicionais do BH5100 (o back já mapeia NRBC/ALY/LIC).
    'NRBC#': 'NRBC',
    'NRBC':  'NRBC_Percent',
    'NRBC%': 'NRBC_Percent',
    'ALY#':  'ALY',
    'ALY':   'ALY_Percent',
    'ALY%':  'ALY_Percent',
    'LIC#':  'LIC',
    'LIC':   'LIC_Percent',
    'LIC%':  'LIC_Percent',
    'RBC':  'RBC',
    'HGB':  'HGB',
    'HCT':  'HCT',
    'MCV':  'MCV',
    'MCH':  'MCH',
    'MCHC': 'MCHC',
    'RDW_CV':'RDW',
    'RDW_SD':'RDW_SD',
    'PLT':  'PLT',
    'PCT':  'PCT',
    'MPV':  'MPV',
    'PDW':  'PDW',
    'P_LCR':'P_LCR',
    'P-LCR':'P_LCR',
    'P_LCC':'P_LCC',
}

CAMPOS_ABSOLUTOS = {'WBC', 'NE', 'LY', 'MO', 'EO', 'BA', 'NRBC', 'ALY', 'LIC', 'PLT'}
CAMPOS_MULTIPLICAR = ('WBC', 'LY', 'MO', 'NE', 'EO', 'BA', 'NRBC', 'ALY', 'LIC', 'PLT')
PERCENT_PAIRS = [
    ('LY_Percent', 'LY'),
    ('MO_Percent', 'MO'),
    ('EO_Percent', 'EO'),
    ('BA_Percent', 'BA'),
    ('NRBC_Percent', 'NRBC'),
    ('ALY_Percent', 'ALY'),
    ('LIC_Percent', 'LIC'),
]

ORDEM_TXT = [
    'WBC', 'NE', 'NE_Percent', 'LY', 'LY_Percent',
    'MO', 'MO_Percent', 'EO', 'EO_Percent', 'BA', 'BA_Percent',
    'NRBC', 'NRBC_Percent', 'ALY', 'ALY_Percent', 'LIC', 'LIC_Percent',
    'RBC', 'HGB', 'HCT', 'MCV', 'MCH', 'MCHC', 'RDW', 'RDW_SD',
    'PLT', 'PCT', 'MPV', 'PDW', 'P_LCR', 'P_LCC',
]


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE PARSE HL7 (BH5100-specific)
# ═══════════════════════════════════════════════════════════

def _clean_hl7(message: str) -> List[str]:
    """Remove caracteres MLLP e normaliza quebras de linha."""
    clean = message.replace(SB, '').replace(EB, '')
    clean = clean.replace('\r\n', '\n').replace('\r', '\n')
    return clean.split('\n')


def _extract_barcode(segments: List[str]) -> str:
    """Extrai código de barras do OBR-3."""
    for seg in segments:
        fields = seg.split('|')
        if fields[0] == 'OBR' and len(fields) > 3:
            return fields[3]
    return ""


def _parse_obx_segments(segments: List[str]) -> Dict[str, str]:
    """
    Extrai resultados dos segmentos OBX.
    Retorna {nome_campo: valor_flag} (ex: {'WBC': '7.640', 'NE_Percent': '62.5H'}).
    """
    resultados: Dict[str, str] = {}
    for seg in segments:
        fields = seg.split('|')
        if fields[0] != 'OBX' or len(fields) < 9:
            continue
        if fields[2] == 'ED':
            continue  # ignora imagens

        test_id_raw = fields[3]
        test_id = test_id_raw.split('^')[0] if test_id_raw else ""
        nome = MAPEAMENTO.get(test_id)
        if nome is None:
            continue

        # L/H são flags do resultado e *, quando presente, também faz parte do
        # valor enviado pela máquina. O laboratório e o back aceitam esses
        # caracteres; não devemos removê-los (ex.: 73.26H, 30.4L, *****L).
        value = (fields[5] or '').strip()
        abnormal_flag = fields[8] if len(fields) > 8 else ""
        flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''
        resultados[nome] = f"{value}{flag}" if value else ''

    return resultados


def _calcular_percentual(valor_abs: str, wbc_val: str) -> Optional[str]:
    """Calcula percentual = (absoluto / WBC) * 100."""
    try:
        flag = ''
        v = valor_abs
        if v and v[-1] in ('H', 'L'):
            flag = v[-1]
            v = v[:-1]

        w = wbc_val
        if w and w[-1] in ('H', 'L'):
            w = w[:-1]

        abs_f = float(v)
        wbc_f = float(w)
        if wbc_f == 0:
            return None
        return f"{(abs_f / wbc_f) * 100}{flag}"
    except (ValueError, TypeError):
        return None


def _multiplicar_por_1000(valor_com_flag: str) -> str:
    """Multiplica valor por 1000, preservando flag."""
    flag = ''
    v = valor_com_flag
    if v and v[-1] in ('H', 'L'):
        flag = v[-1]
        v = v[:-1]
    try:
        return f"{round(float(v) * 1000, 4)}{flag}"
    except (ValueError, TypeError):
        return valor_com_flag


def _arredondar_e_formatar(valor_com_flag: str, nome_campo: str) -> str:
    """Arredonda para 1 casa decimal e aplica format_thousands em absolutos."""
    flag = ''
    v = valor_com_flag
    if v and v[-1] in ('H', 'L'):
        flag = v[-1]
        v = v[:-1]
    try:
        arredondado = str(round(float(v), 1))
        if nome_campo in CAMPOS_ABSOLUTOS:
            arredondado = format_thousands(arredondado)
        return f"{arredondado}{flag}"
    except (ValueError, TypeError):
        return valor_com_flag


def _processar_resultados(resultados: Dict[str, str]) -> Dict[str, str]:
    """Aplica cálculos de percentuais, multiplicação e arredondamento."""
    wbc_original = resultados.get('WBC')

    # Calcular percentuais ausentes
    for perc_key, abs_key in PERCENT_PAIRS:
        if perc_key not in resultados and abs_key in resultados and wbc_original:
            calc = _calcular_percentual(resultados[abs_key], wbc_original)
            if calc:
                resultados[perc_key] = calc
                logging.info(f"{perc_key} calculado: {calc}")

    # Multiplicar WBC e PLT por 1000
    for campo in CAMPOS_MULTIPLICAR:
        if campo in resultados:
            resultados[campo] = _multiplicar_por_1000(resultados[campo])

    # Arredondar e formatar
    for nome in list(resultados):
        resultados[nome] = _arredondar_e_formatar(resultados[nome], nome)

    return resultados


def parse_hl7_to_dict(hl7_message: str) -> Dict[str, str]:
    """Extrai campos do hemograma da mensagem HL7."""
    try:
        segments = _clean_hl7(hl7_message)
        resultados = _parse_obx_segments(segments)
        if not resultados:
            return {}
        return _processar_resultados(resultados)
    except Exception as e:
        logging.error(f"Erro ao extrair campos do HL7: {e}")
        return {}


def parse_hl7_to_txt(hl7_message: str) -> str:
    """Converte HL7 para formato TXT legível (debug)."""
    try:
        segments = _clean_hl7(hl7_message)
        barcode = _extract_barcode(segments)
        resultados = _parse_obx_segments(segments)
        if not resultados:
            return ""
        resultados = _processar_resultados(resultados)

        lines = [f"FileName: {barcode}"]
        for nome in ORDEM_TXT:
            if nome in resultados:
                lines.append(f"{nome}: {resultados[nome]}")
        return "\n".join(lines)
    except Exception as e:
        logging.error(f"Erro ao converter HL7: {e}")
        return ""


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorBH5100(BaseAnalisador):
    """Analisador para equipamento BH5100 (Mindray) via HL7/MLLP (serial ou TCP)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.MODO = str(self.CONFIG.get("modo") or "serial").strip().lower()
        self.TCP_HOST = str(self.CONFIG.get("tcp_host") or "0.0.0.0")
        porta = self.CONFIG.get("tcp_port")
        self.TCP_PORT = int(porta) if porta not in (None, "") else 5100

        self._lock_escrita = threading.Lock()
        self._conexao: Optional[socket.socket] = None
        self._servidor_tcp: Optional[socket.socket] = None
        self._popup_aberto = threading.Event()
        # Antes da primeira conexão não é queda: a máquina pode estar desligada.
        self._ja_conectou = False
        self._endereco_maquina = ""

    def get_file_extension(self) -> str:
        return '.hl7'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta mensagem HL7 completa delimitada por SB e EB+CR.
        Retorna (mensagem, buffer_restante) ou (None, buffer).

        Usa str.find (devolve -1, não lança) em vez de str.index: um EB órfão
        antes do SB (frame parcial/ruído deixado por uma queda da serial) fazia
        buffer.index(EB, start) estourar ValueError, e o loop serial ficava
        preso reprocessando o mesmo lixo. Aqui, lixo antes do SB é descartado e
        frames inválidos (EB sem CR em seguida) são ignorados.
        """
        start = buffer.find(SB)
        if start == -1:
            # Sem início de mensagem: descarta eventual lixo e aguarda o SB.
            return None, ""
        if start > 0:
            # Lixo antes do SB (ex.: EB órfão): descarta tudo antes do início.
            buffer = buffer[start:]

        end = buffer.find(EB)
        if end == -1:
            # EB ainda não chegou — aguarda mais dados.
            return None, buffer
        if end + 1 >= len(buffer):
            # EB no fim do buffer — aguarda o byte seguinte (esperado: CR).
            return None, buffer
        if buffer[end + 1] == CR:
            message = buffer[:end + 2]  # SB...EB+CR
            return message, buffer[end + 2:]
        # EB presente, mas o byte seguinte não é CR: frame inválido. Descarta
        # até depois desse EB e tenta de novo no restante.
        return self.detect_complete_message(buffer[end + 1:])

    def generate_ack(self, raw_message: str) -> Optional[bytes]:
        """Gera ACK HL7 para o BH5100."""
        try:
            segments = _clean_hl7(raw_message)
            msh = next((s for s in segments if s.startswith('MSH')), "")
            if not msh:
                return None

            fields = msh.split('|')
            msg_id = fields[9] if len(fields) > 9 else ""
            dt_now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

            ack = (
                f"MSH|^~\\&|LIS|PC|BH5100||{dt_now}||ACK^R01|{msg_id}|P|2.3.1{CR}"
                f"MSA|AA|{msg_id}{CR}"
            )
            return (SB + ack + EB + CR).encode('utf-8')
        except Exception as e:
            logging.error(f"Erro ao gerar ACK: {e}")
            return None

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa arquivo .hl7: extrai imagens, parse, filtra QC, monta payload.
        """
        # Lê o arquivo
        try:
            with open(filepath, 'r', encoding='utf-8', newline='') as f:
                conteudo_hl7 = f.read()
        except PermissionError:
            logging.error(f"✗ Permissão negada ao ler: {nome_arquivo}")
            return None
        except FileNotFoundError:
            logging.warning(f"Arquivo {nome_arquivo} não encontrado.")
            return None
        except Exception as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        if not conteudo_hl7 or not conteudo_hl7.strip():
            logging.warning(f"Arquivo {nome_arquivo} está vazio.")
            return None

        # Extrai imagens; os metadados (OBX-3) dizem qual é WBC/RBC/PLT.
        imagens_extraidas = []
        try:
            pasta_imagens = os.path.join(self.ENVIADOS_DIR, "imagens")
            prefixo = os.path.splitext(nome_arquivo)[0] + "_"
            imagens_extraidas = extrair_imagens_de_hl7_com_metadados(
                conteudo_hl7, pasta_imagens, prefixo
            )
            if imagens_extraidas:
                logging.info(
                    f"{len(imagens_extraidas)} imagem(ns) extraída(s) de {nome_arquivo}."
                )
        except Exception as e:
            logging.warning(f"Erro ao extrair imagens de {nome_arquivo}: {e}")

        # Parse HL7 → dict
        campos = parse_hl7_to_dict(conteudo_hl7)

        if not campos:
            logging.warning(f"Arquivo {nome_arquivo} NÃO contém campos de hemograma reconhecíveis.")
            logging.warning(f"  Conteúdo (primeiros 200 chars): {conteudo_hl7[:200]}")
            return None

        logging.info(f"Arquivo {nome_arquivo}: {len(campos)} campo(s) extraídos.")

        # Salva TXT debug
        txt_data = parse_hl7_to_txt(conteudo_hl7)
        if txt_data:
            try:
                pasta_txt = os.path.join(self.ENVIADOS_DIR, "txt")
                os.makedirs(pasta_txt, exist_ok=True)
                nome_txt = os.path.splitext(nome_arquivo)[0] + ".txt"
                with open(os.path.join(pasta_txt, nome_txt), "w", encoding="utf-8") as f:
                    f.write(txt_data)
                logging.info(f"TXT salvo: {nome_txt}")
            except Exception as e:
                logging.warning(f"Erro ao salvar TXT: {e}")

        # Extrai barcode
        segments = _clean_hl7(conteudo_hl7)
        barcode = _extract_barcode(segments)

        if not barcode:
            logging.error(f"Arquivo {nome_arquivo}: código de barras (OBR-3) não encontrado.")
            logging.error(f"  Movendo para '{self.REQUISICOES_NAO_ENVIADAS_DIR}'.")
            return []  # será movido para não-enviados pelo task_sender

        # Filtro de calibração/QC
        barcode_limpo = barcode.strip()
        if barcode_limpo == '0000000' or set(barcode_limpo) == {'0'}:
            logging.warning(f"Arquivo {nome_arquivo}: barcode '{barcode}' parece QC (todos zeros).")
            return []  # move para enviados sem enviar

        valores_principais = ['WBC', 'RBC', 'HGB', 'PLT']
        todos_zerados = all(
            _valor_zerado(campos.get(c, '')) for c in valores_principais if c in campos
        )
        if todos_zerados and all(c in campos for c in valores_principais):
            logging.warning(f"Arquivo {nome_arquivo}: todos os valores principais são zero (QC).")
            return []

        # Monta payload
        payload = {
            'franchise_credential_id': self.FRANCHISE_CREDENTIAL_ID,
            'FileName': barcode,
            'ExamCode': 'HEMO',
            **campos,
        }

        # Chave privada: sai do payload antes do POST do hemograma e é usada
        # só depois que o resultado é aceito, em _enviar_payload_webhook().
        if imagens_extraidas:
            payload['_exam_images'] = imagens_extraidas

        return [payload]

    def _enviar_payload_webhook(
        self, payload: Dict[str, Any], nome_arquivo: str, tag_identifier: str = ""
    ) -> bool:
        """
        Envia o hemograma e, se ele foi aceito, as imagens extraídas do HL7.

        A imagem é complemento: falha no S3 não muda o resultado do exame nem
        impede o arquivo de ir para 'enviados'.
        """
        imagens = payload.pop('_exam_images', [])

        enviado = super()._enviar_payload_webhook(payload, nome_arquivo, tag_identifier)

        if enviado and imagens:
            try:
                ExamImageUploader(
                    api_url=self.EXAM_IMAGES_URL,
                    token=self.EXAM_IMAGES_MACHINE_TOKEN,
                    franchise_credential_id=self.FRANCHISE_CREDENTIAL_ID,
                    machine=self.machine_id,
                ).send(
                    barcode=str(payload.get('FileName', '')),
                    exam_code=str(payload.get('ExamCode', 'HEMO')),
                    imagens=imagens,
                )
            except Exception as e:
                logging.error(
                    f"Erro ao enviar imagens de {nome_arquivo}: "
                    f"{type(e).__name__}: {e}"
                )

        return enviado

    # ── TRANSPORTE (serial ou TCP) ────────────────────────────

    def _send_ack(self, ack_data: bytes) -> None:
        """
        Envia o ACK^R01 pela conexão ativa (serial ou TCP).

        Sobrescreve o _send_ack da base (que escreve só na serial) para rotear
        pelo _escrever, fazendo o ACK sair pela conexão TCP quando for o caso.
        Em serial o comportamento é o mesmo de antes: escreve em self._serial.
        """
        if self._escrever(ack_data):
            logging.info("✓ ACK^R01 enviado à máquina.")
        else:
            logging.error("✗ ACK^R01 não chegou à máquina: ela deve reenviar o resultado.")

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
        Em TCP, "conectado" é ter a máquina ligada ao programa. Enquanto ela
        nunca conectou, não conta como queda (watchdog não encerra).
        """
        if self.MODO == "tcp":
            return not self._ja_conectou or self._conexao is not None
        return super()._serial_is_open()

    def _conexao_ativa(self) -> bool:
        """No /health (painel do leitor universal), em TCP só conta a máquina conectada agora."""
        if self.MODO == "tcp":
            return self._conexao is not None
        return super()._conexao_ativa()

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
            "O programa será encerrado. Abra o bh5100.exe de novo e, no menu de",
            "comunicação (LIS) do equipamento Mindray, reconecte ao host.",
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

        Thread(target=_mostrar, daemon=True, name="popup-queda").start()

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
                        f"Provável causa: outro bh5100.exe (ou outro programa) já está usando a porta.\n"
                        f"Feche o programa duplicado. Esta janela tenta de novo a cada {TCP_RETRY_INTERVAL}s."
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
            self._ja_conectou = True
            self._endereco_maquina = f"{endereco[0]}:{endereco[1]}"
            logging.info(f"✓ Máquina conectada por TCP: {self._endereco_maquina}")
            Thread(target=self._ler_tcp, args=(conexao, f"{endereco[0]}:{endereco[1]}"), daemon=True).start()

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


def _valor_zerado(valor_com_flag: str) -> bool:
    """Verifica se um valor (com possível flag) é zero."""
    v = valor_com_flag
    if v and v[-1] in ('H', 'L'):
        v = v[:-1]
    try:
        return float(v) == 0.0
    except (ValueError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════
# Lidos também pelo leitor universal (universal/leitor.py), que abre a máquina
# com "leitor.exe --maquina bh5100" e mostra o config no painel.

MACHINE_ID = 'bh5100'
MACHINE_NAME = 'BH5100'
HEALTH_PORT = 8080
CONFIG_DEFAULTS = {
    # Default continua serial (as unidades atuais rodam por serial); a unidade
    # que for para rede troca para "tcp". Porta 5100 = número da máquina.
    'modo': 'serial',
    'com_port': 'COM3',
    'baud_rate': 9600,
    'tcp_host': '0.0.0.0',
    'tcp_port': 5100,
    'franchise_credential_id': '88cf9273-5044-47f4-b8f6-01160345a190',
    'webhook_url': (
        'https://apoio.internal.vidaexame.com/api/integration/bh5100'
        '?franchise_credential_id={franchise_credential_id}'
    ),
    'exam_images_url': '',
    'exam_images_machine_token': '',
}


def criar_analisador() -> AnalisadorBH5100:
    """Analisador com a configuração de produção (config_bh5100.json ao lado do .exe)."""
    return AnalisadorBH5100(
        machine_id=MACHINE_ID,
        machine_name=MACHINE_NAME,
        config_defaults=CONFIG_DEFAULTS,
        health_port=HEALTH_PORT,
    )


if __name__ == "__main__":
    criar_analisador().start()
