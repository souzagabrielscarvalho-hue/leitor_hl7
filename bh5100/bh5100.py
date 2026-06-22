"""
Analisador BH5100 — Integração HL7 via porta serial.

Herda de BaseAnalisador e implementa apenas os hooks específicos
do protocolo HL7/MLLP do equipamento BH5100 (Mindray).
"""

import os
import sys
import logging
import datetime
from typing import Any, Dict, List, Optional, Tuple

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador, format_thousands, extrair_imagens_de_hl7

# ═══════════════════════════════════════════════════════════
# CONSTANTES DE PROTOCOLO HL7/MLLP
# ═══════════════════════════════════════════════════════════

SB = chr(0x0B)   # Start Block (VT)
EB = chr(0x1C)   # End Block (FS)
CR = chr(0x0D)   # Carriage Return


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
    'RBC':  'RBC',
    'HGB':  'HGB',
    'HCT':  'HCT',
    'MCV':  'MCV',
    'MCH':  'MCH',
    'MCHC': 'MCHC',
    'RDW_CV':'RDW_CV',
    'RDW_SD':'RDW_SD',
    'PLT':  'PLT',
    'PCT':  'PCT',
    'MPV':  'MPV',
    'PDW':  'PDW',
    'P_LCR':'P_LCR',
    'P-LCR':'P_LCR',
    'P_LCC':'P_LCC',
}

CAMPOS_ABSOLUTOS = {'WBC', 'NE', 'LY', 'MO', 'EO', 'BA', 'PLT'}
CAMPOS_MULTIPLICAR = ('WBC', 'PLT')
PERCENT_PAIRS = [
    ('LY_Percent', 'LY'),
    ('MO_Percent', 'MO'),
    ('EO_Percent', 'EO'),
    ('BA_Percent', 'BA'),
]

ORDEM_TXT = [
    'WBC', 'NE', 'NE_Percent', 'LY', 'LY_Percent',
    'MO', 'MO_Percent', 'EO', 'EO_Percent', 'BA', 'BA_Percent',
    'RBC', 'HGB', 'HCT', 'MCV', 'MCH', 'MCHC', 'RDW_CV', 'RDW_SD',
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

        value = fields[5].lstrip('*')
        abnormal_flag = fields[8] if len(fields) > 8 else ""
        flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''
        resultados[nome] = f"{value}{flag}"

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
    """Analisador para equipamento BH5100 (Mindray) via HL7/MLLP."""

    def get_file_extension(self) -> str:
        return '.hl7'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta mensagem HL7 completa delimitada por SB e EB+CR.
        Retorna (mensagem, buffer_restante) ou (None, buffer).
        """
        while SB in buffer and EB in buffer:
            start = buffer.index(SB)
            end = buffer.index(EB, start)
            if end + 1 < len(buffer) and buffer[end + 1] == CR:
                message = buffer[start:end + 2]  # SB...EB+CR
                buffer = buffer[end + 2:]
                return message, buffer
            else:
                # EB sem CR — aguarda mais dados
                return None, buffer
        return None, buffer

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

        # Extrai imagens
        try:
            pasta_imagens = os.path.join(self.ENVIADOS_DIR, "imagens")
            prefixo = os.path.splitext(nome_arquivo)[0] + "_"
            qtd = extrair_imagens_de_hl7(conteudo_hl7, pasta_imagens, prefixo)
            if qtd > 0:
                logging.info(f"{qtd} imagem(ns) extraída(s) de {nome_arquivo}.")
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
        return [payload]


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

if __name__ == "__main__":
    AnalisadorBH5100(
        machine_id='bh5100',
        machine_name='BH5100',
        config_defaults={
            'com_port': 'COM3',
            'baud_rate': 9600,
            'franchise_credential_id': 'f47d9a16-1a3c-4c2e-9e5f-6b3c8d7e9f0a',
            'webhook_url': (
                'https://apoio.internal.vidaexame.com/api/integration/bh5100'
                '?franchise_credential_id={franchise_credential_id}'
            ),
        },
        health_port=8080,
    ).start()
