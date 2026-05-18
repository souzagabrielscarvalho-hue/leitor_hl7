import serial
import time
import requests
import logging
import datetime
import os
import shutil
import re
from threading import Thread
import json

# ================= CONFIGURAÇÕES =================
COM_PORT = 'COM5'
BAUD_RATE = 9600

# ID da franquia configurado no banco de dados
FRANCHISE_CREDENTIAL_ID = '85361c80-9688-47e9-8cb3-ed838a9b1832'

# Webhook do Coagmaster
# Local: http://localhost:8039/api/integration/coagmaster
# Servidor: http://IP_DO_SERVIDOR:8039/api/integration/coagmaster
WEBHOOK_URL = f'http://localhost:8039/api/integration/coagmaster?franchise_credential_id={FRANCHISE_CREDENTIAL_ID}'

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
# =================================================

# Pastas de trabalho – na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorCoagmaster")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
LOG_FILE = os.path.join(BASE_DIR, "analisador_coagmaster.log")
# =================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)


def split_exams_from_log(content: str) -> list[str]:
    """
    Separa os exames individuais de um arquivo de log do Coagmaster.
    
    O log pode conter múltiplos exames concatenados (append).
    Cada exame é identificado pelo padrão (NNNN) no início.
    
    Args:
        content: Conteúdo completo do arquivo de log
        
    Returns:
        Lista de blocos de texto, cada um contendo um exame
    """
    exams: list[str] = []
    
    # Remove cabeçalhos PuTTY
    # Padrão: ~=~=~=~=~=~=~=~=~=~=~=~= PuTTY log YYYY.MM.DD HH:MM:SS ~=~=~=~=~=~=~=~=~=~=~=~=
    content = re.sub(r'=~=~=~=~=~=~=~=~=~=~=~= PuTTY log .*?=~=~=~=~=~=~=~=~=~=~=~=\n?', '', content)
    
    # Divide por blocos de exame
    # Cada exame começa com um número entre parênteses: (0001), (0052), etc.
    # Padrão: linha com (NNNN) seguida de dados do exame
    
    lines = content.split('\n')
    current_exam = []
    exam_started = False
    
    for line in lines:
        # Detecta início de novo exame: linha contendo apenas (NNNN) ou (NNNN) no início
        if re.match(r'^\s*\(\d+\)', line) or re.match(r'^\(\d+\)', line):
            # Se já existe um exame em andamento, salva ele
            if current_exam and exam_started:
                exam_text = '\n'.join(current_exam).strip()
                if exam_text:
                    exams.append(exam_text)
            # Inicia novo exame
            current_exam = [line]
            exam_started = True
        elif exam_started:
            current_exam.append(line)
    
    # Adiciona o último exame se existir
    if current_exam and exam_started:
        exam_text = '\n'.join(current_exam).strip()
        if exam_text:
            exams.append(exam_text)
    
    return exams


def parse_coagmaster_exam(text: str) -> dict[str, str]:
    """
    Extrai os dados de um exame do Coagmaster e retorna um dicionário.
    
    Formato esperado (exemplo real):
        NOME DO LAB
        (0001)
        18/01/2018
        CANAL 1
        14:45:12
        NOME: Joao Pedro
        Exame: TP
        Tempo de Protrombina
        TEMPO: 16,6 s
        RELAÇÃO: 1.25
        %
        81,4%
        INR 1,28
        CONTROLE 100%: 14,2s
        ID(201801210001)
        OPERADOR (CARLOS)
    
    Args:
        text: Texto do exame extraído do log
        
    Returns:
        Dicionário com campos estruturados para o webhook
    """
    result: dict[str, str] = {}
    
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
            result['PatientName'] = match.group(1).strip()
        
        # Código do exame: Exame: XX
        match = re.search(r'Exame:\s*(\w+)', text, re.IGNORECASE)
        if match:
            result['ExamType'] = match.group(1).upper()
        
        # Descrição do exame (linha seguinte ao código)
        for i, line in enumerate(lines):
            if re.match(r'^\s*Exame:', line, re.IGNORECASE):
                # Próxima linha não vazia é a descrição
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    if next_line and not re.match(r'^(TEMPO|RELAÇÃO|INR|CONTROLE|ID|OPERADOR|CANAL|\d{2}/)', next_line):
                        result['ExamDescription'] = next_line
                        break
                break
        
        # Tempo medido: TEMPO: XX,X s
        match = re.search(r'TEMPO:\s*([\d,]+\s*s?)', text, re.IGNORECASE)
        if match:
            result['TimeValue'] = match.group(1).strip()
        
        # Relação: RELAÇÃO: X.XX ou X,XX
        match = re.search(r'RELAÇÃO:\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['Relation'] = match.group(1).replace(',', '.')
        
        # Porcentagem: XX,X%
        # Procura por linha que contém apenas porcentagem ou XX,X% isolado
        match = re.search(r'(?<!\d)([\d,]+%)(?!\d)', text)
        if match:
            result['Percentage'] = match.group(1).replace(',', '.')
        
        # INR: INR X,XX
        match = re.search(r'INR\s*([\d,]+)', text, re.IGNORECASE)
        if match:
            result['INR'] = match.group(1).replace(',', '.')
        
        # Controle: CONTROLE ...: XX,Xs
        match = re.search(r'CONTROLE[^:]*:\s*([\d,]+\s*s?)', text, re.IGNORECASE)
        if match:
            result['Control'] = match.group(1).strip()
        
        # ID do paciente: ID(...)
        match = re.search(r'ID\(([^)]+)\)', text)
        if match:
            result['PatientID'] = match.group(1)
        
        # Operador: OPERADOR (...)
        match = re.search(r'OPERADOR\s*\(([^)]+)\)', text, re.IGNORECASE)
        if match:
            result['Operator'] = match.group(1)
        
        # Número de série: N. SERIE(...)
        match = re.search(r'N\.\s*SERIE\(([^)]+)\)', text, re.IGNORECASE)
        if match:
            result['SerialNumber'] = match.group(1)
        
        # Laboratório (cabeçalho): primeira linha antes do número do exame
        # Procura a linha que vem antes da linha com (NNNN)
        for i, line in enumerate(lines):
            if re.match(r'^\s*\(\d+\)', line):
                # A linha anterior (se existir e não for vazia) é o laboratório
                if i > 0:
                    prev_line = lines[i - 1].strip()
                    if prev_line and not re.match(r'^[\(\d]', prev_line):
                        result['Laboratory'] = prev_line
                break
        
        # Adiciona campos obrigatórios para o webhook
        result['FileName'] = result.get('PatientID', '') or result.get('ExamNumber', '')
        result['ExamCode'] = 'COAG'
        
        # Verifica se o exame falhou
        if 'FALHOU' in text.upper():
            result['Status'] = 'FAILED'
        else:
            result['Status'] = 'SUCCESS'
        
    except Exception as e:
        logging.error(f"Erro ao parsear exame: {e}")
        return {}
    
    return result


def task_sender_to_webhook():
    """
    Thread em background que monitora a pasta GERADOS_DIR e envia
    os exames para o webhook.
    """
    logging.info("Iniciando monitor de envio para Webhook...")
    
    while True:
        try:
            arquivos = [f for f in os.listdir(GERADOS_DIR) if f.endswith('.log')]
            
            for nome_arquivo in arquivos:
                caminho_origem = os.path.join(GERADOS_DIR, nome_arquivo)
                caminho_destino = os.path.join(ENVIADOS_DIR, nome_arquivo)
                
                # Lê o arquivo de log
                with open(caminho_origem, 'r', encoding='utf-8', errors='ignore') as f:
                    conteudo_log = f.read()
                
                # Separa os exames individuais
                exames = split_exams_from_log(conteudo_log)
                
                if not exames:
                    logging.warning(f"Nenhum exame encontrado em {nome_arquivo}")
                    shutil.move(caminho_origem, caminho_destino)
                    continue
                
                logging.info(f"Encontrados {len(exames)} exame(s) em {nome_arquivo}")
                
                # Processa cada exame
                for i, exame_texto in enumerate(exames, 1):
                    # Parseia o exame
                    payload = parse_coagmaster_exam(exame_texto)
                    
                    if not payload:
                        logging.warning(f"Exame {i} inválido em {nome_arquivo}, ignorando.")
                        continue
                    
                    # Salva JSON de referência
                    pasta_txt = os.path.join(ENVIADOS_DIR, "txt")
                    os.makedirs(pasta_txt, exist_ok=True)
                    
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    nome_txt = f"{os.path.splitext(nome_arquivo)[0]}_exame_{i}_{timestamp}.json"
                    caminho_txt = os.path.join(pasta_txt, nome_txt)
                    
                    with open(caminho_txt, "w", encoding="utf-8") as f:
                        f.write(json.dumps(payload, indent=2, ensure_ascii=False))
                    logging.info(f"JSON salvo: {caminho_txt}")
                    
                    # Envia para o webhook
                    headers = {'Content-Type': 'application/json'}
                    
                    try:
                        response = requests.post(
                            WEBHOOK_URL,
                            json=payload,
                            headers=headers,
                            timeout=30
                        )
                        
                        if response.status_code in (200, 201):
                            logging.info(f"✓ Sucesso: Exame {i} de {nome_arquivo} enviado ao Webhook.")
                            try:
                                resp_json = response.json()
                                msg = resp_json.get('message', 'OK')
                                logging.info(f"  Mensagem: {msg}")
                            except:
                                pass
                        else:
                            logging.error(f"✗ Webhook recusou exame {i} de {nome_arquivo}: Status {response.status_code}")
                            logging.error(f"  Resposta: {response.text}")
                            logging.error(f"  Payload enviado: {json.dumps(payload)}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"✗ Erro de conexão ao enviar exame {i}: {e}")
                
                # Move o arquivo original para enviados
                shutil.move(caminho_origem, caminho_destino)
                logging.info(f"Arquivo movido para enviados: {nome_arquivo}")
                
        except Exception as e:
            logging.error(f"Erro na thread de envio: {e}")
        
        time.sleep(CHECK_FILES_INTERVAL)


def main():
    """
    Loop principal que lê dados da porta serial e salva os exames.
    """
    thread_envio = Thread(target=task_sender_to_webhook, daemon=True)
    thread_envio.start()
    
    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
        logging.info(f"Conectado à porta {COM_PORT}. Escutando serial...")
    except Exception as e:
        logging.critical(f"Falha ao abrir porta serial: {e}")
        return
    
    buffer = ""
    
    while True:
        try:
            if ser.in_waiting > 0:
                buffer += ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
            
            # Detecta fim de exame: linha em branco ou padrão de separador
            # O Coagmaster envia blocos de texto terminados por linhas em branco
            # ou pelo padrão ******************************\n\n
            
            # Verifica se há um exame completo no buffer
            # Um exame é considerado completo quando:
            # 1. Contém o padrão (NNNN) - número do exame
            # 2. Seguido por linhas em branco ou novo cabeçalho PuTTY
            
            # Padrão de fim de exame: linha com asteriscos seguida de linhas em branco
            # ou cabeçalho PuTTY
            exam_end_pattern = r'(\*{30,}\s*\n\s*\n)'
            putty_header = '=~=~=~=~=~=~=~=~=~=~=~='
            
            # Verifica se há conteúdo suficiente para um exame
            if '(' in buffer and ')' in buffer:
                # Procura por fim de exame
                end_match = re.search(exam_end_pattern, buffer)
                putty_match = buffer.find(putty_header)
                
                if end_match:
                    # Extrai o exame até o fim detectado
                    end_pos = end_match.end()
                    exam_content = buffer[:end_pos]
                    buffer = buffer[end_pos:]
                    
                    # Salva o exame
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"coagmaster_{timestamp}.log"
                    file_path = os.path.join(GERADOS_DIR, filename)
                    
                    with open(file_path, "w", encoding="utf-8", newline='') as f:
                        f.write(exam_content)
                    
                    logging.info(f"Exame salvo em disco: {filename}")
                    
                elif putty_match > 0:
                    # Novo cabeçalho PuTTY indica fim do exame anterior
                    exam_content = buffer[:putty_match]
                    buffer = buffer[putty_match:]
                    
                    if exam_content.strip():
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        filename = f"coagmaster_{timestamp}.log"
                        file_path = os.path.join(GERADOS_DIR, filename)
                        
                        with open(file_path, "w", encoding="utf-8", newline='') as f:
                            f.write(exam_content)
                        
                        logging.info(f"Exame salvo em disco: {filename}")
            
            time.sleep(READ_INTERVAL)
            
        except KeyboardInterrupt:
            logging.info("Finalizando...")
            ser.close()
            break
        except Exception as e:
            logging.exception(f"Erro no loop serial: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main()