"""
Analisador Coagmaster — Integração de log proprietário via porta serial ou TCP.

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

# Teste impresso pela máquina ("Exame: XX") → exame principal no sistema.
# TT (Tempo de Trombina) e Hep (Heparina) não têm exame no sistema; FIB
# (FIBRI) existe, mas sem atributo. O lab-apoio recusa esses três com 400 e o
# motivo fica no log e na tentativa de integração.
EXAM_CODE_MAP = {
    'TP': 'TAP',
    'TTPA': 'KPTT',
    'APTT': 'KPTT',
    'FIB': 'FIBRI',
    'FIBRINOGENIO': 'FIBRI',
}

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

        # Os valores ficam como a máquina imprime: a vírgula é o separador
        # decimal no sistema (ponto nunca é decimal).

        # Relação: RELAÇÃO: X,XX (o lab-apoio não tem atributo para ela)
        match = re.search(r'RELA[CÇ][AÃ]O:\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['Relation'] = match.group(1)

        # Porcentagem (sem o "%"). Formato do manual (p. 24): "%:   81,4 %".
        # Também aceita a linha "%" seguida de "81,4%" (simulador antigo).
        for line in lines:
            stripped = line.strip()
            pct_match = re.match(r'^%\s*:?\s*([\d,\.]+)\s*%$', stripped)
            if not pct_match:
                pct_match = re.match(r'^([\d,\.]+)\s*%$', stripped)
            if pct_match:
                result['Percentage'] = pct_match.group(1)
                break

        # INR
        match = re.search(r'INR\s*:?\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['INR'] = match.group(1)

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

        # FileName = etiqueta do tubo (ID impresso pela máquina). Sem ID não há
        # como achar o procedimento: o número do exame (NNNN) não é etiqueta.
        result['FileName'] = result.get('PatientID', '')

        # ExamCode é só informativo (vai para a tentativa de integração): o
        # lab-apoio escolhe os procedimentos da etiqueta pelo ExamType, porque
        # o mesmo tubo pode atender TAP, KPTT e COAGU.
        exam_type = result.get('ExamType', '')
        result['ExamCode'] = EXAM_CODE_MAP.get(exam_type, exam_type)

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
        separa os exames e salva um arquivo .txt por teste em gerados/.

        A mesma etiqueta recebe vários testes (TP e TTPA do mesmo tubo), então o
        nome do arquivo leva o teste e o número do exame, e nunca sobrescreve um
        arquivo ainda não enviado. A etiqueta vai no FileName do payload.
        """
        logging.info(f"✓ Dados recebidos da Coagmaster ({len(raw_message)} caracteres)")

        exames = split_exams_from_log(raw_message)
        if not exames:
            logging.warning("✗ Nenhum exame encontrado no texto recebido.")
            return

        logging.info(f"Encontrados {len(exames)} exame(s) no texto recebido.")

        for i, exame_texto in enumerate(exames, 1):
            payload = parse_coagmaster_exam(exame_texto)
            if not payload:
                logging.warning(f"✗ Exame {i}: não foi possível ler o texto, ignorado.")
                continue

            teste = payload.get('ExamType', '?')
            numero = payload.get('ExamNumber', '?')
            tag = payload.get('FileName', '')
            if not tag:
                logging.error(
                    f"✗ Exame {i} (nº {numero}, teste {teste}): sem ID/etiqueta no texto da máquina, ignorado."
                )
                continue

            # Exames com falha não são salvos para envio
            if payload.get('Status') == 'FAILED':
                logging.warning(f"✗ Exame {i} (etiqueta {tag}, teste {teste}): FALHOU na máquina, ignorado.")
                continue

            filepath = self._caminho_unico(tag, teste, numero)
            filename = os.path.basename(filepath)

            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                logging.info(
                    f"✓ Etiqueta {tag}, teste {teste} (ExamCode {payload.get('ExamCode')}): "
                    f"arquivo '{filename}' criado para envio."
                )
            except OSError as e:
                logging.error(f"✗ Etiqueta {tag}, teste {teste}: erro ao salvar {filename}: {e}")

    def _caminho_unico(self, tag: str, teste: str, numero: str) -> str:
        """gerados/<etiqueta>_<teste>_<nº do exame>.txt, com sufixo se já existir."""
        base = re.sub(r'[^\w\-]', '_', f"{tag}_{teste}_{numero}")
        caminho = os.path.join(self.GERADOS_DIR, f"{base}.txt")
        sufixo = 2
        while os.path.exists(caminho):
            caminho = os.path.join(self.GERADOS_DIR, f"{base}_{sufixo}.txt")
            sufixo += 1
        return caminho

    def _on_webhook_response(self, resp: Any, nome_arquivo: str, tag_identifier: str) -> None:
        """Registra o que o lab-apoio gravou e o que ficou de fora."""
        try:
            corpo = resp.json()
        except ValueError:
            corpo = None
        mensagem = str(corpo.get("message") or "") if isinstance(corpo, dict) else resp.text[:300]
        dados = corpo.get("data") if isinstance(corpo, dict) else None

        if resp.status_code not in (200, 201):
            logging.error(
                f"✗ lab-apoio respondeu {resp.status_code} para a etiqueta {tag_identifier} "
                f"({nome_arquivo}): {mensagem or resp.text[:300]}"
            )
            return
        if not isinstance(dados, dict):
            return

        for item in dados.get("saved") or []:
            if isinstance(item, dict):
                logging.info(
                    f"✓ Gravado no laudo: etiqueta {tag_identifier}, exame {item.get('exam_code')}, "
                    f"{item.get('code')}={item.get('value')}."
                )
        ignorados = dados.get("ignored") or []
        if ignorados:
            logging.warning(
                f"Itens da etiqueta {tag_identifier} NÃO gravados: {', '.join(map(str, ignorados))}. "
                f"Motivo provável: o exame não tem esse atributo cadastrado."
            )

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
# Lidos também pelo leitor universal (universal/leitor.py), que abre a máquina
# com "leitor.exe --maquina coagmaster" e mostra o config no painel.

MACHINE_ID = 'coagmaster'
MACHINE_NAME = 'Coagmaster'
HEALTH_PORT = 8081
CONFIG_DEFAULTS = {
    # Conexão: "serial" (COM) ou "tcp" (a máquina conecta neste PC em tcp_host:tcp_port).
    'modo': 'serial',
    'com_port': 'COM4',
    'baud_rate': 115200,
    'tcp_host': '0.0.0.0',
    'tcp_port': 5200,
    'franchise_credential_id': 'f47d9a16-1a3c-4c2e-9e5f-6b3c8d7e9f0a',
    'webhook_url': (
        'https://apoio.internal.vidaexame.com/api/integration/coagmaster'
        '?franchise_credential_id={franchise_credential_id}'
    ),
}


def criar_analisador() -> AnalisadorCoagmaster:
    """Analisador com a configuração de produção (config_coagmaster.json ao lado do .exe)."""
    return AnalisadorCoagmaster(
        machine_id=MACHINE_ID,
        machine_name=MACHINE_NAME,
        config_defaults=CONFIG_DEFAULTS,
        health_port=HEALTH_PORT,
        console_logging=True,
        redirect_stdout=False,
    )


if __name__ == "__main__":
    criar_analisador().start()
