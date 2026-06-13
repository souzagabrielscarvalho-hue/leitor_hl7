import os
import sys
import time
import shutil
import logging
import datetime
import threading

import serial
import requests

# Import do módulo de limpeza compartilhado
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.file_cleanup import FileCleanupConfig, start_cleanup_thread
from shared.config_loader import load_config

# ================= CONFIGURAÇÕES =================
# As configurações abaixo são valores padrão.
# Para alterar COM_PORT ou FRANCHISE_CREDENTIAL_ID sem recompilar o .exe,
# edite o arquivo config_mek7300.json que fica ao lado do executável.
# Se o arquivo não existir, ele será criado automaticamente na primeira execução.
_config, _config_status = load_config('mek7300', {
    'com_port': 'COM5',
    'baud_rate': 9600,
    'franchise_credential_id': '88cf9273-5044-47f4-b8f6-01160345a190',
    'webhook_url': 'https://apoio.internal.vidaexame.com/api/integration/mek7300/v2?franchise_credential_id={franchise_credential_id}',
})

COM_PORT = _config['com_port']
BAUD_RATE = _config['baud_rate']
FRANCHISE_CREDENTIAL_ID = _config['franchise_credential_id']

# Webhook do Vida Exame (V2 — campos individuais no JSON, igual ao BH5100)
# Local: http://localhost/api/integration/mek7300/v2?franchise_credential_id=...
# Produção: https://apoio.internal.vidaexame.com/api/integration/mek7300/v2?franchise_credential_id=...
WEBHOOK_URL = _config['webhook_url'].format(franchise_credential_id=FRANCHISE_CREDENTIAL_ID)

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
MAX_RETRY = 5
RETRY_INTERVAL = 60
# =================================================

# Pastas de trabalho – na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorMEK7300")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
REQUISICOES_NAO_ENVIADAS_DIR = os.path.join(BASE_DIR, "requisições não enviadas")
LOG_FILE = os.path.join(BASE_DIR, "analisador_mek7300.log")
# =================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)
os.makedirs(REQUISICOES_NAO_ENVIADAS_DIR, exist_ok=True)

# Redirecionar stdout/stderr para evitar travamento em modo --noconsole (PyInstaller)
# Quando não há console, sys.stdout/sys.stderr podem ser None ou causar erro ao escrever
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

ETX = chr(0x03)


# ---------------------------------------------------------------------------
# Processamento de dados
# ---------------------------------------------------------------------------

def multiply_by_1000(raw_value: str) -> str:
    """
    Multiplica o valor por 1000, preservando flags como L, H e *.
    Ex: "5.8" → "5800", "224*" → "224000*", "1.6L" → "1600L"
    """
    if not raw_value or not raw_value.strip():
        return raw_value

    trimmed = raw_value.strip()

    # Extrai flags no final (L, H, LH, HL, *, ?)
    suffix = ""
    while trimmed and (trimmed[-1].isalpha() or trimmed[-1] in ('*', '?')):
        suffix = trimmed[-1] + suffix
        trimmed = trimmed[:-1]

    # Remove prefixo * de valor anormal (caso venha no início)
    has_asterisk_prefix = trimmed.startswith("*")
    if has_asterisk_prefix:
        trimmed = trimmed[1:]

    try:
        value = float(trimmed)
        result = value * 1000
        # Formata sem casas decimais desnecessárias
        if result == int(result):
            formatted = str(int(result))
        else:
            formatted = str(result)
        prefix = "*" if has_asterisk_prefix else ""
        return f"{prefix}{formatted}{suffix}"
    except ValueError:
        logging.warning(f"Aviso: Não foi possível multiplicar o valor '{raw_value}' por 1000.")
        return raw_value


def format_thousands(raw_value: str) -> str:
    """
    Adiciona separador de milhar (.) para valores > 999, preservando flags como L, H e *.
    Ex: "7640" → "7.640", "224000*" → "224.000*", "1600L" → "1.600L", "350" → "350"
    """
    if not raw_value or not raw_value.strip():
        return raw_value

    trimmed = raw_value.strip()

    # Extrai flags no final (L, H, LH, HL, *, ?)
    suffix = ""
    while trimmed and (trimmed[-1].isalpha() or trimmed[-1] in ('*', '?')):
        suffix = trimmed[-1] + suffix
        trimmed = trimmed[:-1]

    # Remove prefixo * de valor anormal
    has_asterisk_prefix = trimmed.startswith("*")
    if has_asterisk_prefix:
        trimmed = trimmed[1:]

    try:
        value = float(trimmed)
        if value == int(value):
            int_value = int(value)
        else:
            # Valor com decimal — não formata milhar, retorna como está
            prefix = "*" if has_asterisk_prefix else ""
            return f"{prefix}{trimmed}{suffix}"

        # Só formata se > 999
        if int_value > 999:
            str_val = str(int_value)
            # Insere "." a cada 3 dígitos da direita para esquerda
            parts = []
            while len(str_val) > 3:
                parts.append(str_val[-3:])
                str_val = str_val[:-3]
            parts.append(str_val)
            formatted = ".".join(reversed(parts))
        else:
            formatted = str(int_value)

        prefix = "*" if has_asterisk_prefix else ""
        return f"{prefix}{formatted}{suffix}"
    except ValueError:
        logging.warning(f"Aviso: Não foi possível formatar milhar do valor '{raw_value}'.")
        return raw_value


def create_initialization_file(data_received: str) -> bool:
    """
    Processa os dados brutos recebidos da porta serial e salva arquivo formatado.
    Retorna True se o arquivo foi criado com sucesso.
    """
    try:
        lines = [line.strip() for line in data_received.splitlines() if line.strip()]

        # Verifica se os dados têm o tamanho esperado (data + 23 campos)
        if len(lines) < 24:
            logging.error(f"Erro: Dados incompletos recebidos. Esperado >= 24 linhas, recebido {len(lines)}.")
            return False

        # Remove a primeira linha (data)
        data = lines[1:]

        # Remove um '0' do início do barcode, se presente.
        # O MEK7300 pode enviar o barcode com um zero à frente (ex: "012345678901"),
        # mas o sistema espera apenas os 12 dígitos sem o zero (ex: "123456789012").
        # Remove apenas um zero — se não começar com zero, mantém como está.
        raw_barcode = data[0]
        corrected_barcode = raw_barcode[1:] if raw_barcode.startswith('0') else raw_barcode

        # Mapeia os dados recebidos
        formated_file = {
            "FileName": corrected_barcode,
            "WBC": format_thousands(multiply_by_1000(data[1])),
            "LY_Percent": data[2],
            "MO_Percent": data[3],
            "NE_Percent": data[4],
            "EO_Percent": data[5],
            "BA_Percent": data[6],
            "LY": format_thousands(multiply_by_1000(data[7])),
            "MO": format_thousands(multiply_by_1000(data[8])),
            "NE": format_thousands(multiply_by_1000(data[9])),
            "EO": format_thousands(multiply_by_1000(data[10])),
            "BA": format_thousands(multiply_by_1000(data[11])),
            "RBC": data[12],
            "HGB": data[13],
            "HCT": data[14],
            "MCV": data[15],
            "MCH": data[16],
            "MCHC": data[17],
            "RDWCV": data[18],
            "PLT": format_thousands(multiply_by_1000(data[19])),
            "PCT": data[20],
            "MPV": data[21],
            "PDW": data[22],
        }

        return save_to_file(formated_file)

    except Exception as ex:
        logging.error(f"Erro ao processar os dados: {ex}")
        return False


def save_to_file(formated_file: dict) -> bool:
    """Salva os dados formatados em arquivo texto na pasta 'gerados'."""
    try:
        directory_path = GERADOS_DIR
        os.makedirs(directory_path, exist_ok=True)

        file_name = f"{formated_file['FileName']}.txt"
        file_path = os.path.join(directory_path, file_name)
        logging.info(f"FilePath: {file_path}")

        # Usa \r\n (CRLF) para compatibilidade com o parse_txt_to_dict local.
        # newline='' desabilita a conversão automática de newlines no Windows
        # (evita que \r\n seja convertido para \n na leitura/escrita).
        with open(file_path, "w", encoding="utf-8", newline='') as f:
            f.write(f"FileName: {formated_file['FileName']}\r\n")
            f.write(f"WBC: {formated_file['WBC']}\r\n")
            f.write(f"NE: {formated_file['NE']}\r\n")
            f.write(f"NE_Percent: {formated_file['NE_Percent']}\r\n")
            f.write(f"LY: {formated_file['LY']}\r\n")
            f.write(f"LY_Percent: {formated_file['LY_Percent']}\r\n")
            f.write(f"MO: {formated_file['MO']}\r\n")
            f.write(f"MO_Percent: {formated_file['MO_Percent']}\r\n")
            f.write(f"EO: {formated_file['EO']}\r\n")
            f.write(f"EO_Percent: {formated_file['EO_Percent']}\r\n")
            f.write(f"BA: {formated_file['BA']}\r\n")
            f.write(f"BA_Percent: {formated_file['BA_Percent']}\r\n")
            f.write(f"RBC: {formated_file['RBC']}\r\n")
            f.write(f"HGB: {formated_file['HGB']}\r\n")
            f.write(f"HCT: {formated_file['HCT']}\r\n")
            f.write(f"MCV: {formated_file['MCV']}\r\n")
            f.write(f"MCH: {formated_file['MCH']}\r\n")
            f.write(f"MCHC: {formated_file['MCHC']}\r\n")
            f.write(f"RDWCV: {formated_file['RDWCV']}\r\n")
            f.write(f"PLT: {formated_file['PLT']}\r\n")
            f.write(f"PCT: {formated_file['PCT']}\r\n")
            f.write(f"MPV: {formated_file['MPV']}\r\n")
            f.write(f"PDW: {formated_file['PDW']}\r\n")

        logging.info(f"Arquivo '{file_name}' criado com sucesso na pasta 'gerados'.")
        logging.debug(f"[DEBUG] Conteúdo do arquivo '{file_name}' (repr): {repr(open(file_path, 'r', encoding='utf-8').read()[:200])}")
        return True

    except Exception as ex:
        logging.error(f"Erro ao criar o arquivo: {ex}")
        return False


def parse_txt_to_dict(txt_content: str) -> dict:
    """
    Extrai os campos do hemograma do conteúdo TXT (formato "Chave: Valor").
    Retorna um dict com os campos individuais, pronto para enviar ao webhook.
    Os valores já chegam multiplicados por 1000 do save_to_file — não multiplicar novamente.
    """
    try:
        campos = {}
        for line in txt_content.strip().splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue

            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()

            if key == 'FileName':
                campos['_barcode'] = value
            else:
                campos[key] = value

        logging.info(f"parse_txt_to_dict: {len(campos)} campo(s) extraídos do TXT.")
        return campos
    except Exception as e:
        logging.error(f"Erro ao fazer parse do TXT: {e}")
        return {}


# ---------------------------------------------------------------------------
# Envio para webhook
# ---------------------------------------------------------------------------

def _enviar_payload_webhook(payload: dict, nome_arquivo: str, barcode: str) -> bool:
    """
    Envia um payload para o webhook com até MAX_RETRY tentativas.
    Retorna True se o envio foi bem-sucedido, False caso contrário.
    """
    headers = {'Content-Type': 'application/json'}

    for tentativa in range(1, MAX_RETRY + 1):
        try:
            logging.info(f"Enviando {nome_arquivo} (tag_identifier: {barcode}) para o webhook... [Tentativa {tentativa}/{MAX_RETRY}]")
            response = requests.post(
                WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in (200, 201):
                try:
                    resp_json = response.json()
                    msg = resp_json.get('message', 'OK')
                    logging.info(f"✓ Sucesso ({response.status_code}): {nome_arquivo} enviado ao Webhook.")
                    logging.info(f"  Mensagem: {msg}")
                    # Verifica se a mensagem indica erro apesar do status 200
                    if msg and 'invalid' in msg.lower():
                        logging.error(f"✗ ERRO: Servidor retornou status {response.status_code} mas rejeitou o arquivo: {msg}")
                        logging.error(f"  Possíveis causas: tag_identifier não encontrado no sistema, procedimento já liberado, ou código de barras inválido.")
                        logging.error(f"  tag_identifier enviado: {barcode}")
                        return False
                except Exception:
                    pass
                return True
            elif response.status_code == 404:
                logging.error(f"✗ ERRO 404: Endpoint não encontrado para {nome_arquivo}.")
                logging.error(f"  Verifique se a URL está correta: {WEBHOOK_URL}")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code == 400:
                logging.error(f"✗ ERRO 400: Requisição inválida para {nome_arquivo} (tag_identifier: {barcode}).")
                logging.error(f"  Possíveis causas: tag_identifier não encontrado, procedimento já liberado, ou conteúdo inválido.")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code == 500:
                logging.error(f"✗ ERRO 500: Erro interno do servidor ao processar {nome_arquivo}.")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code in (401, 403):
                logging.error(f"✗ ERRO {response.status_code}: Falha de autenticação para {nome_arquivo}.")
                logging.error(f"  Verifique o FRANCHISE_CREDENTIAL_ID na URL: {WEBHOOK_URL}")
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
    """Lê arquivos da pasta 'gerados' e envia para o webhook com retry."""
    logging.info("Iniciando monitor de envio para Webhook...")
    logging.info(f"URL do Webhook: {WEBHOOK_URL}")
    logging.info(f"Verificando arquivos a cada {CHECK_FILES_INTERVAL}s na pasta: {GERADOS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")

    erros_consecutivos = 0
    MAX_ERROS_CONSECUTIVOS = 10

    while True:
        try:
            arquivos = [f for f in os.listdir(GERADOS_DIR) if f.endswith('.txt')]

            if arquivos:
                logging.info(f"Encontrados {len(arquivos)} arquivo(s) TXT para processar.")
                erros_consecutivos = 0  # reset ao encontrar arquivos

            for nome_arquivo in arquivos:
                caminho_origem = os.path.join(GERADOS_DIR, nome_arquivo)
                caminho_destino = os.path.join(ENVIADOS_DIR, nome_arquivo)
                caminho_nao_enviado = os.path.join(REQUISICOES_NAO_ENVIADAS_DIR, nome_arquivo)

                # Lê o arquivo TXT com newline='' para preservar CRLF no Windows
                try:
                    with open(caminho_origem, 'r', encoding='utf-8', newline='') as f:
                        file_content = f.read()
                except PermissionError:
                    logging.error(f"✗ Permissão negada ao ler arquivo: {nome_arquivo} — o arquivo pode estar em uso.")
                    continue
                except FileNotFoundError:
                    logging.warning(f"Arquivo {nome_arquivo} não encontrado (pode ter sido removido por outro processo).")
                    continue
                except Exception as e:
                    logging.error(f"✗ Erro inesperado ao ler arquivo {nome_arquivo}: {type(e).__name__}: {e}")
                    continue

                if not file_content or not file_content.strip():
                    logging.warning(f"Arquivo {nome_arquivo} está vazio, movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)
                    continue

                # Extrai o FileName (código de barras / tag_identifier) do conteúdo do arquivo
                barcode = ""
                for line in file_content.strip().splitlines():
                    if line.startswith("FileName:"):
                        barcode = line.split(":", 1)[1].strip()
                        break

                if not barcode:
                    logging.error(f"Arquivo {nome_arquivo}: FileName (tag_identifier) não encontrado no conteúdo.")
                    logging.error(f"  Não é possível enviar sem código de barras — o tag_identifier é obrigatório para identificar o procedimento.")
                    logging.error(f"  Movendo para '{REQUISICOES_NAO_ENVIADAS_DIR}'.")
                    shutil.move(caminho_origem, caminho_nao_enviado)
                    continue

                # Faz o parse do TXT para extrair os campos individuais do hemograma
                campos = parse_txt_to_dict(file_content)

                if not campos or '_barcode' not in campos:
                    logging.error(f"Arquivo {nome_arquivo}: não foi possível extrair campos do hemograma.")
                    logging.error(f"  Movendo para '{REQUISICOES_NAO_ENVIADAS_DIR}'.")
                    shutil.move(caminho_origem, caminho_nao_enviado)
                    continue

                # Remove o _barcode interno, usa o barcode extraído como FileName
                barcode = campos.pop('_barcode')

                # Monta o payload no formato V2 (campos individuais, igual ao BH5100):
                # FileName é o barcode puro (sem .txt), franchise_credential_id já está na URL.
                # O ReadBloodCountMachineMek7300V2.php recebe array $hemogramData com os campos.
                payload = {
                    'FileName': barcode,
                    'ExamCode': 'HEMO',
                    **campos  # espalha WBC, NE, NE_Percent, LY, etc. como chaves individuais
                }

                # Log de debug do payload
                logging.info(f"[DEBUG] Payload FileName: {barcode}")
                logging.info(f"[DEBUG] Payload campos: {list(campos.keys())}")

                # Tenta enviar com retry
                enviado = _enviar_payload_webhook(payload, nome_arquivo, barcode)

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
            erros_consecutivos = 0  # reseta para não floodar o log

        time.sleep(CHECK_FILES_INTERVAL)


# ---------------------------------------------------------------------------
# Listener serial
# ---------------------------------------------------------------------------

class SerialListener:
    """Monitora a porta serial e acumula dados até detectar ETX (0x03)."""

    def __init__(self, port_name: str, baud_rate: int):
        self.port_name = port_name
        self.baud_rate = baud_rate
        self._serial_port = None
        self._buffer = ""
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    @property
    def is_port_open(self) -> bool:
        return self._serial_port is not None and self._serial_port.is_open

    def open_port(self):
        """Abre a porta serial."""
        try:
            self._serial_port = serial.Serial(
                port=self.port_name,
                baudrate=self.baud_rate,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1,
            )
            logging.info(f"✓ Conectado à porta {self.port_name} com sucesso.")
        except Exception as ex:
            logging.error(f"✗ Erro ao abrir porta {self.port_name}: {ex}")

    def start_listening(self):
        """Inicia a thread de leitura da porta serial."""
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        logging.info("Thread de leitura serial iniciada.")

    def stop_listening(self):
        """Para a thread de leitura e fecha a porta serial."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._serial_port and self._serial_port.is_open:
            self._serial_port.close()
            logging.info("Porta serial fechada.")

    def _read_loop(self):
        """Loop principal de leitura da porta serial."""
        # Watchdog: se a porta ficar >60s sem receber dados, força reabertura.
        # Evita que handles USB inválidos (ClearCommError) passem despercebidos
        # por longos períodos quando in_waiting == 0.
        WATCHDOG_TIMEOUT = 60  # segundos
        last_activity = time.time()

        while self._running:
            if not self.is_port_open:
                self.open_port()
                last_activity = time.time()
                time.sleep(2)
                continue

            try:
                if self._serial_port.in_waiting > 0:
                    last_activity = time.time()  # reseta watchdog
                    incoming = self._serial_port.read(self._serial_port.in_waiting).decode("ascii", errors="replace")
                    logging.info(f"Recebendo dados via serial...")
                    logging.debug(f"Dados brutos: {incoming}")

                    with self._lock:
                        self._buffer += incoming

                        if ETX in self._buffer:
                            logging.info("Fim de mensagem detectado (ETX)")

                            # Extrai o conteúdo até o ETX
                            etx_pos = self._buffer.index(ETX)
                            exam_data = self._buffer[:etx_pos]
                            self._buffer = self._buffer[etx_pos + 1:]

                            logging.info(f"Dados completos recebidos ({len(exam_data)} caracteres)")

                            create_initialization_file(exam_data)

                time.sleep(READ_INTERVAL)

                # Watchdog: se a porta está aberta mas sem atividade por >60s,
                # força reabertura para evitar handles USB inválidos silenciosos.
                if self.is_port_open and (time.time() - last_activity) > WATCHDOG_TIMEOUT:
                    logging.warning(
                        f"Watchdog: porta {self.port_name} sem atividade há "
                        f"{int(time.time() - last_activity)}s — forçando reabertura."
                    )
                    try:
                        self._serial_port.close()
                    except Exception:
                        pass
                    self._serial_port = None
                    last_activity = time.time()

            except Exception as ex:
                self._buffer = ""
                logging.error(f"✗ Erro no read_loop: {ex}")
                # Fecha a porta para forçar reabertura na próxima iteração.
                # ClearCommError (OSError 22) indica handle inválido — manter a porta
                # "aberta" impede a recuperação automática.
                try:
                    if self._serial_port and self._serial_port.is_open:
                        self._serial_port.close()
                        logging.info("Porta serial fechada após erro — será reaberta na próxima iteração.")
                except Exception as close_ex:
                    logging.error(f"Erro ao fechar porta serial: {close_ex}")
                time.sleep(1)


# ---------------------------------------------------------------------------
# Serviço principal
# ---------------------------------------------------------------------------

def main():
    """Ponto de entrada principal do serviço."""
    logging.info("=" * 60)
    logging.info("Analisador MEK7300 - Serviço de Integração")
    logging.info(f"Data/Hora de início: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Porta Serial: {COM_PORT} | Baud Rate: {BAUD_RATE}")
    logging.info(f"Webhook: {WEBHOOK_URL}")
    logging.info(f"Franchise Credential ID: {FRANCHISE_CREDENTIAL_ID}")
    logging.info(f"Pastas: gerados={GERADOS_DIR} | enviados={ENVIADOS_DIR} | não enviados={REQUISICOES_NAO_ENVIADAS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
    logging.info("=" * 60)

    # Thread de envio ao webhook
    thread_envio = threading.Thread(target=task_sender_to_webhook, daemon=True)
    thread_envio.start()
    logging.info("Thread de envio iniciada.")

    # Configurar e iniciar thread de limpeza de arquivos
    cleanup_config = FileCleanupConfig()
    cleanup_config.log_file_path = LOG_FILE
    cleanup_config.cleanup_directories = [ENVIADOS_DIR, REQUISICOES_NAO_ENVIADAS_DIR]
    start_cleanup_thread(cleanup_config)

    # Inicia o listener serial
    listener = SerialListener(COM_PORT, BAUD_RATE)
    listener.open_port()
    listener.start_listening()

    # Timer para verificar status da porta a cada 5 segundos
    def check_port_timer():
        while True:
            try:
                if not listener.is_port_open:
                    listener.open_port()
            except Exception as ex:
                logging.error(f"Erro ao verificar porta: {ex}")
            time.sleep(5)

    port_thread = threading.Thread(target=check_port_timer, daemon=True)
    port_thread.start()

    logging.info("=== MEK7300 Service (Python) rodando ===")

    # Mantém o programa rodando
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Interrompido pelo usuário. Encerrando...")
        listener.stop_listening()


if __name__ == "__main__":
    main()