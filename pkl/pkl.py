"""
Analisador PKL 125 — Bridge ASTM E1394-97 bidirecional.

Herda de BaseAnalisador com SerialListener.
Implementa protocolo ASTM completo com handshake ENQ/ACK,
gerenciamento de sessão e modo bidirecional (polling de ordens).
"""

import os
import sys
import json
import time
import logging
import datetime
import threading
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Tuple

import serial
import requests

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador

# ═══════════════════════════════════════════════════════════
# CONSTANTES ASTM E1394-97
# ═══════════════════════════════════════════════════════════

STX = chr(0x02)
ETX = chr(0x03)
ENQ = chr(0x05)
ACK = chr(0x06)
NAK = chr(0x15)
EOT = chr(0x04)
CR  = chr(0x0D)
LF  = chr(0x0A)

# ═══════════════════════════════════════════════════════════
# CONFIGURAÇÕES BIDIRECIONAIS
# ═══════════════════════════════════════════════════════════

ENABLE_BIDIRECTIONAL = True
POLL_ORDERS_INTERVAL = 30  # segundos
ORDERS_API_URL = (
    "https://apoio.internal.vidaexame.com/api/integration/pkl-125/orders"
    "?franchise_credential_id={franchise_credential_id}"
)


# ═══════════════════════════════════════════════════════════
# ESTADO DA SESSÃO ASTM
# ═══════════════════════════════════════════════════════════

_session_lock = Lock()
_pending_batches: Dict[str, dict] = {}
_current_tag_id: Optional[str] = None
_session_patient: Optional[dict] = None
_pending_queries: List[str] = []


# ═══════════════════════════════════════════════════════════
# PARSER ASTM
# ═══════════════════════════════════════════════════════════

def calculate_checksum(data: str) -> str:
    total = sum(ord(c) for c in data) % 256
    return f"{total:02X}"


def parse_astm_frame(frame: str) -> Optional[dict]:
    try:
        if not frame or len(frame) < 6:
            return None
        stx_idx = frame.find(STX)
        if stx_idx == -1:
            return None
        etx_idx = frame.find(ETX, stx_idx + 1)
        if etx_idx == -1 or etx_idx <= stx_idx + 2:
            return None

        frame_number = ord(frame[stx_idx + 1]) - 48
        content = frame[stx_idx + 2:etx_idx]
        record_type = content[0] if content else ""

        checksum_received = ""
        if etx_idx + 2 < len(frame):
            checksum_received = frame[etx_idx + 1:etx_idx + 3]

        data_for_checksum = frame[stx_idx + 1:etx_idx + 1]
        checksum_calculated = calculate_checksum(data_for_checksum)
        checksum_valid = checksum_received.upper() == checksum_calculated.upper()

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


def parse_astm_record(fields: List[str], record_type: str) -> dict:
    record = {"type": record_type, "raw_fields": fields}
    try:
        if record_type == "H":
            record["sender_id"] = fields[4] if len(fields) > 4 else ""
            record["receiver_id"] = fields[9] if len(fields) > 9 else ""
            record["timestamp"] = fields[13] if len(fields) > 13 else ""
            record["version"] = fields[12] if len(fields) > 12 else ""
        elif record_type == "P":
            record["patient_id"] = fields[2] if len(fields) > 2 else ""
            name_parts = (fields[5] if len(fields) > 5 else "").split('^')
            record["last_name"] = name_parts[0] if len(name_parts) > 0 else ""
            record["first_name"] = name_parts[1] if len(name_parts) > 1 else ""
            record["birth_date"] = fields[7] if len(fields) > 7 else ""
            record["gender"] = fields[8] if len(fields) > 8 else ""
        elif record_type == "O":
            specimen_raw = fields[2] if len(fields) > 2 else ""
            record["specimen_id"] = specimen_raw.split('^')[0] if specimen_raw else ""
            record["universal_test_id"] = fields[4] if len(fields) > 4 else ""
            record["priority"] = fields[5] if len(fields) > 5 else ""
            record["action_code"] = fields[11] if len(fields) > 11 else ""
            record["sample_type"] = fields[15] if len(fields) > 15 else ""
        elif record_type == "Q":
            specimen_raw = fields[3] if len(fields) > 3 else ""
            record["specimen_id"] = specimen_raw.lstrip('^') if specimen_raw else ""
            record["query_type"] = fields[4] if len(fields) > 4 else ""
            record["action_code"] = fields[12] if len(fields) > 12 else ""
        elif record_type == "R":
            record["universal_test_id"] = fields[3] if len(fields) > 3 else ""
            record["test_name"] = fields[4] if len(fields) > 4 else ""
            record["value"] = fields[5] if len(fields) > 5 else ""
            record["units"] = fields[6] if len(fields) > 6 else ""
            record["reference_range"] = fields[7] if len(fields) > 7 else ""
            record["abnormal_flag"] = fields[8] if len(fields) > 8 else ""
            record["status"] = fields[9] if len(fields) > 9 else ""
            record["instrument_id"] = fields[12] if len(fields) > 12 else ""
        elif record_type == "L":
            record["termination_code"] = fields[2] if len(fields) > 2 else "N"
    except Exception as e:
        logging.error(f"Erro ao parse registro ASTM tipo '{record_type}': {e}")
    return record


def parse_universal_test_id(universal_test_id: str, test_name: str = "") -> Tuple[str, str]:
    exam_code = "HEMO"
    test = ""
    if universal_test_id:
        cleaned = universal_test_id.lstrip('^')
        parts = [p for p in cleaned.split('^') if p]
        if len(parts) >= 2:
            exam_code, test = parts[0], parts[1]
        elif len(parts) == 1:
            test = parts[0]
    elif test_name:
        test = test_name
    return exam_code, test


# ═══════════════════════════════════════════════════════════
# GERENCIAMENTO DE SESSÃO ASTM
# ═══════════════════════════════════════════════════════════

def process_astm_record(record: dict, gerados_dir: str) -> None:
    global _current_tag_id, _session_patient

    record_type = record.get("type", "")
    try:
        if record_type == "H":
            logging.info(
                f"[ASTM] Header recebido | Sender: {record.get('sender_id', '?')} | "
                f"Receiver: {record.get('receiver_id', '?')}"
            )
        elif record_type == "P":
            _session_patient = {
                "patient_id": record.get("patient_id", ""),
                "first_name": record.get("first_name", ""),
                "last_name": record.get("last_name", ""),
                "birth_date": record.get("birth_date", ""),
                "gender": record.get("gender", ""),
            }
            logging.info(
                f"[ASTM] Patient recebido | ID: {_session_patient['patient_id']} | "
                f"Nome: {_session_patient['first_name']} {_session_patient['last_name']}"
            )
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
            logging.info(
                f"[ASTM] Order recebido | tag_id: {tag_id} | "
                f"Priority: {record.get('priority', '?')}"
            )
        elif record_type == "Q":
            specimen_id = record.get("specimen_id", "")
            logging.info(
                f"[ASTM] Query recebido | specimen_id: {specimen_id} | "
                f"query_type: {record.get('query_type', '')}"
            )
            if specimen_id:
                _pending_queries.append(specimen_id)
        elif record_type == "R":
            universal_test_id = record.get("universal_test_id", "")
            test_name = record.get("test_name", "")
            value = record.get("value", "")
            units = record.get("units", "")
            abnormal_flag = record.get("abnormal_flag", "")

            exam_code, test = parse_universal_test_id(universal_test_id, test_name)
            tag_id = record.get("instrument_id", "") or _current_tag_id

            if not tag_id:
                logging.warning(
                    f"[ASTM] Result sem tag_id — ignorando | Test: {test_name}"
                )
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

            logging.debug(
                f"[ASTM] Result adicionado | tag_id: {tag_id} | "
                f"ExamCode: {exam_code} | Test: {test} | Value: {value}{units}"
            )
        elif record_type == "L":
            logging.info(
                f"[ASTM] Terminator recebido | Code: {record.get('termination_code', 'N')}"
            )
            finalize_session(gerados_dir)
    except Exception as e:
        logging.error(f"Erro ao processar registro ASTM tipo '{record_type}': {e}")


def finalize_session(gerados_dir: str) -> None:
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

        payload = {
            "franchise_credential_id": "",  # será preenchido pelo task_sender
            "tag_id": tag_id,
            "results": batch["results"],
        }

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"exame_{timestamp}_{tag_id}.astm"
        file_path = os.path.join(gerados_dir, filename)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logging.info(
                f"[ASTM→VIDA] Batch salvo: {filename} | tag_id: {tag_id} | "
                f"Resultados: {len(batch['results'])}"
            )
        except OSError as e:
            logging.error(f"[ASTM→VIDA] Erro ao salvar batch {filename}: {e}")

    _current_tag_id = None
    _session_patient = None


# ═══════════════════════════════════════════════════════════
# BIDIRECIONAL: CONSULTA DE ORDENS
# ═══════════════════════════════════════════════════════════

def build_astm_frame(frame_number: int, content: str) -> bytes:
    data_for_checksum = chr(frame_number + 48) + content + ETX
    checksum = calculate_checksum(data_for_checksum)
    return (STX + chr(frame_number + 48) + content + ETX + checksum + CR + LF).encode("ascii")


def build_astm_order_message(
    specimen_id: str, tests: List[dict], patient: dict
) -> bytes:
    frames = bytearray()
    fn = 1

    # Header
    dt = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    hdr = f"H|\\^&|||LIS|||||PKL125||P|{dt}"
    frames.extend(build_astm_frame(fn, hdr))
    fn += 1

    # Patient
    pid = patient.get("patient_id", specimen_id)
    name = f"{patient.get('last_name', '')}^{patient.get('first_name', '')}"
    pat = f"P|1|{pid}||{name}||{patient.get('birth_date', '')}|{patient.get('gender', '')}"
    frames.extend(build_astm_frame(fn, pat))
    fn += 1

    # Order
    ordr = f"O|1|{specimen_id}^^^^SERUM||^^^{tests[0]['exam_code']}^{tests[0]['test']}|R||||||N||||SERUM"
    frames.extend(build_astm_frame(fn, ordr))
    fn += 1

    # Results (um frame por teste)
    for t in tests:
        res = f"R|1|^^^{t['exam_code']}^{t['test']}|{t['test']}|{t.get('value', '')}|||||F"
        frames.extend(build_astm_frame(fn, res))
        fn += 1

    # Terminator
    frames.extend(build_astm_frame(fn, "L|1|N"))
    return bytes(frames)


def send_astm_message_via_serial(ser: serial.Serial, message: bytes) -> bool:
    try:
        # ENQ
        ser.write(ENQ.encode("ascii"))
        resp = _wait_for_byte(ser, [ACK, NAK], timeout=3)
        if resp != ACK:
            logging.warning(f"[ASTM→EQP] Handshake ENQ falhou: recebido {repr(resp)}")
            return False

        # Envia frames
        ser.write(message)

        # Aguarda EOT
        resp = _wait_for_byte(ser, [EOT], timeout=5)
        if resp == EOT:
            logging.info("[ASTM→EQP] Mensagem enviada com sucesso (EOT recebido)")
            return True
        else:
            logging.warning(f"[ASTM→EQP] EOT não recebido: {repr(resp)}")
            return False
    except Exception as e:
        logging.error(f"[ASTM→EQP] Erro ao enviar mensagem: {e}")
        return False


def _wait_for_byte(ser: serial.Serial, expected: List[str], timeout: float = 3) -> Optional[str]:
    start = time.time()
    while time.time() - start < timeout:
        if ser.in_waiting > 0:
            byte = ser.read(1).decode("ascii", errors="replace")
            if byte in expected:
                return byte
        time.sleep(0.05)
    return None


def respond_to_query(ser: serial.Serial, specimen_id: str, franchise_id: str) -> None:
    try:
        url = ORDERS_API_URL.format(franchise_credential_id=franchise_id)
        resp = requests.get(url, params={"specimen_id": specimen_id}, timeout=10)
        if resp.status_code != 200:
            logging.warning(f"[BIDIREC] API de ordens retornou {resp.status_code}")
            return

        data = resp.json()
        tests = data.get("tests", [])
        patient = data.get("patient", {})

        if not tests:
            logging.info(f"[BIDIREC] Nenhuma ordem pendente para {specimen_id}")
            return

        logging.info(
            f"[BIDIREC] {len(tests)} ordem(ns) encontrada(s) para {specimen_id}"
        )
        msg = build_astm_order_message(specimen_id, tests, patient)
        send_astm_message_via_serial(ser, msg)
    except Exception as e:
        logging.error(f"[BIDIREC] Erro ao consultar ordens: {e}")


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorPKL125(BaseAnalisador):
    """Analisador para equipamento PKL 125 via ASTM E1394-97 bidirecional."""

    def get_file_extension(self) -> str:
        return '.astm'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta frame ASTM completo: STX ... ETX + checksum(2) + CR + LF.
        Retorna (frame, buffer_restante) ou (None, buffer).
        """
        while STX in buffer and ETX in buffer:
            stx_idx = buffer.index(STX)
            etx_idx = buffer.index(ETX, stx_idx)
            # Frame mínimo: STX + frame_number + ETX + checksum(2) + CR + LF = 7 chars
            frame_end = etx_idx + 5  # ETX + 2 checksum + CR + LF
            if frame_end <= len(buffer):
                frame = buffer[stx_idx:frame_end]
                buffer = buffer[frame_end:]
                if len(frame) >= 7:
                    return frame, buffer
                return None, buffer
            else:
                return None, buffer  # frame incompleto
        return None, buffer

    def _on_serial_message(self, raw_message: str) -> None:
        """
        Sobrescreve: faz parse do frame ASTM e gerencia sessão.
        Não salva raw message — o salvamento é feito em finalize_session().
        """
        frame = parse_astm_frame(raw_message)
        if frame is None:
            logging.warning(f"[ASTM] Frame inválido recebido: {raw_message[:100]!r}")
            return

        if not frame.get("checksum_valid", False):
            logging.warning(
                f"[ASTM] Checksum inválido | Recebido: {frame.get('checksum_received')} | "
                f"Calculado: {frame.get('checksum_calculated')}"
            )

        record = parse_astm_record(frame["fields"], frame["type"])
        process_astm_record(record, self.GERADOS_DIR)

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa arquivo .astm (JSON): lê e retorna o payload diretamente.
        O arquivo .astm já é um JSON pronto para envio.
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except PermissionError:
            logging.error(f"✗ Permissão negada ao ler: {nome_arquivo}")
            return None
        except FileNotFoundError:
            logging.warning(f"Arquivo {nome_arquivo} não encontrado.")
            return None
        except json.JSONDecodeError as e:
            logging.error(f"✗ JSON inválido em {nome_arquivo}: {e}")
            return None
        except Exception as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        if not payload or not payload.get("tag_id"):
            logging.warning(f"Arquivo {nome_arquivo} sem tag_id — ignorando.")
            return None

        # Injeta franchise_credential_id
        payload["franchise_credential_id"] = self.FRANCHISE_CREDENTIAL_ID

        # Salva TXT debug
        try:
            pasta_txt = os.path.join(self.ENVIADOS_DIR, "txt")
            os.makedirs(pasta_txt, exist_ok=True)
            nome_txt = os.path.splitext(nome_arquivo)[0] + ".txt"
            with open(os.path.join(pasta_txt, nome_txt), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logging.info(f"TXT salvo: {nome_txt}")
        except Exception as e:
            logging.warning(f"Erro ao salvar TXT: {e}")

        return [payload]

    def start(self) -> None:
        """Inicia o analisador com suporte bidirecional."""
        self._banner()

        # Thread de envio
        thread_envio = Thread(target=self._task_sender_to_webhook, daemon=True)
        thread_envio.start()
        logging.info("Thread de envio iniciada.")

        # Health server
        self._start_health_server()

        # SerialListener
        self._start_with_serial_listener()

        # Bidirecional: thread de polling de ordens
        if ENABLE_BIDIRECTIONAL and self._listener:
            thread_requester = Thread(
                target=self._exam_requester_loop, daemon=True
            )
            thread_requester.start()
            logging.info("Thread de solicitação de exames iniciada.")

        logging.info(f"=== {self.machine_name} Service (Python) rodando ===")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Interrompido pelo usuário. Encerrando...")
            if self._listener:
                self._listener.stop_listening()
            if self._health:
                self._health.stop()

    def _exam_requester_loop(self) -> None:
        """Thread que consulta a API por ordens pendentes e envia ao equipamento."""
        logging.info(
            f"[BIDIREC] Thread de polling iniciada. "
            f"Intervalo: {POLL_ORDERS_INTERVAL}s"
        )
        while True:
            try:
                # Processa queries pendentes da sessão ASTM
                queries_to_process = []
                with _session_lock:
                    while _pending_queries:
                        queries_to_process.append(_pending_queries.pop(0))

                for specimen_id in queries_to_process:
                    if self._listener and self._listener.is_port_open:
                        respond_to_query(
                            self._listener._serial_port,
                            specimen_id,
                            self.FRANCHISE_CREDENTIAL_ID,
                        )

                self._update_health_stats()
            except Exception as e:
                logging.error(f"[BIDIREC] Erro no loop de polling: {e}")

            time.sleep(POLL_ORDERS_INTERVAL)


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorPKL125(
        machine_id='pkl',
        machine_name='PKL125',
        config_defaults={
            'com_port': 'COM3',
            'baud_rate': 19200,
            'franchise_credential_id': '4e768395-1b2c-4d3e-8f9a-5c6d7e8f9a0b',
            'webhook_url': (
                'https://apoio.internal.vidaexame.com/api/integration/pkl-125'
                '?franchise_credential_id={franchise_credential_id}'
            ),
        },
        health_port=8083,
        use_serial_listener=True,
    ).start()
