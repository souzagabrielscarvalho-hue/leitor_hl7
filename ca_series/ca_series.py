"""
Analisador CA Series (URIT) — Integração HL7 via porta serial (bioquímica).

Herda de BaseAnalisador e implementa os hooks do protocolo descrito no
"CA Series Automatic Chemistry Analyzer HL7 Interface Development Reference
Manual" V1.01 (2023.03):

- HL7 v2.3.1, enquadrado em MLLP: <SB>=0x0B ... <EB>=0x1C <CR>=0x0D.
- O analisador é o "cliente" e o LIS (este programa) é o "servidor".

Mensagens tratadas:

| Recebida (analisador)       | Resposta (LIS)       | O que este leitor faz          |
|-----------------------------|----------------------|--------------------------------|
| ORU^R01 com MSH.16 = 0      | ACK^R01              | salva em gerados/ → webhook    |
| ORU^R01 com MSH.16 = 2 (CQ) | ACK^R01              | guarda em enviados/qc/, não envia |
| QRY^Q02 (pede a amostra)    | QCK^Q02 (QAK|SR|NF)  | só registra: o LIS não envia ordens |
| ACK^Q03                     | —                    | só registra                    |

Campos usados (manual, seções 3.3 a 3.5):
- OBR.2 = código de barras, OBR.3 = Sample ID, OBR.7 = data do teste,
  OBR.15 = tipo de amostra; PID.26 = rack, PID.27 = posição do tubo.
- OBX.3 = nº do item, OBX.4 = nome do item, OBX.5 = resultado,
  OBX.6 = unidade, OBX.7 = faixa de referência, OBX.8 = flag (N = normal),
  OBX.13 = absorbância, OBX.14 = data do teste.
"""

import os
import sys
import json
import logging
import datetime
from typing import Any, Dict, List, Optional, Tuple

# Adiciona raiz do projeto ao path para importar shared
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.base_analisador import BaseAnalisador

# ═══════════════════════════════════════════════════════════
# CONSTANTES DE PROTOCOLO HL7/MLLP (manual, seção 4)
# ═══════════════════════════════════════════════════════════

SB = chr(0x0B)   # Start Block
EB = chr(0x1C)   # End Block
CR = chr(0x0D)   # Carriage Return

# MSH.16 (manual, seção 3.1): tipo de resultado enviado
TIPO_PACIENTE = "0"
TIPO_QC = "2"

# Charset declarado pelo analisador em MSH.18 ("ANSI"). Usado ao codificar
# as respostas; o conteúdo delas é ASCII puro.
ENCODING_RESPOSTA = "latin-1"

# ═══════════════════════════════════════════════════════════
# MAPEAMENTO DE TESTES → EXAM_CODE
# ═══════════════════════════════════════════════════════════
# O nome do item (OBX.4) é o configurado no software do analisador; os
# abaixo são as siglas usuais da URIT (as do manual: ALB, DB, ALP, TB, TP,
# AST, ALT, A/G, ALT/AST, GLB, IBIL). A comparação ignora maiúsculas.
# Itens fora desta tabela vão para QUIM, como no VIDAS 1600.

TEST_TO_EXAM_CODE = {
    # Função Hepática (HEP)
    'ALT': 'HEP', 'GPT': 'HEP', 'AST': 'HEP', 'GOT': 'HEP',
    'ALT/AST': 'HEP', 'AST/ALT': 'HEP',
    'TB': 'HEP', 'TBIL': 'HEP', 'DB': 'HEP', 'DBIL': 'HEP', 'IBIL': 'HEP',
    'ALP': 'HEP', 'GGT': 'HEP', 'ALB': 'HEP', 'TP': 'HEP', 'GLB': 'HEP',
    'A/G': 'HEP', 'CHE': 'HEP', 'TBA': 'HEP',
    # Função Renal (REN)
    'UREA': 'REN', 'BUN': 'REN', 'CREA': 'REN', 'CRE': 'REN',
    'UA': 'REN', 'CYSC': 'REN',
    # Glicose (GLI)
    'GLU': 'GLI', 'HBA1C': 'GLI', 'FRU': 'GLI',
    # Lipídios (LIP)
    'TC': 'LIP', 'CHOL': 'LIP', 'TG': 'LIP',
    'HDL': 'LIP', 'HDL-C': 'LIP', 'LDL': 'LIP', 'LDL-C': 'LIP',
    'APOA1': 'LIP', 'APOB': 'LIP', 'LP(A)': 'LIP',
    # Eletrólitos (ELE)
    'CA': 'ELE', 'P': 'ELE', 'PHOS': 'ELE', 'MG': 'ELE',
    'CL': 'ELE', 'NA': 'ELE', 'K': 'ELE',
    # Marcadores Cardíacos (CARD)
    'CK': 'CARD', 'CK-MB': 'CARD', 'CKMB': 'CARD', 'LDH': 'CARD',
    'HBDH': 'CARD', 'CRP': 'CARD', 'HS-CRP': 'CARD', 'HSCRP': 'CARD',
    # Amilase/Lipase (AML)
    'AMY': 'AML', 'LPS': 'AML', 'LIP': 'AML',
    # Ferro/Anemia (FER)
    'FE': 'FER', 'TIBC': 'FER', 'UIBC': 'FER', 'FER': 'FER',
    # Outros (OUT)
    'ASO': 'OUT', 'RF': 'OUT', 'LAC': 'OUT', 'MALB': 'OUT', 'UPRO': 'OUT',
}


# ═══════════════════════════════════════════════════════════
# FUNÇÕES DE PARSE HL7
# ═══════════════════════════════════════════════════════════

def _clean_hl7(message: str) -> List[str]:
    """Remove os caracteres MLLP e devolve os segmentos não vazios."""
    clean = message.replace(SB, '').replace(EB, '')
    clean = clean.replace('\r\n', '\n').replace('\r', '\n')
    return [s for s in clean.split('\n') if s.strip()]


def _campo(fields: List[str], idx: int) -> str:
    return fields[idx].strip() if len(fields) > idx and fields[idx] else ""


def _segmento(segments: List[str], nome: str) -> List[str]:
    """Campos do primeiro segmento com esse nome (lista vazia se não houver)."""
    for seg in segments:
        fields = seg.split('|')
        if fields[0] == nome:
            return fields
    return []


def ler_msh(message: str) -> Dict[str, str]:
    """
    Campos do MSH usados no roteamento e nas respostas.

    Com o MSH, a posição na lista é o número do campo menos 1, porque
    MSH.1 é o próprio separador: fields[8] = MSH.9.
    """
    fields = _segmento(_clean_hl7(message), 'MSH')
    return {
        'sending_app': _campo(fields, 2),     # MSH.3
        'sending_facility': _campo(fields, 3),  # MSH.4
        'message_type': _campo(fields, 8),    # MSH.9
        'control_id': _campo(fields, 9),      # MSH.10
        'result_type': _campo(fields, 15),    # MSH.16
    }


def _identificadores(segments: List[str]) -> Dict[str, str]:
    obr = _segmento(segments, 'OBR')
    pid = _segmento(segments, 'PID')
    return {
        'barcode': _campo(obr, 2),        # OBR.2
        'sample_id': _campo(obr, 3),      # OBR.3
        'test_date': _campo(obr, 7),      # OBR.7
        'sample_type': _campo(obr, 15),   # OBR.15
        'rack': _campo(pid, 26),          # PID.26
        'tube': _campo(pid, 27),          # PID.27
    }


def _ler_obx(segments: List[str]) -> List[Dict[str, str]]:
    """Itens dos OBX, na ordem em que vieram. Ignora itens sem resultado."""
    itens: List[Dict[str, str]] = []
    for seg in segments:
        fields = seg.split('|')
        if fields[0] != 'OBX':
            continue
        nome = _campo(fields, 4) or _campo(fields, 3).split('^')[0]
        valor = _campo(fields, 5)
        if not nome or not valor:
            continue
        flag = _campo(fields, 8)
        itens.append({
            'nome': nome,
            'valor': valor,
            'flag': flag if flag and flag != 'N' else '',
            'unidade': _campo(fields, 6),
            'referencia': _campo(fields, 7),
            'absorbancia': _campo(fields, 13),
            'item_no': _campo(fields, 3),
        })
    return itens


def exam_code_do_teste(nome: str) -> Optional[str]:
    return TEST_TO_EXAM_CODE.get(nome.strip().upper())


def parse_hl7_to_dict(hl7_message: str) -> dict:
    """
    Extrai os resultados de um ORU^R01 de paciente.

    Retorna {'FileName', 'ExamCode', <teste>: <valor[flag]>, ...,
    'results_by_exam': {exam_code: {teste: valor}}} ou {} em caso de erro.
    """
    result: Dict[str, Any] = {}
    try:
        segments = _clean_hl7(hl7_message)
        ids = _identificadores(segments)

        result['FileName'] = ids['barcode'] or ids['sample_id'] or "DESCONHECIDO"
        result['ExamCode'] = 'QUIM'

        results_by_exam: Dict[str, Dict[str, str]] = {}
        nao_mapeados: List[str] = []
        for item in _ler_obx(segments):
            valor = f"{item['valor']}{item['flag']}"
            result[item['nome']] = valor
            exam_code = exam_code_do_teste(item['nome'])
            if exam_code is None:
                nao_mapeados.append(item['nome'])
                exam_code = 'QUIM'
            results_by_exam.setdefault(exam_code, {})[item['nome']] = valor

        result['results_by_exam'] = results_by_exam
        if nao_mapeados:
            logging.warning(f"Testes não mapeados (usando QUIM): {nao_mapeados}")
    except Exception as e:
        logging.error(f"Erro ao parsear HL7 do CA Series: {e}")
        return {}
    return result


def parse_hl7_to_txt(hl7_message: str) -> str:
    """Converte o ORU para TXT legível (debug)."""
    try:
        segments = _clean_hl7(hl7_message)
        ids = _identificadores(segments)
        itens = _ler_obx(segments)
        if not itens:
            return ""

        lines = [f"FileName: {ids['barcode'] or ids['sample_id'] or 'DESCONHECIDO'}"]
        if ids['sample_id']:
            lines.append(f"SampleID: {ids['sample_id']}")
        if ids['sample_type']:
            lines.append(f"Amostra: {ids['sample_type']}")
        if ids['test_date']:
            lines.append(f"Data: {ids['test_date']}")
        if ids['rack'] or ids['tube']:
            lines.append(f"Rack/Tubo: {ids['rack']}/{ids['tube']}")
        for item in itens:
            linha = f"{item['nome']}: {item['valor']}"
            if item['unidade']:
                linha += f" {item['unidade']}"
            if item['flag']:
                linha += f" ({item['flag']})"
            if item['referencia']:
                linha += f" [ref {item['referencia']}]"
            lines.append(linha)
        return "\n".join(lines)
    except Exception as e:
        logging.error(f"Erro ao converter HL7: {e}")
        return ""


# ═══════════════════════════════════════════════════════════
# RESPOSTAS DO LIS (manual, seções 4.1 a 4.3)
# ═══════════════════════════════════════════════════════════

def _agora() -> str:
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S")


def _msh_resposta(msh: Dict[str, str], tipo: str, result_type: str) -> str:
    """
    MSH da resposta. O manual repete MSH.3/MSH.4 da mensagem recebida
    (não inverte remetente e destinatário) e usa processing ID "P".
    """
    app = msh['sending_app'] or "URIT"
    fac = msh['sending_facility'] or "CA-XXX"
    return (
        f"MSH|^~\\&|{app}|{fac}|||{_agora()}||{tipo}|{msh['control_id']}|P|"
        f"2.3.1||||{result_type}||ANSI|||"
    )


def _mllp(segmentos: List[str]) -> bytes:
    corpo = CR.join(segmentos) + CR
    return (SB + corpo + EB + CR).encode(ENCODING_RESPOSTA, errors="replace")


def montar_ack_r01(raw_message: str) -> bytes:
    """ACK^R01 para um ORU^R01 (paciente ou CQ): MSH.16 igual ao recebido."""
    msh = ler_msh(raw_message)
    result_type = msh['result_type'] or TIPO_PACIENTE
    return _mllp([
        _msh_resposta(msh, "ACK^R01", result_type),
        f"MSA|AA|{msh['control_id']}|Message accepted|||0|",
    ])


def montar_qck_q02(raw_message: str, encontrou: bool = False) -> bytes:
    """
    QCK^Q02 para um QRY^Q02. Este LIS não devolve dados de amostra (DSR^Q03),
    então responde QAK|SR|NF ("no data found"); o analisador segue com as
    informações digitadas nele. Com encontrou=True responde QAK|SR|OK.
    """
    msh = ler_msh(raw_message)
    status = "OK" if encontrou else "NF"
    return _mllp([
        _msh_resposta(msh, "QCK^Q02", TIPO_PACIENTE),
        f"MSA|AA|{msh['control_id']}|Message accepted|||0|",
        "ERR|0|",
        f"QAK|SR|{status}|",
    ])


def barcode_da_consulta(raw_message: str) -> str:
    """QRD.8 (Who Subject Filter) = código de barras consultado."""
    qrd = _segmento(_clean_hl7(raw_message), 'QRD')
    return _campo(qrd, 8)


# ═══════════════════════════════════════════════════════════
# CLASSE DO ANALISADOR
# ═══════════════════════════════════════════════════════════

class AnalisadorCASeries(BaseAnalisador):
    """Analisador bioquímico URIT CA Series via HL7/MLLP."""

    def get_file_extension(self) -> str:
        return '.hl7'

    def detect_complete_message(self, buffer: str) -> Tuple[Optional[str], str]:
        """
        Extrai a próxima mensagem <SB> ... <EB><CR> do buffer.

        Não filtra por tipo: o roteamento (ORU, QRY, ACK^Q03) é feito em
        _on_serial_message, porque QRY e CQ também precisam de resposta.
        Lixo antes do <SB> é descartado.
        """
        while SB in buffer:
            start = buffer.index(SB)
            if start > 0:
                logging.debug(f"Descartados {start} byte(s) antes do <SB>.")
                buffer = buffer[start:]
                start = 0
            end = buffer.find(EB, start)
            if end < 0:
                return None, buffer
            if end + 1 >= len(buffer):
                return None, buffer  # falta o <CR> final
            message = buffer[start:end + 2] if buffer[end + 1] == CR else buffer[start:end + 1]
            buffer = buffer[len(message):]
            if 'MSH|' not in message:
                logging.warning("Bloco MLLP sem MSH ignorado.")
                continue
            return message, buffer
        return None, buffer

    def _on_serial_message(self, raw_message: str) -> None:
        msh = ler_msh(raw_message)
        tipo = msh['message_type'].upper()

        if tipo.startswith('ORU'):
            if msh['result_type'] in ('', TIPO_PACIENTE):
                logging.info(f"ORU^R01 de paciente recebido (MSH.10={msh['control_id']}).")
                super()._on_serial_message(raw_message)  # salva em gerados/ + ACK
            else:
                self._guardar_qc(raw_message, msh)
                self._send_ack(montar_ack_r01(raw_message))
            return

        if tipo.startswith('QRY'):
            barcode = barcode_da_consulta(raw_message)
            logging.info(
                f"QRY^Q02 recebido (barcode={barcode or '?'}): respondendo "
                f"QCK^Q02 com QAK|SR|NF (o LIS não envia ordens ao analisador)."
            )
            self._send_ack(montar_qck_q02(raw_message, encontrou=False))
            return

        if tipo.startswith('ACK'):
            logging.info(f"{tipo} recebido do analisador (MSH.10={msh['control_id']}).")
            return

        logging.warning(f"Mensagem HL7 de tipo não tratado ignorada: '{tipo}'.")

    def _guardar_qc(self, raw_message: str, msh: Dict[str, str]) -> None:
        """Resultado de CQ: confirma ao analisador, guarda e não envia ao webhook."""
        obr = _segmento(_clean_hl7(raw_message), 'OBR')
        logging.info(
            f"Resultado de CQ recebido (MSH.16={msh['result_type']}, "
            f"item={_campo(obr, 3)}, nível={_campo(obr, 17)}, "
            f"resultado={_campo(obr, 20)}): guardado, não enviado ao webhook."
        )
        try:
            pasta = os.path.join(self.ENVIADOS_DIR, "qc")
            os.makedirs(pasta, exist_ok=True)
            nome = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".hl7"
            with open(os.path.join(pasta, nome), "w", encoding="utf-8", newline="") as f:
                f.write(raw_message)
        except OSError as e:
            logging.warning(f"Erro ao guardar mensagem de CQ: {e}")

    def generate_ack(self, raw_message: str) -> Optional[bytes]:
        try:
            return montar_ack_r01(raw_message)
        except Exception as e:
            logging.error(f"Erro ao gerar ACK: {e}")
            return None

    def process_file(
        self, filepath: str, nome_arquivo: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Processa um .hl7 de gerados/ e retorna um payload por ExamCode."""
        try:
            with open(filepath, 'r', encoding='utf-8', newline='') as f:
                conteudo_hl7 = f.read()
        except FileNotFoundError:
            logging.warning(f"Arquivo {nome_arquivo} não encontrado.")
            return None
        except Exception as e:
            logging.error(f"✗ Erro ao ler {nome_arquivo}: {type(e).__name__}: {e}")
            return None

        if not conteudo_hl7.strip():
            logging.warning(f"Arquivo {nome_arquivo} está vazio.")
            return None

        campos = parse_hl7_to_dict(conteudo_hl7)
        if not campos:
            logging.warning(f"Arquivo {nome_arquivo} NÃO contém dados válidos.")
            return None

        if campos.get('FileName') == 'DESCONHECIDO':
            logging.error(
                f"✗ Arquivo {nome_arquivo}: sem código de barras (OBR.2) nem "
                f"Sample ID (OBR.3). Movendo para '{self.REQUISICOES_NAO_ENVIADAS_DIR}'."
            )
            return []

        base = os.path.splitext(nome_arquivo)[0]
        txt_data = parse_hl7_to_txt(conteudo_hl7)
        if txt_data:
            self._salvar_debug("txt", base + ".txt", txt_data)
        self._salvar_debug("json", base + ".json", json.dumps(campos, indent=2, ensure_ascii=False))

        results_by_exam = campos.get('results_by_exam', {})
        total = sum(len(t) for t in results_by_exam.values())
        logging.info(
            f"Arquivo {nome_arquivo}: {total} teste(s) em {len(results_by_exam)} "
            f"exame(s) (barcode: {campos['FileName']})"
        )

        payloads = []
        for exam_code, testes in results_by_exam.items():
            if not testes:
                continue
            payload = {'FileName': campos['FileName'], 'ExamCode': exam_code}
            payload.update(testes)
            payloads.append(payload)
        return payloads

    def _salvar_debug(self, subpasta: str, nome: str, conteudo: str) -> None:
        try:
            pasta = os.path.join(self.ENVIADOS_DIR, subpasta)
            os.makedirs(pasta, exist_ok=True)
            with open(os.path.join(pasta, nome), "w", encoding="utf-8") as f:
                f.write(conteudo)
        except OSError as e:
            logging.warning(f"Erro ao salvar {nome}: {e}")


# ═══════════════════════════════════════════════════════════
# PONTO DE ENTRADA
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    AnalisadorCASeries(
        machine_id='ca_series',
        machine_name='CASeries',
        config_defaults={
            'com_port': 'COM6',
            'baud_rate': 9600,
            'franchise_credential_id': '',
            'webhook_url': (
                'https://apoio.internal.vidaexame.com/api/integration/ca-series'
                '?franchise_credential_id={franchise_credential_id}'
            ),
        },
        health_port=8085,
    ).start()
