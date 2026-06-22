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
import datetime
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
        result['ExamCode'] = 'COAGU'

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
        return '.log'

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

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Processa arquivo .log: separa exames, parseia cada um, filtra falhas.
        Retorna lista de payloads (um por exame válido).
        """
        # Lê o arquivo
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                conteudo_log = f.read()
        except PermissionError:
            logging.error(f"✗ Permissão negada ao ler: {nome_arquivo}")
            return None
        except FileNotFoundError:
            logging.warning(f"Arquivo {nome_arquivo} não encontrado.")
            return None
        except Exception as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        if not conteudo_log or not conteudo_log.strip():
            logging.warning(f"Arquivo {nome_arquivo} está vazio.")
            return None

        # Separa exames individuais
        exames = split_exams_from_log(conteudo_log)
        if not exames:
            logging.warning(f"Nenhum exame encontrado em {nome_arquivo}")
            return None

        logging.info(f"Encontrados {len(exames)} exame(s) em {nome_arquivo}")

        payloads: List[Dict[str, Any]] = []
        pasta_json = os.path.join(self.ENVIADOS_DIR, "txt")
        os.makedirs(pasta_json, exist_ok=True)

        for i, exame_texto in enumerate(exames, 1):
            payload = parse_coagmaster_exam(exame_texto)
            if not payload:
                logging.warning(f"Exame {i} inválido em {nome_arquivo}, ignorando.")
                continue

            # Verifica FileName (tag_identifier)
            if not payload.get('FileName'):
                logging.error(
                    f"✗ Exame {i} de {nome_arquivo}: FileName vazio — "
                    f"impossível identificar o procedimento."
                )
                continue

            # Exame com falha (TEMPO: FALHOU!) — não envia
            if payload.get('Status') == 'FAILED':
                logging.warning(
                    f"⚠ Exame {i} de {nome_arquivo} "
                    f"(tag: {payload.get('FileName')}) FALHOU — não será enviado."
                )
                # Salva JSON de falha na pasta de não enviados
                pasta_falha = os.path.join(self.REQUISICOES_NAO_ENVIADAS_DIR, "txt")
                os.makedirs(pasta_falha, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                nome_falha = f"{os.path.splitext(nome_arquivo)[0]}_exame_{i}_{ts}.json"
                try:
                    with open(os.path.join(pasta_falha, nome_falha), "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2, ensure_ascii=False)
                    logging.info(f"  JSON de falha salvo: {nome_falha}")
                except Exception as e:
                    logging.warning(f"  Erro ao salvar JSON de falha: {e}")
                continue

            # Salva JSON de referência
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            nome_json = f"{os.path.splitext(nome_arquivo)[0]}_exame_{i}_{ts}.json"
            try:
                with open(os.path.join(pasta_json, nome_json), "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                logging.info(f"JSON salvo: {nome_json}")
            except Exception as e:
                logging.warning(f"Erro ao salvar JSON: {e}")

            payloads.append(payload)

        return payloads if payloads else []


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
