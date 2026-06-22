"""
Analisador MEK7300 — Integração via porta serial (protocolo delimitado por ETX).

Herda de BaseAnalisador com SerialListener, watchdog e keepalive ativados.
Implementa apenas os hooks específicos do protocolo do MEK7300.
"""

import os
import sys
import logging
from typing import Any, Dict, List, Optional, Tuple

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador, format_thousands as _base_format_thousands

# ═══════════════════════════════════════════════════════════
# CONSTANTES DE PROTOCOLO
# ═══════════════════════════════════════════════════════════

ETX = chr(0x03)


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE PROCESSAMENTO (MEK7300-specific)
# ═══════════════════════════════════════════════════════════

def multiply_by_1000(raw_value: str) -> str:
    """
    Multiplica o valor por 1000, preservando flags como L, H e *.
    Ex: ""5.8"" → ""5800"", ""224*"" → ""224000*"", ""1.6L"" → ""1600L""
    """
    if not raw_value or not raw_value.strip():
        return raw_value

    trimmed = raw_value.strip()
    suffix = ""
    while trimmed and (trimmed[-1].isalpha() or trimmed[-1] in ('*', '?')):
        suffix = trimmed[-1] + suffix
        trimmed = trimmed[:-1]

    has_asterisk_prefix = trimmed.startswith("*")
    if has_asterisk_prefix:
        trimmed = trimmed[1:]

    try:
        value = float(trimmed)
        result = value * 1000
        if result == int(result):
            formatted = str(int(result))
        else:
            formatted = str(result)
        prefix = "*" if has_asterisk_prefix else ""
        return f"{prefix}{formatted}{suffix}"
    except ValueError:
        logging.warning(f"Aviso: Não foi possível multiplicar o valor '{raw_value}' por 1000.")
        return raw_value


def format_thousands_mek(raw_value: str) -> str:
    """
    Adiciona separador de milhar (.) para valores > 999, preservando flags.
    Ex: ""7640"" → ""7.640"", ""224000*"" → ""224.000*"", ""1600L"" → ""1.600L""
    """
    if not raw_value or not raw_value.strip():
        return raw_value

    trimmed = raw_value.strip()
    suffix = ""
    while trimmed and (trimmed[-1].isalpha() or trimmed[-1] in ('*', '?')):
        suffix = trimmed[-1] + suffix
        trimmed = trimmed[:-1]

    has_asterisk_prefix = trimmed.startswith("*")
    if has_asterisk_prefix:
        trimmed = trimmed[1:]

    try:
        value = float(trimmed)
        if value == int(value):
            int_value = int(value)
        else:
            prefix = "*" if has_asterisk_prefix else ""
            return f"{prefix}{trimmed}{suffix}"

        if int_value > 999:
            str_val = str(int_value)
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


def create_initialization_file(data_received: str, gerados_dir: str) -> bool:
    """
    Processa os dados brutos recebidos da porta serial e salva arquivo .txt
    formatado na pasta gerados/. Retorna True se o arquivo foi criado.
    """
    try:
        lines = [line.strip() for line in data_received.splitlines() if line.strip()]

        if len(lines) < 24:
            logging.error(
                f"Erro: Dados incompletos recebidos. "
                f"Esperado >= 24 linhas, recebido {len(lines)}."
            )
            return False

        data = lines[1:]  # Remove a primeira linha (data)

        # Remove um '0' do início do barcode, se presente
        raw_barcode = data[0]
        corrected_barcode = (
            raw_barcode[1:] if raw_barcode.startswith('0') else raw_barcode
        )

        formated_file = {
            "FileName": corrected_barcode,
            "WBC": format_thousands_mek(multiply_by_1000(data[1])),
            "LY_Percent": data[2],
            "MO_Percent": data[3],
            "NE_Percent": data[4],
            "EO_Percent": data[5],
            "BA_Percent": data[6],
            "LY": format_thousands_mek(multiply_by_1000(data[7])),
            "MO": format_thousands_mek(multiply_by_1000(data[8])),
            "NE": format_thousands_mek(multiply_by_1000(data[9])),
            "EO": format_thousands_mek(multiply_by_1000(data[10])),
            "BA": format_thousands_mek(multiply_by_1000(data[11])),
            "RBC": data[12],
            "HGB": data[13],
            "HCT": data[14],
            "MCV": data[15],
            "MCH": data[16],
            "MCHC": data[17],
            "RDW_CV": data[18],
            "PLT": format_thousands_mek(multiply_by_1000(data[19])),
            "PCT": data[20],
            "MPV": data[21],
            "PDW": data[22],
        }

        return _save_formatted_file(formated_file, gerados_dir)

    except Exception as ex:
        logging.error(f"Erro ao processar os dados: {ex}")
        return False


def _save_formatted_file(formated_file: dict, gerados_dir: str) -> bool:
    """Salva os dados formatados em arquivo .txt na pasta gerados/."""
    try:
        os.makedirs(gerados_dir, exist_ok=True)
        file_name = f"{formated_file['FileName']}.txt"
        file_path = os.path.join(gerados_dir, file_name)
        logging.info(f"FilePath: {file_path}")

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
            f.write(f"RDW_CV: {formated_file['RDW_CV']}\r\n")
            f.write(f"PLT: {formated_file['PLT']}\r\n")
            f.write(f"PCT: {formated_file['PCT']}\r\n")
            f.write(f"MPV: {formated_file['MPV']}\r\n")
            f.write(f"PDW: {formated_file['PDW']}\r\n")

        logging.info(f"Arquivo '{file_name}' criado com sucesso na pasta 'gerados'.")
        return True

    except Exception as ex:
        logging.error(f"Erro ao criar o arquivo: {ex}")
        return False


def parse_txt_to_dict(txt_content: str) -> dict:
    """
    Extrai os campos do hemograma do conteúdo TXT (formato ""Chave: Valor"").
    Os valores já chegam multiplicados por 1000 do save — não multiplicar novamente.
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


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorMEK7300(BaseAnalisador):
    """Analisador para equipamento MEK7300 via protocolo delimitado por ETX."""

    def get_file_extension(self) -> str:
        return '.txt'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta mensagem completa delimitada por ETX (0x03).
        Retorna (mensagem, buffer_restante) ou (None, buffer).
        """
        if ETX in buffer:
            etx_pos = buffer.index(ETX)
            message = buffer[:etx_pos]
            buffer = buffer[etx_pos + 1:]
            if message.strip():
                return message, buffer
            return None, buffer
        return None, buffer

    def _on_serial_message(self, raw_message: str) -> None:
        """
        Sobrescreve o comportamento padrão: em vez de salvar o raw message,
        processa com create_initialization_file que já salva .txt formatado
        em gerados/.
        """
        logging.info(f"Dados completos recebidos ({len(raw_message)} caracteres)")
        success = create_initialization_file(raw_message, self.GERADOS_DIR)
        if not success:
            logging.error("Falha ao processar mensagem serial — dados incompletos ou inválidos.")

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa arquivo .txt: extrai barcode, faz parse, monta payload.
        """
        # Lê o arquivo TXT
        try:
            with open(filepath, 'r', encoding='utf-8', newline='') as f:
                file_content = f.read()
        except PermissionError:
            logging.error(f"✗ Permissão negada ao ler: {nome_arquivo}")
            return None
        except FileNotFoundError:
            logging.warning(f"Arquivo {nome_arquivo} não encontrado.")
            return None
        except Exception as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        if not file_content or not file_content.strip():
            logging.warning(f"Arquivo {nome_arquivo} está vazio.")
            return None

        # Extrai barcode
        barcode = ""
        for line in file_content.strip().splitlines():
            if line.startswith("FileName:"):
                barcode = line.split(":", 1)[1].strip()
                break

        if not barcode:
            logging.error(
                f"Arquivo {nome_arquivo}: FileName (tag_identifier) não encontrado."
            )
            logging.error(
                f"  Movendo para '{self.REQUISICOES_NAO_ENVIADAS_DIR}'."
            )
            return []  # task_sender move para não-enviados

        # Parse TXT → dict
        campos = parse_txt_to_dict(file_content)
        if not campos or '_barcode' not in campos:
            logging.error(
                f"Arquivo {nome_arquivo}: não foi possível extrair campos do hemograma."
            )
            logging.error(
                f"  Movendo para '{self.REQUISICOES_NAO_ENVIADAS_DIR}'."
            )
            return []

        barcode = campos.pop('_barcode')

        payload = {
            'FileName': barcode,
            'ExamCode': 'HEMO',
            **campos,
        }
        return [payload]


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorMEK7300(
        machine_id='mek7300',
        machine_name='MEK7300',
        config_defaults={
            'com_port': 'COM5',
            'baud_rate': 9600,
            'franchise_credential_id': '88cf9273-5044-47f4-b8f6-01160345a190',
            'webhook_url': (
                'https://apoio.internal.vidaexame.com/api/integration/mek7300/v2'
                '?franchise_credential_id={franchise_credential_id}'
            ),
        },
        health_port=8082,
        use_serial_listener=True,
        enable_watchdog=True,
        enable_keepalive=True,
    ).start()
