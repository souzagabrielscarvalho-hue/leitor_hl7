"""
Analisador Coagmaster — Integração de log proprietário via porta serial.

Herda de BaseAnalisador e implementa apenas os hooks específicos
do protocolo de texto do Coagmaster.
"""

import os
import sys
import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE PARSE (Coagmaster-specific)
# ═══════════════════════════════════════════════════════════

def split_exams_from_log(content: str) -> List[str]:
    """
    Separa os exames individuais de um arquivo de log do Coagmaster.
    Cada exame é identificado pelo padrão (NNNN) no início.
    """
    exams: List[str] = []

    # Remove cabeçalhos PuTTY
    content = re.sub(
        r'=~=~=~=~=~=~=~=~=~=~=~= PuTTY log .*?=~=~=~=~=~=~=~=~=~=~=~=\n?',
        '', content
    )

    lines = content.split('\n')
    current_exam: List[str] = []
    exam_started = False

    for line in lines:
        if re.match(r'^\s*\(\d+\)', line):
            if current_exam and exam_started:
                exam_text = '\n'.join(current_exam).strip()
                if exam_text:
                    exams.append(exam_text)
            current_exam = [line]
            exam_started = True
        elif exam_started:
            current_exam.append(line)

    if current_exam and exam_started:
        exam_text = '\n'.join(current_exam).strip()
        if exam_text:
            exams.append(exam_text)

    return exams


def parse_coagmaster_exam(text: str) -> Dict[str, str]:
    """
    Extrai os dados de um exame do Coagmaster e retorna um dicionário.
    """
    result: Dict[str, str] = {}

    try:
        lines = text.split('\n')

        # Número do exame: (NNNN)
        match = re.search(r'\((\d+)\)', text)
        if match:
            result['ExamNumber'] = match.group(1)

        # Data: DD/MM/YYYY
        match = re.search(r'(\d{2}/\d{2}/\d{4})', text)
        if match:
            result['Date'] = match.group(1)

        # Canal: CANAL N
        match = re.search(r'CANAL\s*(\d+)', text, re.IGNORECASE)
        if match:
            result['Channel'] = match.group(1)

        # Hora: HH:MM:SS
        match = re.search(r'(\d{2}:\d{2}:\d{2})', text)
        if match:
            result['Time'] = match.group(1)

        # Nome do paciente: NOME: ...
        match = re.search(r'NOME:\s*(.+)', text, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            if nome and not re.match(
                r'^(EXAME|CANAL|TEMPO|RELA)', nome, re.IGNORECASE
            ):
                result['PatientName'] = nome

        # Código do exame: Exame: XX
        match = re.search(r'Exame:\s*(\S+)', text, re.IGNORECASE)
        if match:
            result['ExamType'] = match.group(1).upper()

        # Descrição do exame (linha seguinte ao código)
        for i, line in enumerate(lines):
            if re.match(r'^\s*Exame:', line, re.IGNORECASE):
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    if not next_line or next_line == '%' or re.match(r'^[\d,]+%$', next_line):
                        continue
                    if re.match(
                        r'^(TEMPO|RELA[CÇ][AÃ]O|INR|CONTROLE|ID|OPERADOR|CANAL|NOME|N\.\s*SERIE)',
                        next_line, re.IGNORECASE
                    ) and ':' in next_line:
                        continue
                    if re.match(r'^ID\(', next_line, re.IGNORECASE):
                        continue
                    if re.match(r'^\d{2}/\d{2}/\d{4}', next_line):
                        continue
                    if re.match(r'^\d{2}:\d{2}:\d{2}', next_line):
                        continue
                    if re.match(r'^\*{10,}', next_line):
                        continue
                    result['ExamDescription'] = next_line
                    break
                break

        # Tempo medido: TEMPO: XX,X s ou TEMPO: FALHOU!
        match = re.search(r'TEMPO:\s*(.+)', text, re.IGNORECASE)
        if match:
            result['TimeValue'] = match.group(1).strip()

        # Relação: RELAÇÃO: X.XX
        match = re.search(r'RELA[CÇ][AÃ]O:\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['Relation'] = match.group(1).replace(',', '.')

        # Porcentagem
        for line in lines:
            stripped = line.strip()
            pct_match = re.match(r'^%\s*:\s*([\d,\.]+)%', stripped)
            if pct_match:
                result['Percentage'] = pct_match.group(1).replace(',', '.')
                break
            if re.match(r'^[\d,]+%$', stripped):
                result['Percentage'] = stripped.replace(',', '.')
                break

        # INR
        match = re.search(r'INR\s*:?\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['INR'] = match.group(1).replace(',', '.')

        # Controle
        match = re.search(r'CONTROLE[^:]*:\s*([\d,\.]+\s*s?)', text, re.IGNORECASE)
        if match:
            result['Control'] = match.group(1).strip()

        # Concentração
        match = re.search(
            r'CONCENTRA[CÇ][AÃ]O:\s*([\d,\.]+\s*(?:mg/dL|g/L|%)?)',
            text, re.IGNORECASE
        )
        if match:
            result['Concentration'] = match.group(1).strip()

        # ID do paciente
        match = re.search(r'ID\(([^)]*)\)', text)
        if match:
            patient_id = match.group(1).strip()
            if patient_id:
                result['PatientID'] = patient_id

        # Operador
        match = re.search(r'OPERADOR\s*\(([^)]+)\)', text, re.IGNORECASE)
        if match:
            result['Operator'] = match.group(1)

        # Número de série
        match = re.search(r'N\.\s*SERIE\(([^)]+)\)', text, re.IGNORECASE)
        if match:
            result['SerialNumber'] = match.group(1)

        # Laboratório (cabeçalho)
        for i, line in enumerate(lines):
            if re.match(r'^\s*\(\d+\)', line):
                if i > 0:
                    prev_line = lines[i - 1].strip()
                    if (
                        prev_line
                        and not re.match(r'^[\(\d]', prev_line)
                        and not re.match(r'^\*{10,}', prev_line)
                    ):
                        result['Laboratory'] = prev_line
                break

        # Campos obrigatórios para o webhook
        result['FileName'] = result.get('PatientID', '') or result.get('ExamNumber', '')

        # Mapeia ExamType → ExamCode (código real do exame no banco)
        exam_type = result.get('ExamType', '')
        exam_code_map = {
            'TP': 'TAP',           # TEMPO DE PROTROMBINA
            'TTPA': 'KPTT',        # TEMPO DE TROMBOPLASTINA PARCIAL ATIVADO
            'APTT': 'KPTT',        # TEMPO DE TROMBOPLASTINA PARCIAL ATIVADO
            'FIB': 'FIBRI',        # FIBRINOGÊNIO
            'FIBRINOGENIO': 'FIBRI',  # FIBRINOGÊNIO
            'TT': 'TCO',           # TEMPO DE COAGULAÇÃO (TROMBINA)
            'TROMBINA': 'TCO',     # TEMPO DE COAGULAÇÃO (TROMBINA)
            'COAG': 'COAG',      # ← adicionado
            'COAGU': 'COAGU',
        }
        result['ExamCode'] = exam_code_map.get(exam_type, 'COAGU')
        if exam_type and exam_type not in exam_code_map:
            logging.warning(
                f"Tipo de exame desconhecido: '{exam_type}' — "
                f"usando fallback 'COAGU'. Verifique se o código existe no banco."
            )

        if 'FALHOU' in text.upper():
            result['Status'] = 'FAILED'
        else:
            result['Status'] = 'SUCCESS'

    except Exception as e:
        logging.error(f"Erro ao parsear exame: {e}")
        return {}

    return result


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorCoagmaster(BaseAnalisador):
    """Analisador para equipamento Coagmaster via log proprietário."""

    def get_file_extension(self) -> str:
        return '.txt'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Detecta fim de exame no buffer do Coagmaster.
        O Coagmaster envia linhas de texto contínuas. O fim de um ciclo de
        exames é detectado por uma linha de asteriscos (**********...) seguida
        de linha(s) em branco, ou por um cabeçalho PuTTY.
        """
        # Detecta cabeçalho PuTTY como início de nova sessão
        putty_match = re.search(
            r'=~=~=~=~=~=~=~=~=~=~=~= PuTTY log \d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2} =~=~=~=~=~=~=~=~=~=~=~=',
            buffer
        )
        if putty_match:
            end = putty_match.end()
            message = buffer[:end]
            buffer = buffer[end:]
            if message.strip():
                return message, buffer
            return None, buffer

        # Detecta bloco de asteriscos + linhas em branco como fim de exame
        asterisk_match = re.search(r'(\*{30,}\s*\n\s*\n)', buffer)
        if asterisk_match:
            end = asterisk_match.end()
            message = buffer[:end]
            buffer = buffer[end:]
            if message.strip():
                return message, buffer
            return None, buffer

        return None, buffer

    def _on_serial_message(self, raw_message: str) -> None:
        """
        Sobrescreve o comportamento padrão: processa o log recebido,
        separa os exames e salva um arquivo .txt por exame em gerados/.
        Nome do arquivo = tag_identifier (barcode), igual ao MEK7300.
        """
        logging.info(f"Dados completos recebidos ({len(raw_message)} caracteres)")

        exames = split_exams_from_log(raw_message)
        if not exames:
            logging.warning("Nenhum exame encontrado no log recebido.")
            return

        logging.info(f"Encontrados {len(exames)} exame(s) no log.")

        for i, exame_texto in enumerate(exames, 1):
            payload = parse_coagmaster_exam(exame_texto)
            if not payload:
                logging.warning(f"Exame {i} inválido, ignorando.")
                continue

            tag = payload.get('FileName', '')
            if not tag:
                logging.error(f"Exame {i}: FileName vazio, ignorando.")
                continue

            # Exames com falha não são salvos para envio
            if payload.get('Status') == 'FAILED':
                logging.warning(f"Exame {i} (tag: {tag}) FALHOU — ignorado.")
                continue

            # Salva como .txt (JSON internamente) — nome = barcode
            filename = f"{tag}.txt"
            filepath = os.path.join(self.GERADOS_DIR, filename)

            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                logging.info(f"Arquivo '{filename}' criado com sucesso.")
            except OSError as e:
                logging.error(f"Erro ao salvar {filename}: {e}")

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa arquivo .txt: lê o JSON e retorna o payload.
        Formato simples, igual ao MEK7300.
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
            logging.error(f"✗ Erro ao decodificar JSON de {nome_arquivo}: {e}")
            return None
        except Exception as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        if not payload or not payload.get('FileName'):
            logging.error(
                f"✗ {nome_arquivo}: FileName (tag_identifier) não encontrado."
            )
            return []

        return [payload]


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorCoagmaster(
        machine_id='coagmaster',
        machine_name='Coagmaster',
        config_defaults={
            'com_port': 'COM4',
            'baud_rate': 115200,
            'franchise_credential_id': 'f47d9a16-1a3c-4c2e-9e5f-6b3c8d7e9f0a',
            'webhook_url': (
                'https://apoio.internal.vidaexame.com/api/integration/coagmaster'
                '?franchise_credential_id={franchise_credential_id}'
            ),
        },
        health_port=8081,
        console_logging=True,
        redirect_stdout=False,
    ).start()
