"""
pkl.py — Bridge ASTM E1394-97 para o analisador PKL 125

Comunicação serial com o equipamento PKL 125 via protocolo ASTM (STX/ETX frames).
Recebe resultados de hemograma, agrupa por tag_id (specimen ID) e envia em batch
para a API VIDA Exame via webhook.

Modo bidirecional opcional: também consulta a API VIDA por ordens pendentes e
envia mensagens ASTM de solicitação ao equipamento.

Baseado no pkl_integrator C# (PklBridge) e no molde estrutural do bh5100.py.
"""

import serial
import time
import requests
import logging
import datetime
import os
import shutil
import sys
import json
from threading import Thread, Lock

# Import do módulo de limpeza compartilhado
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.file_cleanup import FileCleanupConfig, start_cleanup_thread
from shared.config_loader import load_config

# ================= CONFIGURAÇÕES =================
# As configurações abaixo são valores padrão.
# Para alterar COM_PORT ou FRANCHISE_CREDENTIAL_ID sem recompilar o .exe,
# edite o arquivo config_pkl.json que fica ao lado do executável.
# Se o arquivo não existir, ele será criado automaticamente na primeira execução.
_config, _config_status = load_config('pkl', {
    'com_port': 'COM3',
    'baud_rate': 19200,
    'franchise_credential_id': '4e768395-38bb-46d8-a74a-1c7efba027b8',
    'webhook_url': 'https://apoio.internal.vidaexame.com/api/integration/pkl-125?franchise_credential_id={franchise_credential_id}',
})

COM_PORT = _config['com_port']
BAUD_RATE = _config['baud_rate']
FRANCHISE_CREDENTIAL_ID = _config['franchise_credential_id']

# Webhook do Vida Exame
# Local: http://localhost/api/integration/pkl-125
# Produção: https://apoio.internal.vidaexame.com/api/integration/pkl-125
WEBHOOK_URL = _config['webhook_url'].format(franchise_credential_id=FRANCHISE_CREDENTIAL_ID)

# Flag para modo bidirecional (solicitar exames à API VIDA e enviar ordens ASTM ao equipamento)
ENABLE_BIDIRECTIONAL = True

# Intervalo de polling para ordens pendentes (segundos)
POLL_ORDERS_INTERVAL = 30

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
MAX_RETRY = 5
RETRY_INTERVAL = 60  # segundos entre tentativas de reenvio (1 minuto)
# =================================================

# Pastas de trabalho — na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorPKL125")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
REQUISICOES_NAO_ENVIADAS_DIR = os.path.join(BASE_DIR, "requisições não enviadas")
LOG_FILE = os.path.join(BASE_DIR, "analisador_pkl125.log")
# =================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)
os.makedirs(REQUISICOES_NAO_ENVIADAS_DIR, exist_ok=True)

# Redirecionar stdout/stderr para evitar travamento em modo --noconsole (PyInstaller)
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8')]
)
logging.info(_config_status)

# ================= CONSTANTES ASTM E1394-97 =================
STX = chr(0x02)   # Start of Text
ETX = chr(0x03)   # End of Text
ENQ = chr(0x05)   # Enquiry
ACK = chr(0x06)   # Acknowledge
NAK = chr(0x15)   # Not Acknowledge
EOT = chr(0x04)   # End of Transmission
CR  = chr(0x0D)   # Carriage Return
LF  = chr(0x0A)   # Line Feed

# ================= ESTADO DA SESSÃO ASTM =================
_session_lock = Lock()
_pending_batches: dict = {}       # tag_id → {"tag_id", "results": [], "patient": {...}, "order_time": datetime}
_current_tag_id: str | None = None
_session_patient: dict | None = None
_pending_queries: list = []       # Lista de specimen_ids recebidos em registros Q
# ===========================================================


# ═══════════════════════════════════════════════════════════
# FASE 2: PARSER ASTM
# ═══════════════════════════════════════════════════════════

def calculate_checksum(data: str) -> str:
    """
    Calcula o checksum ASTM: soma dos bytes módulo 256, retornado como hex de 2 dígitos.
    O cálculo inclui do byte após STX (frame number) até ETX inclusive.
    """
    total = sum(ord(c) for c in data) % 256
    return f"{total:02X}"


def parse_astm_frame(frame: str) -> dict | None:
    """
    Extrai e valida um frame ASTM individual.
    Formato: STX + frame_number(1 char) + content + ETX + checksum(2 hex chars) + CR + LF

    Retorna dict com type, content, fields, seq, checksum_valid ou None se inválido.
    """
    try:
        if not frame or len(frame) < 6:
            return None

        # Localizar STX e ETX
        stx_idx = frame.find(STX)
        if stx_idx == -1:
            return None

        etx_idx = frame.find(ETX, stx_idx + 1)
        if etx_idx == -1 or etx_idx <= stx_idx + 2:
            return None

        # Frame number (1 char após STX, convertido de ASCII)
        frame_number_char = frame[stx_idx + 1]
        frame_number = ord(frame_number_char) - 48  # '1' → 1, '2' → 2, etc.

        # Conteúdo entre STX+2 e ETX
        content = frame[stx_idx + 2:etx_idx]

        # Record type (primeiro char do conteúdo)
        record_type = content[0] if content else ""

        # Checksum (2 chars hex após ETX)
        checksum_received = ""
        if etx_idx + 2 < len(frame):
            checksum_received = frame[etx_idx + 1:etx_idx + 3]

        # Validar checksum: dados do frame number até ETX inclusive
        data_for_checksum = frame[stx_idx + 1:etx_idx + 1]
        checksum_calculated = calculate_checksum(data_for_checksum)
        checksum_valid = checksum_received.upper() == checksum_calculated.upper()

        # Parse dos campos (separador '|')
        fields = content.split('|')

        return {
            "type": record_type,
            "content": content,
            "fields": fields,
            "seq": frame_number,
            "checksum_received": checksum_received,
            "checksum_calculated": checksum_calculated,
            "checksum_valid": checksum_valid,
        }
    except Exception as e:
        logging.error(f"Erro ao parse frame ASTM: {e}")
        return None


def parse_astm_record(fields: list[str], record_type: str) -> dict:
    """
    Converte os campos de um registro ASTM em um dicionário estruturado,
    dependendo do tipo de registro (H, P, O, Q, R, L).
    """
    record = {"type": record_type, "raw_fields": fields}

    try:
        if record_type == "H":  # Header
            record["sender_id"] = fields[4] if len(fields) > 4 else ""
            record["receiver_id"] = fields[9] if len(fields) > 9 else ""
            record["timestamp"] = fields[13] if len(fields) > 13 else ""
            record["version"] = fields[12] if len(fields) > 12 else ""

        elif record_type == "P":  # Patient
            record["patient_id"] = fields[2] if len(fields) > 2 else ""
            # Nome no campo 5, formato "LastName^FirstName"
            name_parts = (fields[5] if len(fields) > 5 else "").split('^')
            record["last_name"] = name_parts[0] if len(name_parts) > 0 else ""
            record["first_name"] = name_parts[1] if len(name_parts) > 1 else ""
            record["birth_date"] = fields[7] if len(fields) > 7 else ""
            record["gender"] = fields[8] if len(fields) > 8 else ""

        elif record_type == "O":  # Order
            # specimen_id = campo 2, formato "ID^^^^Type" — extrai só o ID
            specimen_raw = fields[2] if len(fields) > 2 else ""
            record["specimen_id"] = specimen_raw.split('^')[0] if specimen_raw else ""
            record["universal_test_id"] = fields[4] if len(fields) > 4 else ""
            record["priority"] = fields[5] if len(fields) > 5 else ""
            record["action_code"] = fields[11] if len(fields) > 11 else ""
            record["sample_type"] = fields[15] if len(fields) > 15 else ""

        elif record_type == "Q":  # Query — equipamento solicita informações sobre amostra
            # Q|1|^SpecimenID||ALL||||||||O
            # Campo 2: número da sequência de início
            # Campo 3: specimen ID (formato ^ID)
            specimen_raw = fields[3] if len(fields) > 3 else ""
            record["specimen_id"] = specimen_raw.lstrip('^') if specimen_raw else ""
            record["query_type"] = fields[4] if len(fields) > 4 else ""  # ALL = todos os testes
            record["action_code"] = fields[12] if len(fields) > 12 else ""

        elif record_type == "R":  # Result
            record["universal_test_id"] = fields[3] if len(fields) > 3 else ""
            record["test_name"] = fields[4] if len(fields) > 4 else ""
            record["value"] = fields[5] if len(fields) > 5 else ""
            record["units"] = fields[6] if len(fields) > 6 else ""
            record["reference_range"] = fields[7] if len(fields) > 7 else ""
            record["abnormal_flag"] = fields[8] if len(fields) > 8 else ""
            record["status"] = fields[9] if len(fields) > 9 else ""
            record["instrument_id"] = fields[12] if len(fields) > 12 else ""

        elif record_type == "L":  # Terminator
            # L|1|N → fields[0]=L, fields[1]=sequence, fields[2]=termination_code
            record["termination_code"] = fields[2] if len(fields) > 2 else "N"

    except Exception as e:
        logging.error(f"Erro ao parse registro ASTM tipo '{record_type}': {e}")

    return record


def parse_universal_test_id(universal_test_id: str, test_name: str = "") -> tuple[str, str]:
    """
    Extrai (exam_code, test) do Universal Test ID ASTM.
    Formato esperado: ^^^EXAM_CODE^TEST ou ^^^TEST
    Se não encontrar exam_code, usa 'HEMO' como default (hemograma).
    Se universal_test_id estiver vazio, usa test_name como fallback.
    """
    exam_code = "HEMO"  # default para hemograma
    test = ""

    if universal_test_id:
        # Remove prefixos ^^^ e split por ^
        cleaned = universal_test_id.lstrip('^')
        parts = cleaned.split('^')
        # Filtra partes vazias
        parts = [p for p in parts if p]

        if len(parts) >= 2:
            exam_code = parts[0]
            test = parts[1]
        elif len(parts) == 1:
            test = parts[0]
    elif test_name:
        test = test_name

    return exam_code, test


# ═══════════════════════════════════════════════════════════
# FASE 3: GERENCIAMENTO DE SESSÃO ASTM
# ═══════════════════════════════════════════════════════════

def process_astm_record(record: dict) -> None:
    """
    Processa um registro ASTM individual, mantendo estado da sessão.
    - H: loga header
    - P: armazena dados do paciente
    - O: cria batch por tag_id (specimen_id)
    - R: adiciona resultado ao batch do tag_id
    - L: finaliza sessão e envia batches
    """
    global _current_tag_id, _session_patient

    record_type = record.get("type", "")

    try:
        if record_type == "H":
            logging.info(f"[ASTM] Header recebido | Sender: {record.get('sender_id', '?')} | "
                         f"Receiver: {record.get('receiver_id', '?')}")

        elif record_type == "P":
            _session_patient = {
                "patient_id": record.get("patient_id", ""),
                "first_name": record.get("first_name", ""),
                "last_name": record.get("last_name", ""),
                "birth_date": record.get("birth_date", ""),
                "gender": record.get("gender", ""),
            }
            logging.info(f"[ASTM] Patient recebido | ID: {_session_patient['patient_id']} | "
                         f"Nome: {_session_patient['first_name']} {_session_patient['last_name']}")

        elif record_type == "O":
            tag_id = record.get("specimen_id", "")
            if not tag_id:
                logging.warning("[ASTM] Order sem specimen_id — ignorando")
                return

            _current_tag_id = tag_id

            with _session_lock:
                if tag_id not in _pending_batches:
                    _pending_batches[tag_id] = {
                        "tag_id": tag_id,
                        "results": [],
                        "patient": _session_patient.copy() if _session_patient else {},
                        "order_time": datetime.datetime.now(),
                    }

            logging.info(f"[ASTM] Order recebido | tag_id: {tag_id} | "
                         f"Priority: {record.get('priority', '?')} | "
                         f"Action: {record.get('action_code', '?')}")

        elif record_type == "Q":
            # Query — o equipamento PKL está perguntando se há ordens para uma amostra
            specimen_id = record.get("specimen_id", "")
            query_type = record.get("query_type", "")
            logging.info(f"[ASTM] Query recebido | specimen_id: {specimen_id} | "
                         f"query_type: {query_type} | action: {record.get('action_code', '?')}")
            # Armazenar query para responder após o EOT
            if specimen_id:
                _pending_queries.append(specimen_id)

        elif record_type == "R":
            universal_test_id = record.get("universal_test_id", "")
            test_name = record.get("test_name", "")
            value = record.get("value", "")
            units = record.get("units", "")
            abnormal_flag = record.get("abnormal_flag", "")

            exam_code, test = parse_universal_test_id(universal_test_id, test_name)

            # Determinar tag_id: do instrument_id ou usar o último conhecido
            tag_id = record.get("instrument_id", "")
            if not tag_id:
                tag_id = _current_tag_id

            if not tag_id:
                logging.warning(f"[ASTM] Result sem tag_id — ignorando | Test: {test_name} | Value: {value}")
                return

            with _session_lock:
                if tag_id not in _pending_batches:
                    _pending_batches[tag_id] = {
                        "tag_id": tag_id,
                        "results": [],
                        "patient": _session_patient.copy() if _session_patient else {},
                        "order_time": datetime.datetime.now(),
                    }

                _pending_batches[tag_id]["results"].append({
                    "exam_code": exam_code,
                    "test": test,
                    "value": value,
                })

            logging.debug(f"[ASTM] Result adicionado | tag_id: {tag_id} | "
                          f"ExamCode: {exam_code} | Test: {test} | Value: {value}{units} "
                          f"{'[' + abnormal_flag + ']' if abnormal_flag else ''}")

        elif record_type == "L":
            logging.info(f"[ASTM] Terminator recebido | Code: {record.get('termination_code', 'N')}")
            finalize_session()

    except Exception as e:
        logging.error(f"Erro ao processar registro ASTM tipo '{record_type}': {e}")


def finalize_session() -> None:
    """
    Finaliza a sessão ASTM: para cada batch pendente, monta o payload,
    salva como arquivo .astm na pasta gerados e limpa os batches.
    O envio ao webhook é feito pela thread task_sender_to_webhook.
    """
    global _pending_batches, _current_tag_id, _session_patient

    with _session_lock:
        batches_to_send = dict(_pending_batches)
        _pending_batches.clear()

    if not batches_to_send:
        logging.debug("[ASTM] Nenhum batch pendente para finalizar")
        return

    logging.info(f"[ASTM→VIDA] Finalizando sessão com {len(batches_to_send)} batch(es)")

    for tag_id, batch in batches_to_send.items():
        if not batch["results"]:
            logging.warning(f"[ASTM→VIDA] Batch {tag_id} sem resultados — ignorando")
            continue

        # Montar payload conforme VidaResultRequest do C#
        payload = {
            "franchise_credential_id": FRANCHISE_CREDENTIAL_ID,
            "tag_id": tag_id,
            "results": batch["results"],
        }

        # Salvar como .astm na pasta gerados
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"exame_{timestamp}_{tag_id}.astm"
        file_path = os.path.join(GERADOS_DIR, filename)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logging.info(f"[ASTM→VIDA] Batch salvo: {filename} | tag_id: {tag_id} | "
                         f"Resultados: {len(batch['results'])}")
        except OSError as e:
            logging.error(f"[ASTM→VIDA] Erro ao salvar batch {filename}: {e}")

    _current_tag_id = None
    _session_patient = None


# ═══════════════════════════════════════════════════════════
# FASE 5: ENVIO AO WEBHOOK
# ═══════════════════════════════════════════════════════════

def _enviar_payload_webhook(payload: dict, nome_arquivo: str, tag_id: str) -> bool:
    """
    Envia um payload para o webhook com até MAX_RETRY tentativas.
    Retorna True se o envio foi bem-sucedido, False caso contrário.
    """
    headers = {'Content-Type': 'application/json'}

    for tentativa in range(1, MAX_RETRY + 1):
        try:
            logging.info(f"Enviando {nome_arquivo} (tag_id: {tag_id}) para o webhook... "
                         f"[Tentativa {tentativa}/{MAX_RETRY}]")
            response = requests.post(
                WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in (200, 201):
                logging.info(f"✓ Sucesso ({response.status_code}): {nome_arquivo} enviado ao Webhook.")
                try:
                    resp_json = response.json()
                    msg = resp_json.get('message', 'OK')
                    logging.info(f"  Mensagem: {msg}")
                except Exception:
                    pass
                return True
            elif response.status_code == 404:
                logging.error(f"✗ ERRO 404: Endpoint não encontrado para {nome_arquivo}.")
                logging.error(f"  Verifique se a URL está correta: {WEBHOOK_URL}")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code == 400:
                logging.error(f"✗ ERRO 400: Requisição inválida para {nome_arquivo} (tag_id: {tag_id}).")
                logging.error(f"  Possíveis causas: tag_id não encontrado, procedimento já liberado, ou payload inválido.")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code == 500:
                logging.error(f"✗ ERRO 500: Erro interno do servidor ao processar {nome_arquivo}.")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code in (401, 403):
                logging.error(f"✗ ERRO {response.status_code}: Falha de autenticação para {nome_arquivo}.")
                logging.error(f"  Verifique o FRANCHISE_CREDENTIAL_ID: {FRANCHISE_CREDENTIAL_ID}")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code in (502, 503):
                logging.error(f"✗ ERRO {response.status_code}: Servidor indisponível para {nome_arquivo}.")
                logging.error(f"  O servidor pode estar fora do ar ou em manutenção.")
            else:
                logging.error(f"✗ Webhook recusou {nome_arquivo}: Status HTTP {response.status_code}")
                logging.error(f"  Resposta: {response.text[:500]}")

        except requests.exceptions.ConnectionError as e:
            logging.error(f"✗ ERRO DE CONEXÃO: Não foi possível conectar ao servidor para {nome_arquivo}.")
            logging.error(f"  URL: {WEBHOOK_URL}")
            logging.error(f"  Detalhe: {e}")
        except requests.exceptions.Timeout as e:
            logging.error(f"✗ TIMEOUT: O servidor não respondeu a tempo para {nome_arquivo} (30s).")
            logging.error(f"  URL: {WEBHOOK_URL}")
        except requests.exceptions.TooManyRedirects as e:
            logging.error(f"✗ ERRO: Muitos redirecionamentos ao acessar {WEBHOOK_URL}: {e}")
        except requests.exceptions.RequestException as e:
            logging.error(f"✗ ERRO DE REDE ao enviar {nome_arquivo}: {type(e).__name__}: {e}")
            logging.error(f"  URL: {WEBHOOK_URL}")

        if tentativa < MAX_RETRY:
            logging.warning(f"  Tentativa {tentativa}/{MAX_RETRY} falhou. Nova tentativa em {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)

    return False


def task_sender_to_webhook():
    """
    Thread que monitora a pasta GERADOS_DIR por arquivos .astm e os envia ao webhook.
    Arquivos enviados com sucesso vão para ENVIADOS_DIR.
    Arquivos com falha definitiva vão para REQUISICOES_NAO_ENVIADAS_DIR.
    """
    logging.info("Iniciando monitor de envio para Webhook...")
    logging.info(f"URL do Webhook: {WEBHOOK_URL}")
    logging.info(f"Verificando arquivos a cada {CHECK_FILES_INTERVAL}s na pasta: {GERADOS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")

    erros_consecutivos = 0
    MAX_ERROS_CONSECUTIVOS = 10

    while True:
        try:
            arquivos = [f for f in os.listdir(GERADOS_DIR) if f.endswith('.astm')]

            if arquivos:
                logging.info(f"Encontrados {len(arquivos)} arquivo(s) ASTM para processar.")
                erros_consecutivos = 0

            for nome_arquivo in arquivos:
                caminho_origem = os.path.join(GERADOS_DIR, nome_arquivo)
                caminho_destino = os.path.join(ENVIADOS_DIR, nome_arquivo)
                caminho_nao_enviado = os.path.join(REQUISICOES_NAO_ENVIADAS_DIR, nome_arquivo)

                # Lê o arquivo ASTM (JSON)
                try:
                    with open(caminho_origem, 'r', encoding='utf-8') as f:
                        payload = json.load(f)
                except PermissionError:
                    logging.error(f"✗ Permissão negada ao ler arquivo: {nome_arquivo} — o arquivo pode estar em uso.")
                    continue
                except FileNotFoundError:
                    logging.warning(f"Arquivo {nome_arquivo} não encontrado (pode ter sido removido por outro processo).")
                    continue
                except json.JSONDecodeError as e:
                    logging.error(f"✗ JSON inválido em {nome_arquivo}: {e}")
                    shutil.move(caminho_origem, caminho_nao_enviado)
                    continue
                except Exception as e:
                    logging.error(f"✗ Erro inesperado ao ler arquivo {nome_arquivo}: {type(e).__name__}: {e}")
                    continue

                if not payload:
                    logging.warning(f"Arquivo {nome_arquivo} está vazio, movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)
                    continue

                tag_id = payload.get('tag_id', '')
                results = payload.get('results', [])

                if not tag_id:
                    logging.error(f"Arquivo {nome_arquivo}: tag_id não encontrado no payload.")
                    logging.error(f"  Não é possível enviar sem tag_id — é obrigatório para identificar o procedimento.")
                    logging.error(f"  Movendo para '{REQUISICOES_NAO_ENVIADAS_DIR}'.")
                    shutil.move(caminho_origem, caminho_nao_enviado)
                    continue

                if not results:
                    logging.warning(f"Arquivo {nome_arquivo}: payload sem resultados (tag_id: {tag_id}).")
                    logging.warning(f"  Movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)
                    continue

                logging.info(f"Arquivo {nome_arquivo}: {len(results)} resultado(s) | tag_id: {tag_id}")

                # Salvar TXT de debug em ENVIADOS_DIR/txt/
                try:
                    pasta_txt = os.path.join(ENVIADOS_DIR, "txt")
                    os.makedirs(pasta_txt, exist_ok=True)
                    nome_txt = os.path.splitext(nome_arquivo)[0] + ".txt"
                    caminho_txt = os.path.join(pasta_txt, nome_txt)

                    linhas = [f"tag_id: {tag_id}"]
                    for r in results:
                        linhas.append(f"{r.get('exam_code', '?')}/{r.get('test', '?')}: {r.get('value', '?')}")
                    with open(caminho_txt, "w", encoding="utf-8") as f:
                        f.write("\n".join(linhas))
                    logging.info(f"TXT salvo: {caminho_txt}")
                except Exception as e:
                    logging.warning(f"Erro ao salvar TXT de debug para {nome_arquivo}: {e}")

                # Tenta enviar com retry
                enviado = _enviar_payload_webhook(payload, nome_arquivo, tag_id)

                if enviado:
                    shutil.move(caminho_origem, caminho_destino)
                    logging.info(f"  Arquivo movido para: {caminho_destino}")
                else:
                    logging.error(f"✗ Falha definitiva: {nome_arquivo} não foi enviado após {MAX_RETRY} tentativas.")
                    logging.error(f"  Movendo para '{REQUISICOES_NAO_ENVIADAS_DIR}'.")
                    shutil.move(caminho_origem, caminho_nao_enviado)

        except FileNotFoundError as e:
            logging.error(f"Erro no monitor de envio: diretório não encontrado: {e}")
            erros_consecutivos += 1
        except PermissionError as e:
            logging.error(f"Erro no monitor de envio: permissão negada: {e}")
            erros_consecutivos += 1
        except OSError as e:
            logging.error(f"Erro de sistema no monitor de envio: {type(e).__name__}: {e}")
            erros_consecutivos += 1
        except Exception as e:
            logging.error(f"Erro inesperado no monitor de envio: {type(e).__name__}: {e}")
            erros_consecutivos += 1

        if erros_consecutivos >= MAX_ERROS_CONSECUTIVOS:
            logging.critical(f"ALERTA: {erros_consecutivos} erros consecutivos no monitor de envio!")
            logging.critical(f"  O serviço continua rodando, mas pode haver um problema persistente.")
            logging.critical(f"  Verifique: (1) Permissões das pastas (2) Espaço em disco (3) Conexão de rede")
            erros_consecutivos = 0

        time.sleep(CHECK_FILES_INTERVAL)


# ═══════════════════════════════════════════════════════════
# FASE 6: BIDIRECIONAL — SOLICITAÇÃO DE EXAMES
# ═══════════════════════════════════════════════════════════

def build_astm_order_message(tag_id: str, exam_data: dict) -> str:
    """
    Constrói uma mensagem ASTM completa (H + P + O + L) para solicitar exames
    ao equipamento PKL 125, usando dados da API VIDA.

    Args:
        tag_id: Etiqueta do tubo (specimen ID)
        exam_data: Dicionário com patient_name, birth_date, gender, exams (lista de {exam_code, test})

    Returns:
        String com a mensagem ASTM completa (frames separados por linha)
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    patient_name = exam_data.get("patient_name", f"Paciente {tag_id}")
    birth_date = exam_data.get("birth_date", datetime.datetime.now().strftime("%Y%m%d"))
    gender = exam_data.get("gender", "M")
    exams = exam_data.get("exams", [])

    # Converter data de nascimento para formato ASTM (yyyyMMdd)
    try:
        birth_date_formatted = datetime.datetime.strptime(birth_date, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        birth_date_formatted = datetime.datetime.now().strftime("%Y%m%d")

    lines = []

    # H - Header Record
    lines.append(f"H|\\^&|||PKL Bridge^1.0^PKL125|||||||P|1|{timestamp}")

    # P - Patient Record
    lines.append(f"P|1|||{tag_id}|{patient_name}||{birth_date_formatted}|{gender}"
                 f"|||||||||||||||||||")

    # O - Order Record (um para todos os testes)
    test_codes = "`".join([f"^^^{e.get('test', '')}" for e in exams])
    sample_type = exam_data.get("sample_type", "SORO")
    lines.append(f"O|2|{tag_id}^^^^N||{test_codes}|R|{timestamp}|||||||||{sample_type}"
                 f"||||||||||O")

    # L - Terminator Record
    lines.append("L|1|N")

    return "\n".join(lines)


def respond_to_query(ser: serial.Serial, specimen_id: str) -> None:
    """
    Responde a uma query ASTM do PKL 125 consultando a API VIDA por ordens
    pendentes para o specimen_id informado e enviando a resposta via serial.

    Se não houver ordens pendentes, envia uma mensagem ASTM vazia (apenas H+L)
    para que o equipamento saiba que não há trabalho a fazer.
    """
    logging.info(f"[Query→PKL] Consultando API por ordens para specimen_id: {specimen_id}")

    # Consultar API VIDA por ordens pendentes para este specimen_id
    poll_url = (f"https://apoio.internal.vidaexame.com/api/integration/pkl-125"
                f"?franchise_credential_id={FRANCHISE_CREDENTIAL_ID}"
                f"&tag_id={specimen_id}")

    order_data = None
    try:
        response = requests.get(poll_url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            orders = data.get("data", [])
            if orders and len(orders) > 0:
                order_data = orders[0]  # Usar a primeira ordem
                logging.info(f"[Query→PKL] Ordem encontrada para specimen_id: {specimen_id}")
            else:
                logging.info(f"[Query→PKL] Nenhuma ordem pendente para specimen_id: {specimen_id}")
        else:
            logging.warning(f"[Query→PKL] API retornou status {response.status_code} para specimen_id: {specimen_id}")
    except requests.exceptions.RequestException as e:
        logging.error(f"[Query→PKL] Erro ao consultar API: {type(e).__name__}: {e}")

    # Construir e enviar mensagem ASTM de resposta
    if order_data:
        # Tem ordem — enviar H + P + O + L
        astm_message = build_astm_order_message(specimen_id, order_data)
        logging.info(f"[Query→PKL] Enviando ordem de exame para specimen_id: {specimen_id}")
    else:
        # Sem ordem — enviar mensagem vazia (H + L) para o equipamento saber que não há trabalho
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        astm_message = f"H|\\^&|||PKL Bridge^1.0^PKL125|||||||P|1|{timestamp}\nL|1|N"
        logging.info(f"[Query→PKL] Enviando resposta vazia (sem ordens) para specimen_id: {specimen_id}")

    success = send_astm_message_via_serial(ser, astm_message)
    if success:
        logging.info(f"[Query→PKL] ✓ Resposta enviada com sucesso para specimen_id: {specimen_id}")
    else:
        logging.error(f"[Query→PKL] ✗ Falha ao enviar resposta para specimen_id: {specimen_id}")


def build_astm_frame(frame_number: int, content: str) -> bytes:
    """
    Constrói um frame ASTM completo em bytes:
    STX + frame_number(ASCII) + content + ETX + checksum(2 hex) + CR + LF
    """
    fn_char = chr(frame_number + 48)  # 1→'1', 2→'2', etc.
    data_for_checksum = fn_char + content + ETX
    checksum = calculate_checksum(data_for_checksum)
    frame = STX + fn_char + content + ETX + checksum + CR + LF
    return frame.encode('ascii', errors='ignore')


def send_astm_message_via_serial(ser: serial.Serial, message: str) -> bool:
    """
    Envia uma mensagem ASTM completa via porta serial com handshake ENQ/ACK.

    Protocolo:
    1. Envia ENQ, aguarda ACK (timeout 5s)
    2. Para cada linha (frame): envia frame, aguarda ACK
    3. Envia EOT

    Returns:
        True se todos os ACKs foram recebidos, False caso contrário.
    """
    if not ser or not ser.is_open:
        logging.error("[Bridge→PKL] Porta serial não está aberta para envio ASTM")
        return False

    try:
        lines = message.strip().split('\n')
        if not lines:
            return False

        logging.info(f"[Bridge→PKL] Enviando mensagem ASTM ({len(lines)} frames)...")

        # 1. ENQ → aguardar ACK
        ser.write(ENQ.encode('ascii'))
        ser.flush()
        logging.debug("[Bridge→PKL] ENQ enviado, aguardando ACK...")

        ack_received = _wait_for_byte(ser, ACK, timeout_ms=5000)
        if not ack_received:
            logging.error("[Bridge→PKL] ACK não recebido após ENQ — abortando")
            return False
        logging.debug("[Bridge→PKL] ACK recebido")

        # 2. Enviar cada frame
        for i, line in enumerate(lines):
            frame_number = (i + 1) % 8  # 1-7, 0, 1-7, 0...
            frame_bytes = build_astm_frame(frame_number, line)

            ser.write(frame_bytes)
            ser.flush()
            logging.debug(f"[Bridge→PKL] Frame {frame_number} enviado ({len(frame_bytes)} bytes)")

            ack_received = _wait_for_byte(ser, ACK, timeout_ms=5000)
            if not ack_received:
                logging.error(f"[Bridge→PKL] ACK não recebido para frame {frame_number} — abortando")
                return False
            logging.debug(f"[Bridge→PKL] ACK recebido para frame {frame_number}")

        # 3. EOT
        ser.write(EOT.encode('ascii'))
        ser.flush()
        logging.info("[Bridge→PKL] Mensagem ASTM enviada com sucesso")

        return True

    except serial.SerialException as e:
        logging.error(f"[Bridge→PKL] Erro serial ao enviar ASTM: {e}")
        return False
    except Exception as e:
        logging.error(f"[Bridge→PKL] Erro ao enviar ASTM: {e}")
        return False


def _wait_for_byte(ser: serial.Serial, expected_byte: str, timeout_ms: int = 5000) -> bool:
    """
    Aguarda até timeout_ms por um byte específico na porta serial.
    Retorna True se o byte foi recebido, False se timeout.
    """
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            if ser.in_waiting > 0:
                data = ser.read(1)
                if data.decode('ascii', errors='ignore') == expected_byte:
                    return True
        except serial.SerialException:
            return False
        time.sleep(0.05)
    return False


def task_exam_requester(ser: serial.Serial):
    """
    Thread que faz polling da API VIDA por ordens de exame pendentes e
    envia mensagens ASTM de solicitação ao equipamento PKL 125.

    Fluxo:
    1. GET /api/integration/pkl-125?franchise_credential_id=...&tag_id=...
       (ou endpoint específico para listar ordens pendentes)
    2. Para cada ordem pendente, constrói mensagem ASTM
    3. Envia via serial com handshake ENQ/ACK
    """
    if not ENABLE_BIDIRECTIONAL:
        logging.info("Modo bidirecional DESABILITADO. Thread de solicitação não será iniciada.")
        return

    logging.info("Iniciando thread de solicitação de exames (bidirecional)...")
    logging.info(f"Polling a cada {POLL_ORDERS_INTERVAL}s")

    # URL para polling de ordens (assume GET no mesmo endpoint com query params)
    POLL_URL = (f"https://apoio.internal.vidaexame.com/api/integration/pkl-125"
                f"?franchise_credential_id={FRANCHISE_CREDENTIAL_ID}")

    while True:
        try:
            # Aguardar a porta serial estar disponível
            if not ser or not ser.is_open:
                logging.debug("[Poll] Porta serial não disponível — aguardando...")
                time.sleep(POLL_ORDERS_INTERVAL)
                continue

            # Consultar API VIDA por ordens pendentes
            logging.debug("[Poll] Consultando API VIDA por ordens pendentes...")
            try:
                response = requests.get(POLL_URL, timeout=15)
            except requests.exceptions.RequestException as e:
                logging.warning(f"[Poll] Erro ao consultar API VIDA: {type(e).__name__}: {e}")
                time.sleep(POLL_ORDERS_INTERVAL)
                continue

            if response.status_code == 200:
                try:
                    data = response.json()
                except json.JSONDecodeError:
                    logging.warning("[Poll] Resposta da API não é JSON válido")
                    time.sleep(POLL_ORDERS_INTERVAL)
                    continue

                # Estrutura esperada: {"message": "...", "data": [...]}
                # Cada item: {"tag_id", "patient_name", "birth_date", "gender", "exams": [...]}
                orders = data.get("data", [])
                if not orders:
                    logging.debug("[Poll] Nenhuma ordem pendente")
                else:
                    logging.info(f"[Poll] {len(orders)} ordem(ns) pendente(s) encontrada(s)")

                    for order in orders:
                        tag_id = order.get("tag_id", "")
                        if not tag_id:
                            logging.warning("[Poll] Ordem sem tag_id — ignorando")
                            continue

                        logging.info(f"[Poll→PKL] Solicitando exames para tag_id: {tag_id}")

                        # Construir mensagem ASTM
                        astm_message = build_astm_order_message(tag_id, order)
                        logging.debug(f"[Poll→PKL] Mensagem ASTM gerada ({len(astm_message)} chars)")

                        # Enviar via serial
                        success = send_astm_message_via_serial(ser, astm_message)
                        if success:
                            logging.info(f"[Poll→PKL] ✓ Ordem enviada com sucesso para tag_id: {tag_id}")
                        else:
                            logging.error(f"[Poll→PKL] ✗ Falha ao enviar ordem para tag_id: {tag_id}")

            elif response.status_code == 404:
                logging.warning(f"[Poll] Endpoint de polling não encontrado (404): {POLL_URL}")
                logging.warning("[Poll] O endpoint GET para listar ordens pode não estar implementado.")
            else:
                logging.warning(f"[Poll] API retornou status {response.status_code}: {response.text[:200]}")

        except Exception as e:
            logging.error(f"[Poll] Erro inesperado: {type(e).__name__}: {e}")

        time.sleep(POLL_ORDERS_INTERVAL)


# ═══════════════════════════════════════════════════════════
# FASE 4 + 7: LOOP SERIAL + MAIN
# ═══════════════════════════════════════════════════════════

def main():
    logging.info("=" * 60)
    logging.info("Analisador PKL 125 - Bridge ASTM E1394-97")
    logging.info(f"Data/Hora de início: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Porta Serial: {COM_PORT} | Baud Rate: {BAUD_RATE}")
    logging.info(f"Webhook: {WEBHOOK_URL}")
    logging.info(f"Bidirecional: {'HABILITADO' if ENABLE_BIDIRECTIONAL else 'DESABILITADO'}")
    logging.info(f"Pastas: gerados={GERADOS_DIR} | enviados={ENVIADOS_DIR} | não enviados={REQUISICOES_NAO_ENVIADAS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
    logging.info("=" * 60)

    # Iniciar thread de envio ao webhook
    thread_envio = Thread(target=task_sender_to_webhook, daemon=True)
    thread_envio.start()
    logging.info("Thread de envio iniciada.")

    # Configurar e iniciar thread de limpeza de arquivos
    cleanup_config = FileCleanupConfig()
    cleanup_config.log_file_path = LOG_FILE
    cleanup_config.cleanup_directories = [ENVIADOS_DIR, REQUISICOES_NAO_ENVIADAS_DIR]
    start_cleanup_thread(cleanup_config)

    # Abrir porta serial
    ser = None
    tentativas_porta = 0
    MAX_TENTATIVAS_PORTA = 5

    while ser is None and tentativas_porta < MAX_TENTATIVAS_PORTA:
        try:
            ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
            logging.info(f"✓ Conectado à porta {COM_PORT} com sucesso.")
        except serial.SerialException as e:
            tentativas_porta += 1
            logging.error(f"✗ Tentativa {tentativas_porta}/{MAX_TENTATIVAS_PORTA}: "
                          f"Falha ao abrir porta serial {COM_PORT}: {e}")
            if tentativas_porta < MAX_TENTATIVAS_PORTA:
                logging.info("  Nova tentativa em 10 segundos...")
                time.sleep(10)
        except Exception as e:
            logging.critical(f"✗ Erro inesperado ao abrir porta serial: {type(e).__name__}: {e}")
            return

    if ser is None:
        logging.critical(f"✗ NÃO FOI POSSÍVEL CONECTAR à porta {COM_PORT} após {MAX_TENTATIVAS_PORTA} tentativas.")
        logging.critical("  Verifique: (1) Cabo USB conectado? (2) Porta COM correta? (3) Driver instalado?")
        logging.critical("  O serviço NÃO será iniciado. Corrija o problema e reinicie.")
        return

    # Iniciar thread de solicitação de exames (bidirecional)
    if ENABLE_BIDIRECTIONAL:
        thread_requester = Thread(target=task_exam_requester, args=(ser,), daemon=True)
        thread_requester.start()
        logging.info("Thread de solicitação de exames iniciada.")

    # Buffer para acumular dados da serial
    buffer = ""
    bytes_recebidos = 0
    frames_processados = 0
    ultimo_log_status = time.time()
    INTERVALO_LOG_STATUS = 300  # log de status a cada 5 minutos
    _debug_bytes_log_interval = 0  # contador para limitar logs de bytes brutos

    logging.info("Escutando dados da porta serial (protocolo ASTM)...")

    while True:
        try:
            # Ler dados disponíveis na serial
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                bytes_recebidos += len(data)
                decoded = data.decode('ascii', errors='ignore')
                buffer += decoded

                # Log dos bytes brutos recebidos (limitado para não poluir)
                _debug_bytes_log_interval += 1
                if _debug_bytes_log_interval <= 10 or _debug_bytes_log_interval % 50 == 0:
                    hex_repr = ' '.join(f'{b:02X}' for b in data[:100])
                    logging.info(f"[SERIAL] Recebidos {len(data)} bytes | Hex: {hex_repr}")
                    logging.info(f"[SERIAL] Decodificado: {repr(decoded[:200])}")
                    logging.info(f"[SERIAL] Buffer total: {len(buffer)} chars")

            # PRIMEIRO: Processar caracteres de controle (ENQ, EOT, ACK, NAK)
            # antes de processar frames, pois o protocolo ASTM exige resposta imediata ao ENQ
            # Remover caracteres NUL (0x00) que o equipamento pode enviar
            buffer_clean = buffer.replace(chr(0x00), '')
            if buffer_clean != buffer:
                logging.debug(f"[SERIAL] Removidos {len(buffer) - len(buffer_clean)} bytes NUL do buffer")
                buffer = buffer_clean

            # ENQ: equipamento solicita permissão para enviar → responder ACK imediatamente
            while ENQ in buffer:
                enq_idx = buffer.find(ENQ)
                buffer = buffer[:enq_idx] + buffer[enq_idx + 1:]
                try:
                    ser.write(ACK.encode('ascii'))
                    ser.flush()
                    logging.info("[ASTM] ENQ detectado → ACK enviado (equipamento solicitou envio)")
                except serial.SerialException as e:
                    logging.error(f"Erro ao responder ENQ: {e}")

            # EOT: equipamento finaliza transmissão → finalizar sessão e responder queries
            while EOT in buffer:
                eot_idx = buffer.find(EOT)
                buffer = buffer[:eot_idx] + buffer[eot_idx + 1:]
                logging.info("[ASTM] EOT detectado — finalizando sessão")
                finalize_session()

                # Se houver queries pendentes, responder ao equipamento
                if _pending_queries and ser and ser.is_open:
                    queries_to_respond = list(_pending_queries)
                    _pending_queries.clear()
                    for q_specimen_id in queries_to_respond:
                        try:
                            respond_to_query(ser, q_specimen_id)
                        except Exception as e:
                            logging.error(f"[Query→PKL] Erro ao responder query para {q_specimen_id}: {e}")

            # ACK: resposta do equipamento (pode vir em modo bidirecional)
            while ACK in buffer:
                ack_idx = buffer.find(ACK)
                buffer = buffer[:ack_idx] + buffer[ack_idx + 1:]
                logging.debug("[ASTM] ACK recebido do equipamento")

            # NAK: equipamento rejeitou frame
            while NAK in buffer:
                nak_idx = buffer.find(NAK)
                buffer = buffer[:nak_idx] + buffer[nak_idx + 1:]
                logging.warning("[ASTM] NAK recebido do equipamento — frame rejeitado")

            # DEPOIS: Processar frames ASTM completos no buffer
            # Formato ASTM E1394-97: STX + FN(1) + content + ETX + checksum(2) + CR + LF
            # Alguns equipamentos enviam apenas CR (sem LF) — tratamos ambos
            while True:
                stx_idx = buffer.find(STX)
                if stx_idx == -1:
                    break

                etx_idx = buffer.find(ETX, stx_idx + 1)
                if etx_idx == -1:
                    break

                # Após ETX: checksum(2 chars hex) + terminador (CR+LF ou apenas CR ou apenas LF)
                # Mínimo após ETX: checksum(2) + CR(1) = 3 bytes
                min_after_etx = 3  # checksum(2) + pelo menos CR ou LF
                if len(buffer) < etx_idx + 1 + min_after_etx:
                    # Buffer ainda não tem bytes suficientes após ETX — aguardar mais dados
                    break

                # Determinar o fim do frame: ETX + checksum(2) + terminador
                checksum_start = etx_idx + 1
                checksum_str = buffer[checksum_start:checksum_start + 2]

                # Procurar o terminador após o checksum
                # Pode ser CR+LF, CR, ou LF
                frame_end = checksum_start + 2  # após checksum
                if frame_end < len(buffer) and buffer[frame_end] == CR:
                    frame_end += 1  # consome CR
                    if frame_end < len(buffer) and buffer[frame_end] == LF:
                        frame_end += 1  # consome LF também
                elif frame_end < len(buffer) and buffer[frame_end] == LF:
                    frame_end += 1  # consome LF
                # Se não houver terminador, ainda assim processa (alguns equipamentos não enviam)

                # Extrair frame completo
                frame_str = buffer[stx_idx:frame_end]
                buffer = buffer[frame_end:]

                # Parse do frame
                parsed = parse_astm_frame(frame_str)
                if parsed is None:
                    logging.warning(f"Frame ASTM inválido ignorado: {repr(frame_str[:80])}")
                    continue

                logging.info(f"[ASTM] Frame recebido | Tipo: {parsed['type']} | Seq: {parsed['seq']} | "
                             f"Checksum: {'OK' if parsed['checksum_valid'] else 'INVÁLIDO'} "
                             f"(recebido={parsed['checksum_received']}, calculado={parsed['checksum_calculated']}) | "
                             f"Conteúdo: {parsed['content'][:80]}")

                if not parsed["checksum_valid"]:
                    logging.warning(f"Checksum inválido no frame | Recebido: {parsed['checksum_received']} | "
                                    f"Calculado: {parsed['checksum_calculated']} | Tipo: {parsed['type']}")
                    # Enviar NAK
                    try:
                        ser.write(NAK.encode('ascii'))
                        ser.flush()
                        logging.debug("NAK enviado")
                    except serial.SerialException:
                        pass
                    continue

                # Enviar ACK
                try:
                    ser.write(ACK.encode('ascii'))
                    ser.flush()
                except serial.SerialException as e:
                    logging.error(f"Erro ao enviar ACK: {e}")

                # Parse do registro e processamento na sessão
                record = parse_astm_record(parsed["fields"], parsed["type"])
                process_astm_record(record)

                frames_processados += 1
                logging.debug(f"Frame ASTM processado | Tipo: {parsed['type']} | "
                              f"Seq: {parsed['seq']} | Checksum: OK")

            # Log de diagnóstico: se o buffer tem dados mas não formou frame
            if buffer and len(buffer) > 0:
                # Mostrar conteúdo do buffer para diagnóstico (limitado)
                buffer_preview = repr(buffer[:150])
                logging.debug(f"[SERIAL] Buffer residual ({len(buffer)} chars): {buffer_preview}")

                # Se o buffer tem dados há muito tempo sem processar, alertar
                # (pode ser que o equipamento envie formato diferente do esperado)
                if len(buffer) > 200:
                    logging.warning(f"[SERIAL] Buffer acumulou {len(buffer)} chars sem formar frame ASTM válido!")
                    logging.warning(f"[SERIAL] Conteúdo (hex): {' '.join(f'{ord(c):02X}' for c in buffer[:100])}")
                    logging.warning("[SERIAL] Possível incompatibilidade de protocolo — limpando buffer")
                    buffer = ""

            # Log de status periódico (a cada 5 min)
            agora = time.time()
            if agora - ultimo_log_status >= INTERVALO_LOG_STATUS:
                logging.info(f"[STATUS] Uptime: {int(agora - ultimo_log_status)}s | "
                             f"Frames processados: {frames_processados} | "
                             f"Bytes recebidos: {bytes_recebidos} | "
                             f"Buffer atual: {len(buffer)} bytes | "
                             f"Porta aberta: {ser.is_open if ser else 'N/A'}")
                ultimo_log_status = agora

            time.sleep(READ_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Finalizando serviço (KeyboardInterrupt)...")
            break
        except serial.SerialException as e:
            logging.error(f"✗ Erro na porta serial: {e}")
            logging.info("  Tentando reconectar em 5 segundos...")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(5)
            try:
                ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
                logging.info("  Porta serial reconectada com sucesso.")
            except Exception as recon_err:
                logging.error(f"  Falha ao reconectar: {recon_err}")
                time.sleep(10)
        except Exception as e:
            logging.exception(f"✗ Erro inesperado no loop serial: {type(e).__name__}: {e}")
            time.sleep(1)

    if ser and ser.is_open:
        ser.close()
        logging.info("Porta serial fechada.")
    logging.info(f"Serviço finalizado. Total de frames processados: {frames_processados}")


if __name__ == "__main__":
    main()
