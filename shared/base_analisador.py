"""
Classe base para todos os analisadores HL7.

Centraliza:
- Configuração de pastas, logging, cleanup, stdout redirect
- Thread de envio ao webhook (task_sender_to_webhook)
- Envio HTTP com retry (_enviar_payload_webhook)
- Servidor HTTP de health check
- SerialListener com keepalive (opcional)
- Loop serial simples (opcional)
- Utilitários compartilhados (format_thousands, extrair_imagens_de_hl7)

Cada máquina herda e implementa APENAS os hooks específicos do protocolo:
- detect_complete_message()
- process_file()
- generate_ack()
- get_file_extension()
"""

import os
import sys
import re
import json
import time
import shutil
import base64
import logging
import datetime
import threading
from threading import Thread
from typing import Any, Callable, Dict, List, Optional, Tuple

import serial
import requests

# Import dos módulos compartilhados
from shared.file_cleanup import FileCleanupConfig, start_cleanup_thread
from shared.config_loader import load_config
from shared.health_server import HealthServer


# ═══════════════════════════════════════════════════════════════
# CONSTANTES PADRÃO
# ═══════════════════════════════════════════════════════════════

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
MAX_RETRY = 5
RETRY_INTERVAL = 60
MAX_ERROS_CONSECUTIVOS = 10
MAX_TENTATIVAS_PORTA = 5
DEFAULT_HEALTH_PORT = 8080

# Constantes para tratamento de erros ClearCommError na porta serial
MAX_COMM_ERRORS = 5          # Quantos erros ClearCommError antes de forçar reabertura
COMM_ERROR_RESET_INTERVAL = 60  # Segundos sem erro para resetar o contador


# ═══════════════════════════════════════════════════════════════
# UTILITÁRIOS COMPARTILHADOS
# ═══════════════════════════════════════════════════════════════

def format_thousands(value_str: str) -> str:
    """
    Formata valor numérico com separador de milhar.
    Ex: '5800' → '5.800', '224000' → '224.000', '1600L' → '1.600L'
    """
    if not value_str or not value_str.strip():
        return value_str

    trimmed = value_str.strip()
    suffix = ""
    while trimmed and (trimmed[-1].isalpha() or trimmed[-1] in ('*', '?')):
        suffix = trimmed[-1] + suffix
        trimmed = trimmed[:-1]

    has_asterisk = trimmed.startswith("*")
    if has_asterisk:
        trimmed = trimmed[1:]

    try:
        num = float(trimmed)
    except (ValueError, TypeError):
        return value_str

    if num == int(num):
        formatted = f"{int(num):,}".replace(",", ".")
    else:
        formatted = f"{num:,}".replace(",", ".")

    if has_asterisk:
        formatted = "*" + formatted
    return formatted + suffix


def extrair_imagens_de_hl7(conteudo_hl7: str, pasta_destino: str, prefixo: str = "") -> int:
    """
    Extrai imagens Base64 de segmentos OBX com tipo ED (Encapsulated Data)
    de uma mensagem HL7. Retorna o número de imagens extraídas.

    Comum a BH5100 e VIDAS1600.
    """
    if not conteudo_hl7 or not pasta_destino:
        return 0

    os.makedirs(pasta_destino, exist_ok=True)
    qtd_imagens = 0

    clean = conteudo_hl7.replace('\r\n', '\n').replace('\r', '\n')
    segments = clean.split('\n')

    for seg in segments:
        fields = seg.split('|')
        if fields[0] != 'OBX' or len(fields) < 6:
            continue
        if fields[2] != 'ED':
            continue

        obx5 = fields[5] if len(fields) > 5 else ""
        if not obx5:
            continue

        subfields = obx5.split('^')
        if len(subfields) < 4:
            continue

        b64_data = subfields[3]
        if not b64_data:
            continue

        try:
            img_bytes = base64.b64decode(b64_data)
        except Exception as e:
            logging.warning(f"Erro ao decodificar Base64: {e}")
            continue

        ext = ".png"
        if len(subfields) > 2 and subfields[2]:
            ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/bmp": ".bmp"}
            ext = ext_map.get(subfields[2].lower(), ".png")

        nome_arquivo = f"{prefixo}obx_{qtd_imagens + 1}{ext}"
        caminho_completo = os.path.join(pasta_destino, nome_arquivo)

        try:
            with open(caminho_completo, 'wb') as f:
                f.write(img_bytes)
            qtd_imagens += 1
        except OSError as e:
            logging.warning(f"Erro ao salvar imagem {nome_arquivo}: {e}")

    return qtd_imagens


# ═══════════════════════════════════════════════════════════════
# SERIAL LISTENER (com keepalive opcional)
# ═══════════════════════════════════════════════════════════════

class SerialListener:
    """
    Listener de porta serial com suporte opcional a keepalive.

    Usado por MEK7300 e PKL. Máquinas mais simples (BH5100, Coagmaster,
    VIDAS1600) usam loop serial inline no BaseAnalisador.
    """

    def __init__(
        self,
        port_name: str,
        baud_rate: int,
        detect_complete_message: Callable[[str], Tuple[Optional[str], str]],
        on_message: Callable[[str], None],
        enable_keepalive: bool = False,
        keepalive_interval: int = 90,
    ) -> None:
        self.port_name = port_name
        self.baud_rate = baud_rate
        self._detect_complete_message = detect_complete_message
        self._on_message = on_message
        self._enable_keepalive = enable_keepalive
        self._keepalive_interval = keepalive_interval

        self._serial_port: Optional[serial.Serial] = None
        self._thread: Optional[Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._buffer = ""

        # Métricas
        self.bytes_recebidos = 0
        self.mensagens_processadas = 0
        self.last_activity = 0.0

    @property
    def is_port_open(self) -> bool:
        return self._serial_port is not None and self._serial_port.is_open

    def open_port(self) -> bool:
        if self.is_port_open:
            return True
        try:
            self._serial_port = serial.Serial(self.port_name, self.baud_rate, timeout=0.1)
            logging.info(f"✓ Conectado à porta {self.port_name} com sucesso.")
            self.last_activity = time.time()
            return True
        except serial.SerialException as e:
            logging.error(f"✗ Falha ao abrir porta serial {self.port_name}: {e}")
            return False
        except Exception as e:
            logging.critical(f"✗ Erro inesperado ao abrir porta serial: {type(e).__name__}: {e}")
            return False

    def start_listening(self) -> None:
        self._running = True
        self._thread = Thread(target=self._read_loop, daemon=True, name="serial-listener")
        self._thread.start()

    def stop_listening(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._serial_port and self._serial_port.is_open:
            self._serial_port.close()
            logging.info("Porta serial fechada.")

    def _read_loop(self) -> None:
        last_activity = time.time()
        comm_error_count = 0
        last_comm_error_time = 0.0

        while self._running:
            if not self.is_port_open:
                self.open_port()
                last_activity = time.time()
                comm_error_count = 0
                time.sleep(2)
                continue

            try:
                if self._serial_port.in_waiting > 0:
                    last_activity = time.time()
                    incoming = self._serial_port.read(
                        self._serial_port.in_waiting
                    ).decode("ascii", errors="replace")
                    self.bytes_recebidos += len(incoming)
                    comm_error_count = 0  # Reset: dados recebidos com sucesso

                    with self._lock:
                        self._buffer += incoming
                        message, self._buffer = self._detect_complete_message(self._buffer)
                        while message is not None:
                            self._on_message(message)
                            self.mensagens_processadas += 1
                            message, self._buffer = self._detect_complete_message(self._buffer)

                time.sleep(READ_INTERVAL)

                # Keep-alive (opcional)
                if (
                    self._enable_keepalive
                    and self.is_port_open
                    and (time.time() - last_activity) > self._keepalive_interval
                ):
                    try:
                        byte = self._serial_port.read(1)
                        if byte:
                            with self._lock:
                                self._buffer += byte.decode("ascii", errors="replace")
                            self.bytes_recebidos += 1
                            last_activity = time.time()
                    except Exception:
                        pass

            except OSError as ex:
                # ClearCommError: erro transitório do driver serial no Windows.
                # Em vez de fechar a porta imediatamente, conta erros consecutivos
                # e só força reabertura após MAX_COMM_ERRORS erros.
                now = time.time()
                if now - last_comm_error_time > COMM_ERROR_RESET_INTERVAL:
                    comm_error_count = 0
                comm_error_count += 1
                last_comm_error_time = now

                if comm_error_count < MAX_COMM_ERRORS:
                    logging.warning(
                        f"⚠ Erro transitório na porta serial ({comm_error_count}/{MAX_COMM_ERRORS}): "
                        f"{ex}. Continuando sem reabrir."
                    )
                    time.sleep(0.5)
                else:
                    logging.error(
                        f"✗ {MAX_COMM_ERRORS} erros ClearCommError consecutivos — "
                        f"forçando reabertura da porta."
                    )
                    self._buffer = ""
                    try:
                        if self._serial_port and self._serial_port.is_open:
                            self._serial_port.close()
                            logging.info("Porta serial fechada após erros consecutivos — será reaberta.")
                    except Exception as close_ex:
                        logging.error(f"Erro ao fechar porta serial: {close_ex}")
                    self._serial_port = None
                    comm_error_count = 0
                    time.sleep(2)  # Delay maior para evitar PermissionError

            except Exception as ex:
                self._buffer = ""
                logging.error(f"✗ Erro no read_loop: {ex}")
                try:
                    if self._serial_port and self._serial_port.is_open:
                        self._serial_port.close()
                        logging.info("Porta serial fechada após erro — será reaberta.")
                except Exception as close_ex:
                    logging.error(f"Erro ao fechar porta serial: {close_ex}")
                self._serial_port = None
                time.sleep(2)  # Delay maior para evitar PermissionError

        self.last_activity = last_activity


# ═══════════════════════════════════════════════════════════════
# CLASSE BASE
# ═══════════════════════════════════════════════════════════════

class BaseAnalisador:
    """
    Classe base para todos os analisadores.

    Args:
        machine_id: Identificador usado no config JSON e webhook (ex: 'bh5100').
        machine_name: Nome legível (ex: 'BH5100').
        config_defaults: Dicionário com defaults para load_config.
        health_port: Porta do servidor HTTP de health check (0 = desabilitado).
        console_logging: Se True, adiciona StreamHandler ao logging.
        redirect_stdout: Se True, redireciona stdout/stderr para devnull.
        enable_keepalive: Se True, ativa keepalive no SerialListener.
        use_serial_listener: Se True, usa SerialListener; senão, loop inline.
    """

    def __init__(
        self,
        machine_id: str,
        machine_name: str,
        config_defaults: Dict[str, Any],
        health_port: int = DEFAULT_HEALTH_PORT,
        console_logging: bool = False,
        redirect_stdout: bool = True,
        enable_keepalive: bool = False,
        use_serial_listener: bool = False,
    ) -> None:
        self.machine_id = machine_id
        self.machine_name = machine_name
        self._health_port = health_port
        self._console_logging = console_logging
        self._redirect_stdout = redirect_stdout
        self._enable_keepalive = enable_keepalive
        self._use_serial_listener = use_serial_listener

        # Configuração
        self._setup_config(config_defaults)

        # Pastas
        self._setup_dirs()

        # Redirecionar stdout/stderr
        if self._redirect_stdout:
            self._redirect_stdouterr()

        # Logging
        self._setup_logging()

        # Cleanup
        self._setup_cleanup()

        # Health server
        self._health: Optional[HealthServer] = None
        if self._health_port > 0:
            self._health = HealthServer(port=self._health_port, machine_name=self.machine_name)

        # Estado
        self._start_time = time.time()
        self._errors_consecutive = 0
        self._files_sent_today = 0
        self._last_webhook_status = -1

        # Serial listener (opcional)
        self._listener: Optional[SerialListener] = None

    # ── SETUP ──────────────────────────────────────────────────

    def _setup_config(self, defaults: Dict[str, Any]) -> None:
        _config, _config_status = load_config(self.machine_id, defaults)
        self.COM_PORT = _config["com_port"]
        self.BAUD_RATE = _config["baud_rate"]
        self.FRANCHISE_CREDENTIAL_ID = _config.get("franchise_credential_id", "")
        self.WEBHOOK_URL = _config["webhook_url"].format(
            franchise_credential_id=self.FRANCHISE_CREDENTIAL_ID
        )
        self._config_status = _config_status

    def _setup_dirs(self) -> None:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        self.BASE_DIR = os.path.join(desktop, f"Analisador{self.machine_name}")
        self.GERADOS_DIR = os.path.join(self.BASE_DIR, "gerados")
        self.ENVIADOS_DIR = os.path.join(self.BASE_DIR, "enviados")
        self.REQUISICOES_NAO_ENVIADAS_DIR = os.path.join(
            self.BASE_DIR, "requisições não enviadas"
        )
        self.LOG_FILE = os.path.join(
            self.BASE_DIR, f"analisador_{self.machine_id}.log"
        )
        self.LOGS_DIR = os.path.join(self.BASE_DIR, "logs")

        for d in [
            self.GERADOS_DIR,
            self.ENVIADOS_DIR,
            self.REQUISICOES_NAO_ENVIADAS_DIR,
            self.LOGS_DIR,
        ]:
            os.makedirs(d, exist_ok=True)

    def _redirect_stdouterr(self) -> None:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")

    def _setup_logging(self) -> None:
        handlers: List[logging.Handler] = [
            logging.FileHandler(self.LOG_FILE, encoding="utf-8")
        ]
        if self._console_logging:
            handlers.append(logging.StreamHandler())

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=handlers,
        )
        logging.info(self._config_status)

    def _setup_cleanup(self) -> None:
        self._cleanup_config = FileCleanupConfig()
        self._cleanup_config.log_file_path = self.LOG_FILE
        self._cleanup_config.base_dir = self.BASE_DIR
        self._cleanup_config.daily_rotation_dirs = [
            self.GERADOS_DIR,
            self.ENVIADOS_DIR,
            self.REQUISICOES_NAO_ENVIADAS_DIR,
        ]
        self._cleanup_config.cleanup_directories = [
            self.ENVIADOS_DIR,
            self.REQUISICOES_NAO_ENVIADAS_DIR,
        ]
        start_cleanup_thread(self._cleanup_config)

    # ── BANNER ─────────────────────────────────────────────────

    def _banner(self) -> None:
        logging.info("=" * 60)
        logging.info(
            f"Analisador {self.machine_name} - Serviço de Integração"
        )
        logging.info(
            f"Data/Hora de início: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logging.info(f"Porta Serial: {self.COM_PORT} | Baud Rate: {self.BAUD_RATE}")
        logging.info(f"Webhook: {self.WEBHOOK_URL}")
        if self.FRANCHISE_CREDENTIAL_ID:
            logging.info(f"Franchise Credential ID: {self.FRANCHISE_CREDENTIAL_ID}")
        logging.info(
            f"Pastas: gerados={self.GERADOS_DIR} | enviados={self.ENVIADOS_DIR} | "
            f"não enviados={self.REQUISICOES_NAO_ENVIADAS_DIR} | logs={self.LOGS_DIR}"
        )
        logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
        if self._health_port > 0:
            logging.info(f"Health Check: http://0.0.0.0:{self._health_port}/health")
        logging.info("=" * 60)

    # ── HEALTH CHECK ──────────────────────────────────────────

    def _start_health_server(self) -> None:
        if self._health:
            self._health.start()
            self._update_health_stats()

    def _update_health_stats(self) -> None:
        if not self._health:
            return
        self._health.update_stats(
            com_port=self.COM_PORT,
            baud_rate=self.BAUD_RATE,
            webhook_url=self.WEBHOOK_URL,
            errors_consecutive=self._errors_consecutive,
            files_sent_today=self._files_sent_today,
            last_webhook_status=self._last_webhook_status,
        )

    # ── WEBHOOK ───────────────────────────────────────────────

    def _enviar_payload_webhook(
        self, payload: Dict[str, Any], nome_arquivo: str, tag_identifier: str = ""
    ) -> bool:
        """
        Envia payload ao webhook com retry (até MAX_RETRY tentativas).

        Returns:
            True se enviado com sucesso, False caso contrário.
        """
        headers = {"Content-Type": "application/json"}

        for tentativa in range(1, MAX_RETRY + 1):
            try:
                resp = requests.post(
                    self.WEBHOOK_URL,
                    json=payload,
                    headers=headers,
                    timeout=30,
                )
                self._last_webhook_status = resp.status_code

                if resp.status_code in (200, 201):
                    logging.info(
                        f"✓ [{tentativa}/{MAX_RETRY}] {nome_arquivo} "
                        f"(tag: {tag_identifier}) enviado com sucesso. "
                        f"Status: {resp.status_code}"
                    )
                    self._files_sent_today += 1
                    return True

                elif resp.status_code == 404:
                    logging.warning(
                        f"⚠ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                        f"Webhook retornou 404 (não encontrado)."
                    )
                    if tentativa < MAX_RETRY:
                        time.sleep(RETRY_INTERVAL)

                elif resp.status_code == 400:
                    logging.error(
                        f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                        f"Erro 400 (Bad Request). Resposta: {resp.text[:300]}"
                    )
                    if tentativa < MAX_RETRY:
                        time.sleep(RETRY_INTERVAL)

                elif resp.status_code in (401, 403):
                    logging.critical(
                        f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                        f"Erro {resp.status_code} (Autenticação/Autorização). "
                        f"Verifique franchise_credential_id."
                    )
                    return False

                elif resp.status_code in (500, 502, 503):
                    logging.error(
                        f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                        f"Erro {resp.status_code} (Servidor). "
                        f"Tentando novamente em {RETRY_INTERVAL}s..."
                    )
                    if tentativa < MAX_RETRY:
                        time.sleep(RETRY_INTERVAL)

                else:
                    logging.warning(
                        f"⚠ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                        f"Status inesperado: {resp.status_code}"
                    )
                    if tentativa < MAX_RETRY:
                        time.sleep(RETRY_INTERVAL)

            except requests.ConnectionError:
                logging.error(
                    f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                    f"Erro de conexão. Servidor indisponível?"
                )
                if tentativa < MAX_RETRY:
                    time.sleep(RETRY_INTERVAL)

            except requests.Timeout:
                logging.error(
                    f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                    f"Timeout (30s). Servidor não respondeu."
                )
                if tentativa < MAX_RETRY:
                    time.sleep(RETRY_INTERVAL)

            except requests.TooManyRedirects:
                logging.error(
                    f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                    f"Muitos redirecionamentos."
                )
                return False

            except requests.RequestException as e:
                logging.error(
                    f"✗ [{tentativa}/{MAX_RETRY}] {nome_arquivo}: "
                    f"Erro na requisição: {type(e).__name__}: {e}"
                )
                if tentativa < MAX_RETRY:
                    time.sleep(RETRY_INTERVAL)

        return False

    # ── TASK SENDER (thread de envio) ─────────────────────────

    def _task_sender_to_webhook(self) -> None:
        """
        Thread que monitora GERADOS_DIR e envia arquivos ao webhook.
        Usa o hook process_file() que cada máquina implementa.
        """
        extensao = self.get_file_extension()

        while True:
            try:
                arquivos = [
                    f for f in os.listdir(self.GERADOS_DIR)
                    if f.endswith(extensao)
                ]

                if arquivos:
                    self._errors_consecutive = 0

                for nome_arquivo in arquivos:
                    caminho_origem = os.path.join(self.GERADOS_DIR, nome_arquivo)
                    caminho_destino = os.path.join(self.ENVIADOS_DIR, nome_arquivo)
                    caminho_nao_enviado = os.path.join(
                        self.REQUISICOES_NAO_ENVIADAS_DIR, nome_arquivo
                    )

                    # Processa o arquivo → lista de payloads
                    try:
                        payloads = self.process_file(caminho_origem, nome_arquivo)
                    except Exception as e:
                        logging.error(
                            f"✗ Erro ao processar {nome_arquivo}: "
                            f"{type(e).__name__}: {e}"
                        )
                        continue

                    if payloads is None:
                        try:
                            shutil.move(caminho_origem, caminho_destino)
                        except OSError:
                            pass
                        continue

                    if not payloads:
                        try:
                            shutil.move(caminho_origem, caminho_destino)
                        except OSError:
                            pass
                        continue

                    # Envia cada payload
                    all_sent = True
                    for payload in payloads:
                        tag = payload.get("FileName", payload.get("tag_id", nome_arquivo))
                        sent = self._enviar_payload_webhook(payload, nome_arquivo, str(tag))
                        if not sent:
                            all_sent = False

                    if all_sent:
                        try:
                            shutil.move(caminho_origem, caminho_destino)
                        except OSError as e:
                            logging.error(f"✗ Falha ao mover arquivo {nome_arquivo}: {e}")
                    else:
                        logging.error(
                            f"✗ Falha definitiva: {nome_arquivo} não foi enviado "
                            f"após {MAX_RETRY} tentativas. "
                            f"Movido para '{self.REQUISICOES_NAO_ENVIADAS_DIR}'."
                        )
                        try:
                            shutil.move(caminho_origem, caminho_nao_enviado)
                        except OSError as e:
                            logging.error(f"✗ Falha ao mover arquivo: {e}")

                self._update_health_stats()

            except FileNotFoundError as e:
                logging.error(f"Erro no monitor de envio: diretório não encontrado: {e}")
                self._errors_consecutive += 1
            except PermissionError as e:
                logging.error(f"Erro no monitor de envio: permissão negada: {e}")
                self._errors_consecutive += 1
            except OSError as e:
                logging.error(f"Erro no monitor de envio (OS): {e}")
                self._errors_consecutive += 1
            except Exception as e:
                logging.error(
                    f"Erro inesperado no monitor de envio: {type(e).__name__}: {e}"
                )
                self._errors_consecutive += 1

            if self._errors_consecutive >= MAX_ERROS_CONSECUTIVOS:
                logging.critical(
                    f"⚠ ALERTA: {MAX_ERROS_CONSECUTIVOS} erros consecutivos "
                    f"no monitor de envio!"
                )
                self._errors_consecutive = 0

            self._update_health_stats()
            time.sleep(CHECK_FILES_INTERVAL)

    # ── SERIAL ─────────────────────────────────────────────────

    def _open_serial_direct(self) -> Optional[serial.Serial]:
        """Abre porta serial diretamente (modo simples)."""
        tentativas = 0
        ser = None
        while ser is None and tentativas < MAX_TENTATIVAS_PORTA:
            try:
                ser = serial.Serial(self.COM_PORT, self.BAUD_RATE, timeout=0.1)
                logging.info(f"✓ Conectado à porta {self.COM_PORT} com sucesso.")
                return ser
            except serial.SerialException as e:
                tentativas += 1
                logging.error(
                    f"✗ Tentativa {tentativas}/{MAX_TENTATIVAS_PORTA}: "
                    f"Falha ao abrir porta serial {self.COM_PORT}: {e}"
                )
                if tentativas < MAX_TENTATIVAS_PORTA:
                    time.sleep(10)
            except Exception as e:
                logging.critical(
                    f"✗ Erro inesperado ao abrir porta serial: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        if ser is None:
            logging.critical(
                f"✗ NÃO FOI POSSÍVEL CONECTAR à porta {self.COM_PORT} "
                f"após {MAX_TENTATIVAS_PORTA} tentativas. "
                f"Verifique: cabo USB, porta COM, driver."
            )
        return ser

    def _serial_read_loop_simple(self, ser: serial.Serial) -> None:
        """
        Loop serial inline para máquinas simples (BH5100, Coagmaster, VIDAS1600).
        Chama detect_complete_message() e on_message() como hooks.
        """
        buffer = ""
        bytes_recebidos = 0
        mensagens_processadas = 0

        while True:
            try:
                if ser.in_waiting > 0:
                    data = ser.read(ser.in_waiting)
                    bytes_recebidos += len(data)
                    buffer += data.decode("utf-8", errors="ignore")

                    message, buffer = self.detect_complete_message(buffer)
                    while message is not None:
                        self._on_serial_message(message)
                        mensagens_processadas += 1
                        message, buffer = self.detect_complete_message(buffer)

                time.sleep(READ_INTERVAL)

            except serial.SerialException as e:
                logging.error(f"✗ Erro na porta serial: {e}")
                time.sleep(5)
                try:
                    ser.close()
                except Exception:
                    pass
                try:
                    ser.open()
                    logging.info(f"✓ Reconectado à porta {self.COM_PORT}.")
                    buffer = ""
                except Exception as ex:
                    logging.error(f"✗ Falha na reconexão: {ex}")
                    time.sleep(5)

            except Exception as e:
                logging.error(f"✗ Erro no loop serial: {type(e).__name__}: {e}")
                time.sleep(1)

    def _on_serial_message(self, raw_message: str) -> None:
        """
        Chamado quando uma mensagem completa é detectada.
        Salva em GERADOS_DIR e envia ACK se aplicável.
        """
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        extensao = self.get_file_extension()
        filename = f"{timestamp}{extensao}"
        filepath = os.path.join(self.GERADOS_DIR, filename)

        try:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                f.write(raw_message)
        except OSError as e:
            logging.error(f"✗ Erro ao salvar mensagem {filename}: {e}")
            return

        # Envia ACK se a máquina implementar
        ack = self.generate_ack(raw_message)
        if ack is not None:
            self._send_ack(ack)

    def _send_ack(self, ack_data: bytes) -> None:
        """Envia ACK pela porta serial. Sobrescreva se necessário."""
        pass  # Máquinas com serial direto implementam no loop

    # ── START (orquestrador) ──────────────────────────────────

    def start(self) -> None:
        """Inicia o analisador: threads, serial, health server, keep-alive."""
        self._banner()

        # Thread de envio ao webhook
        thread_envio = Thread(target=self._task_sender_to_webhook, daemon=True)
        thread_envio.start()

        # Health server
        self._start_health_server()

        # Serial
        if self._use_serial_listener:
            self._start_with_serial_listener()
        else:
            self._start_with_serial_direct()

    def _start_with_serial_listener(self) -> None:
        """Inicia usando SerialListener (MEK7300, PKL)."""
        self._listener = SerialListener(
            port_name=self.COM_PORT,
            baud_rate=self.BAUD_RATE,
            detect_complete_message=self.detect_complete_message,
            on_message=self._on_serial_message,
            enable_keepalive=self._enable_keepalive,
        )

        if not self._listener.open_port():
            logging.critical("Não foi possível abrir a porta serial. Encerrando.")
            return

        self._listener.start_listening()

        # Timer para verificar status da porta
        def check_port_timer() -> None:
            while True:
                try:
                    if self._listener and not self._listener.is_port_open:
                        self._listener.open_port()
                    if self._health and self._listener:
                        self._health.update_stats(
                            port_open=self._listener.is_port_open,
                            bytes_received=self._listener.bytes_recebidos,
                            messages_processed=self._listener.mensagens_processadas,
                            buffer_size=0,
                            last_activity_seconds_ago=int(
                                time.time() - self._listener.last_activity
                            ) if self._listener.last_activity > 0 else -1,
                        )
                except Exception as ex:
                    logging.error(f"Erro ao verificar porta: {ex}")
                time.sleep(5)

        port_thread = Thread(target=check_port_timer, daemon=True)
        port_thread.start()

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

    def _start_with_serial_direct(self) -> None:
        """Inicia com loop serial inline (BH5100, Coagmaster, VIDAS1600)."""
        ser = self._open_serial_direct()
        if ser is None:
            return

        # Armazena referência para _send_ack
        self._serial = ser

        logging.info(f"=== {self.machine_name} Service (Python) rodando ===")

        try:
            self._serial_read_loop_simple(ser)
        except KeyboardInterrupt:
            logging.info("Interrompido pelo usuário. Encerrando...")
            try:
                ser.close()
            except Exception:
                pass
            if self._health:
                self._health.stop()

    def _send_ack(self, ack_data: bytes) -> None:
        """Envia ACK pela porta serial (modo direto)."""
        if hasattr(self, "_serial") and self._serial and self._serial.is_open:
            try:
                self._serial.write(ack_data)
                logging.debug(f"ACK enviado: {ack_data!r}")
            except Exception as e:
                logging.error(f"Erro ao enviar ACK: {e}")

    # ═══════════════════════════════════════════════════════════
    # HOOKS ABSTRATOS (cada máquina DEVE implementar)
    # ═══════════════════════════════════════════════════════════

    def get_file_extension(self) -> str:
        """Extensão dos arquivos gerados (ex: '.hl7', '.log', '.txt', '.astm')."""
        raise NotImplementedError

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta uma mensagem completa no buffer.
        Retorna (mensagem, buffer_restante) ou (None, buffer) se incompleta.
        """
        raise NotImplementedError

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa um arquivo de GERADOS_DIR e retorna lista de payloads
        para envio ao webhook.

        Retorna:
            - List[dict]: payloads a enviar
            - []: nada a enviar (move para enviados/)
            - None: arquivo inválido (move para enviados/ sem processar)
        """
        raise NotImplementedError

    def generate_ack(self, raw_message: str) -> Optional[bytes]:
        """
        Gera ACK para uma mensagem recebida.
        Retorna bytes do ACK ou None se o protocolo não usa ACK.
        """
        return None
