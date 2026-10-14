"""
Analisador VIDAS 1600 — Integração HL7 via porta serial (bioquímico).

Herda de BaseAnalisador e implementa apenas os hooks específicos
do protocolo HL7/MLLP do equipamento VIDAS 1600 (bioMérieux E-LAB).
"""

import os
import sys
import json
import logging
import datetime
from typing import Any, Dict, List, Optional, Tuple

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador, extrair_imagens_de_hl7

# ═══════════════════════════════════════════════════════════
# CONSTANTES DE PROTOCOLO HL7/MLLP
# ═══════════════════════════════════════════════════════════

SB = chr(0x0B)
EB = chr(0x1C)
CR = chr(0x0D)

# ═══════════════════════════════════════════════════════════
# MAPEAMENTO DE TESTES → EXAM_CODE (VIDAS 1600)
# ═══════════════════════════════════════════════════════════

TEST_TO_EXAM_CODE = {
    # Função Hepática (HEP)
    'TGP': 'HEP', 'TGO': 'HEP', 'Bili T': 'HEP', 'Bili D': 'HEP',
    'Falc': 'HEP', 'GGT': 'HEP', 'Alb': 'HEP', 'PT': 'HEP',
    'ALFA': 'HEP', 'COLIN': 'HEP',
    # Função Renal (REN)
    'Ureia': 'REN', 'Crea': 'REN', 'AUR': 'REN',
    # Glicose (GLI)
    'Gli': 'GLI', 'HbA1c': 'GLI', 'FRU': 'GLI',
    # Lipídios (LIP)
    'Col': 'LIP', 'Trig': 'LIP', 'HDL': 'LIP',
    # Eletrólitos (ELE)
    'CL': 'ELE', 'Ca': 'ELE', 'FOSF': 'ELE', 'MG': 'ELE',
    # Marcadores Cardíacos (CARD)
    'CKNAC': 'CARD', 'CKMB': 'CARD', 'LDH': 'CARD',
    'PCR': 'CARD', 'PCRu': 'CARD', 'PCRDUO': 'CARD', 'PCRuDUO': 'CARD',
    # Amilase/Lipase (AML)
    'Ami': 'AML', 'LIP': 'AML',
    # Ferro/Anemia (FER)
    'Fe': 'FER', 'FERRI': 'FER',
    # Outros (OUT)
    'ASO': 'OUT', 'FR': 'OUT', 'LAC': 'OUT', 'PTUR': 'OUT',
}


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE PARSE HL7 (VIDAS 1600-specific)
# ═══════════════════════════════════════════════════════════

def _clean_hl7(message: str) -> List[str]:
    clean = message.replace(SB, '').replace(EB, '')
    clean = clean.replace('\r\n', '\n').replace('\r', '\n')
    return clean.split('\n')


def _extract_identifiers(segments: List[str]) -> Tuple[str, str, str]:
    """Extrai barcode, sample_id e sample_type do OBR."""
    for seg in segments:
        fields = seg.split('|')
        if fields[0] == 'OBR':
            barcode = fields[2].strip() if len(fields) > 2 else ""
            sample_id = fields[3].strip() if len(fields) > 3 else ""
            sample_type = fields[15].strip() if len(fields) > 15 else ""
            return barcode, sample_id, sample_type
    return "", "", ""


def parse_hl7_to_dict(hl7_message: str) -> dict:
    """
    Extrai dados de uma mensagem HL7 ORU^R01 do VIDAS 1600.
    Retorna dict com FileName, ExamCode, results_by_exam e campos raiz.
    """
    result: Dict[str, Any] = {}
    raw_tests: Dict[str, str] = {}

    try:
        segments = _clean_hl7(hl7_message)
        barcode, sample_id, sample_type = _extract_identifiers(segments)

        tag_identifier = barcode if barcode else sample_id
        if not tag_identifier:
            tag_identifier = "DESCONHECIDO"

        result['FileName'] = tag_identifier
        result['ExamCode'] = 'QUIM'

        # Extrai resultados dos OBX
        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue
            if fields[2] == 'ED':
                continue

            test_name = fields[4].strip() if len(fields) > 4 and fields[4] else ""
            key = test_name if test_name else (
                fields[3].split('^')[0].strip() if len(fields) > 3 and fields[3] else ""
            )
            if not key:
                continue

            value = fields[5].strip() if len(fields) > 5 and fields[5] else ""
            if not value:
                continue

            abnormal_flag = fields[8].strip() if len(fields) > 8 and fields[8] else ""
            flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''
            result_value = f"{value}{flag}" if flag else value

            raw_tests[key] = result_value
            result[key] = result_value

        # Agrupa por ExamCode
        results_by_exam: Dict[str, Dict[str, str]] = {}
        unmapped: List[str] = []

        for test_name, test_value in raw_tests.items():
            exam_code = TEST_TO_EXAM_CODE.get(test_name)
            if exam_code:
                results_by_exam.setdefault(exam_code, {})[test_name] = test_value
            else:
                unmapped.append(test_name)
                results_by_exam.setdefault('QUIM', {})[test_name] = test_value

        result['results_by_exam'] = results_by_exam
        if unmapped:
            logging.warning(
                f"Testes não mapeados (usando QUIM): {unmapped}"
            )

    except Exception as e:
        logging.error(f"Erro ao parsear HL7 do VIDAS 1600: {e}")
        return {}

    return result


def parse_hl7_to_txt(hl7_message: str) -> str:
    """Converte HL7 para TXT legível (debug)."""
    try:
        segments = _clean_hl7(hl7_message)
        barcode, sample_id, _ = _extract_identifiers(segments)
        amostra_id = barcode if barcode else sample_id
        if not amostra_id:
            amostra_id = "DESCONHECIDO"

        resultados: Dict[str, Tuple[str, str, str]] = {}
        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue
            if fields[2] == 'ED':
                continue

            test_name = fields[4].strip() if len(fields) > 4 and fields[4] else ""
            key = test_name if test_name else (
                fields[3].split('^')[0] if len(fields) > 3 and fields[3] else "?"
            )
            value = fields[5] if len(fields) > 5 else ""
            unit = fields[6] if len(fields) > 6 else ""
            flag = fields[8] if len(fields) > 8 and fields[8] and fields[8] != 'N' else ""
            resultados[key] = (value, unit, flag)

        if not resultados:
            return ""

        lines = [f"FileName: {amostra_id}"]
        for key, (val, uni, flg) in sorted(resultados.items()):
            linha = f"{key}: {val}"
            if uni:
                linha += f" {uni}"
            if flg:
                linha += f" ({flg})"
            lines.append(linha)
        return "\n".join(lines)
    except Exception as e:
        logging.error(f"Erro ao converter HL7: {e}")
        return ""


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorVIDAS1600(BaseAnalisador):
    """Analisador para equipamento VIDAS 1600 (bioMérieux) via HL7/MLLP."""

    def get_file_extension(self) -> str:
        return '.hl7'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta mensagem HL7 completa delimitada por SB e EB+CR.
        Filtra mensagens que não são ORU^R01 (ex: QRY, QCK, DSR).
        Filtra mensagens de calibração/QC (MSH-16 != 0).
        """
        while SB in buffer and EB in buffer:
            start = buffer.index(SB)
            end = buffer.index(EB, start)
            if end + 1 < len(buffer) and buffer[end + 1] == CR:
                message = buffer[start:end + 2]
                buffer = buffer[end + 2:]

                # Filtra mensagens não-ORU
                clean = message.replace(SB, '').replace(EB, '')
                clean = clean.replace('\r\n', '\n').replace('\r', '\n')
                segments = clean.split('\n')
                msh = next((s for s in segments if s.startswith('MSH')), "")

                if msh:
                    fields = msh.split('|')
                    msg_type = fields[8] if len(fields) > 8 else ""
                    if 'ORU' not in msg_type.upper():
                        logging.debug(
                            f"Mensagem não-ORU ignorada: tipo={msg_type}"
                        )
                        continue  # descarta, procura próxima

                    # MSH-16: 0=paciente, 1=calibração, 2=controle
                    app_ack_type = fields[15] if len(fields) > 15 else "0"
                    if app_ack_type != "0":
                        logging.info(
                            f"Mensagem de calibração/QC ignorada "
                            f"(MSH-16={app_ack_type})"
                        )
                        continue

                return message, buffer
            else:
                return None, buffer
        return None, buffer

    def generate_ack(self, raw_message: str) -> Optional[bytes]:
        """Gera ACK HL7 dinâmico (inverte MSH-3/4/5 da mensagem original)."""
        try:
            segments = _clean_hl7(raw_message)
            msh = next((s for s in segments if s.startswith('MSH')), "")
            if not msh:
                return None

            fields = msh.split('|')
            sending_app = fields[2] if len(fields) > 2 else "E-LAB"
            sending_fac = fields[3] if len(fields) > 3 else "ES-480"
            receiving_app = fields[4] if len(fields) > 4 else "LIS"
            receiving_fac = fields[5] if len(fields) > 5 else ""
            msg_id = fields[9] if len(fields) > 9 else ""
            app_ack_type = fields[15] if len(fields) > 15 else "0"
            dt_now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

            ack = (
                f"MSH|^~\\&|{receiving_app}|{receiving_fac}|{sending_app}|"
                f"{sending_fac}|{dt_now}||ACK^R01|{msg_id}|P|2.3.1||||"
                f"{app_ack_type}||UNICODE||{CR}"
                f"MSA|AA|{msg_id}|Message Accepted|||0{CR}"
            )
            return (SB + ack + EB + CR).encode('utf-8')
        except Exception as e:
            logging.error(f"Erro ao gerar ACK: {e}")
            return None

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa arquivo .hl7: extrai imagens, parse, filtra QC,
        retorna um payload por ExamCode.
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

        # Parse HL7
        campos = parse_hl7_to_dict(conteudo_hl7)
        if not campos:
            logging.warning(f"Arquivo {nome_arquivo} NÃO contém dados válidos.")
            return None

        # Verifica FileName
        if not campos.get('FileName') or campos.get('FileName') == 'DESCONHECIDO':
            logging.error(
                f"✗ Arquivo {nome_arquivo}: FileName (barcode) não encontrado."
            )
            logging.error(
                f"  Movendo para '{self.REQUISICOES_NAO_ENVIADAS_DIR}'."
            )
            return []

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

        # Salva JSON debug
        try:
            pasta_json = os.path.join(self.ENVIADOS_DIR, "json")
            os.makedirs(pasta_json, exist_ok=True)
            nome_json = os.path.splitext(nome_arquivo)[0] + ".json"
            with open(os.path.join(pasta_json, nome_json), "w", encoding="utf-8") as f:
                json.dump(campos, f, indent=2, ensure_ascii=False)
            logging.info(f"JSON salvo: {nome_json}")
        except Exception as e:
            logging.warning(f"Erro ao salvar JSON: {e}")

        # Monta payloads por ExamCode
        results_by_exam = campos.get('results_by_exam', {})
        total_tests = sum(len(tests) for tests in results_by_exam.values())
        logging.info(
            f"Arquivo {nome_arquivo}: {total_tests} teste(s) em "
            f"{len(results_by_exam)} exame(s) (barcode: {campos.get('FileName')})"
        )

        if not results_by_exam:
            # Fallback legado
            results_by_exam = {
                'QUIM': {
                    k: v for k, v in campos.items()
                    if k not in ('FileName', 'ExamCode', 'results_by_exam')
                }
            }

        payloads = []
        for exam_code, exam_tests in results_by_exam.items():
            if not exam_tests:
                continue
            payload = {
                'FileName': campos.get('FileName', 'DESCONHECIDO'),
                'ExamCode': exam_code,
            }
            payload.update(exam_tests)
            payloads.append(payload)

        return payloads if payloads else []


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorVIDAS1600(
        machine_id='vidas1600',
        machine_name='VIDAS1600',
        config_defaults={
            'com_port': 'COM5',
            'baud_rate': 9600,
            'franchise_credential_id': '85361c80-1a2b-3c4d-5e6f-7a8b9c0d1e2f',
            'webhook_url': (
                'https://apoio.internal.vidaexame.com/api/integration/vidas1600'
                '?franchise_credential_id={franchise_credential_id}'
            ),
        },
        health_port=8084,
    ).start()
