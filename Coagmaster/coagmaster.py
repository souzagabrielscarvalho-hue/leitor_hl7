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
COM_PORT = 'COM4'
BAUD_RATE = 115200

# ID da franquia configurado no banco de dados
FRANCHISE_CREDENTIAL_ID = 'f47d9a16-df12-4091-b759-79648d13e371'

# Webhook do Coagmaster
# Local: http://localhost:8039/api/integration/coagmaster
# Produção: https://apoio.internal.vidaexame.com/api/integration/coagmaster
WEBHOOK_URL = f'https://apoio.internal.vidaexame.com/api/integration/coagmaster?franchise_credential_id={FRANCHISE_CREDENTIAL_ID}'

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
MAX_RETRY = 5
RETRY_INTERVAL = 60  # segundos entre tentativas de reenvio (1 minuto)
# =================================================

# Pastas de trabalho – na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorCoagmaster")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
REQUISICOES_NAO_ENVIADAS_DIR = os.path.join(BASE_DIR, "requisições não enviadas")
LOG_FILE = os.path.join(BASE_DIR, "analisador_coagmaster.log")
# ==================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)
os.makedirs(REQUISICOES_NAO_ENVIADAS_DIR, exist_ok=True)

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
    lines = content.split('\n')
    current_exam = []
    exam_started = False
    
    for line in lines:
        # Detecta início de novo exame: linha contendo (NNNN) com possível CANAL
        if re.match(r'^\s*\(\d+\)', line):
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
    
    Formato esperado (exemplo real - TP):
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
    
    Formato real com falha:
        VIDA EXAMES
        (0052)
        CANAL 1
        05/05/2026         09:36:43
        N. SERIE(26031005)
        OPERADOR(OPERADOR)
        ID()
        NOME:  
        EXAME:            OUTROS1
                          OUTROS
        TEMPO:     FALHOU!
    
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
        # IMPORTANTE: Não capturar se NOME: está vazio ou contém apenas espaços
        # Evita capturar a próxima linha como nome (ex: "EXAME: OUTROS1")
        match = re.search(r'NOME:\s*(.+)', text, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            if nome and not re.match(r'^(EXAME|CANAL|TEMPO|RELA)', nome, re.IGNORECASE):
                result['PatientName'] = nome
        
        # Código do exame: Exame: XX
        match = re.search(r'Exame:\s*(\S+)', text, re.IGNORECASE)
        if match:
            result['ExamType'] = match.group(1).upper()
        
        # Descrição do exame (linha seguinte ao código)
        # A descrição é a primeira linha não-vazia após "Exame: XX"
        # IMPORTANTE: Não confundir com linhas de dados (TEMPO:, RELAÇÃO:, etc.)
        # que começam com a palavra-chave seguida de ":"
        for i, line in enumerate(lines):
            if re.match(r'^\s*Exame:', line, re.IGNORECASE):
                for j in range(i + 1, len(lines)):
                    next_line = lines[j].strip()
                    # Ignorar linhas vazias, "%" sozinho, porcentagens isoladas
                    if not next_line or next_line == '%' or re.match(r'^[\d,]+%$', next_line):
                        continue
                    # Ignorar linhas de dados que contêm ":" (TEMPO:, RELAÇÃO:, etc.)
                    if re.match(r'^(TEMPO|RELA[CÇ][AÃ]O|INR|CONTROLE|ID|OPERADOR|CANAL|NOME|N\.\s*SERIE)', next_line, re.IGNORECASE) and ':' in next_line:
                        continue
                    # Ignorar linhas que são apenas "ID(...)"
                    if re.match(r'^ID\(', next_line, re.IGNORECASE):
                        continue
                    # Ignorar datas e horas
                    if re.match(r'^\d{2}/\d{2}/\d{4}', next_line):
                        continue
                    if re.match(r'^\d{2}:\d{2}:\d{2}', next_line):
                        continue
                    # Ignorar linhas de asteriscos
                    if re.match(r'^\*{10,}', next_line):
                        continue
                    # Se chegou aqui, é a descrição do exame
                    result['ExamDescription'] = next_line
                    break
                break
        
        # Tempo medido: TEMPO: XX,X s  ou  TEMPO: FALHOU!
        match = re.search(r'TEMPO:\s*(.+)', text, re.IGNORECASE)
        if match:
            tempo_valor = match.group(1).strip()
            result['TimeValue'] = tempo_valor
        
        # Relação: RELAÇÃO: X.XX ou X,XX
        match = re.search(r'RELA[CÇ][AÃ]O:\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['Relation'] = match.group(1).replace(',', '.')
        
        # Porcentagem: XX,X% ou %: XX,X%
        # IMPORTANTE: Capturar a porcentagem do resultado, não a do CONTROLE
        # Formato 1: linha isolada "81,4%"
        # Formato 2: linha com prefixo "%: 604.0%"
        for line in lines:
            stripped = line.strip()
            # Formato 2: "%: XX,X%" ou "%: XX.X%"
            pct_match = re.match(r'^%\s*:\s*([\d,\.]+)%', stripped)
            if pct_match:
                result['Percentage'] = pct_match.group(1).replace(',', '.')
                break
            # Formato 1: linha que contém apenas uma porcentagem (ex: "81,4%" ou "81.4%")
            if re.match(r'^[\d,]+%$', stripped):
                result['Percentage'] = stripped.replace(',', '.')
                break
        
        # INR: INR X,XX ou INR: X,XX
        match = re.search(r'INR\s*:?\s*([\d,\.]+)', text, re.IGNORECASE)
        if match:
            result['INR'] = match.group(1).replace(',', '.')
        
        # Controle: CONTROLE ...: XX,Xs ou XX.Xs
        match = re.search(r'CONTROLE[^:]*:\s*([\d,\.]+\s*s?)', text, re.IGNORECASE)
        if match:
            result['Control'] = match.group(1).strip()
        
        # Concentração (para Fibrinogênio): CONCENTRAÇÃO: XX,X ou mg/dL
        match = re.search(r'CONCENTRA[CÇ][AÃ]O:\s*([\d,\.]+\s*(?:mg/dL|g/L|%)?)', text, re.IGNORECASE)
        if match:
            result['Concentration'] = match.group(1).strip()
        
        # ID do paciente: ID(...)
        match = re.search(r'ID\(([^)]*)\)', text)
        if match:
            patient_id = match.group(1).strip()
            if patient_id:  # Só incluir se não estiver vazio
                result['PatientID'] = patient_id
        
        # Operador: OPERADOR (...)
        match = re.search(r'OPERADOR\s*\(([^)]+)\)', text, re.IGNORECASE)
        if match:
            result['Operator'] = match.group(1)
        
        # Número de série: N. SERIE(...)
        match = re.search(r'N\.\s*SERIE\(([^)]+)\)', text, re.IGNORECASE)
        if match:
            result['SerialNumber'] = match.group(1)
        
        # Laboratório (cabeçalho): primeira linha antes do número do exame
        for i, line in enumerate(lines):
            if re.match(r'^\s*\(\d+\)', line):
                # A linha anterior (se existir e não for vazia) é o laboratório
                if i > 0:
                    prev_line = lines[i - 1].strip()
                    if prev_line and not re.match(r'^[\(\d]', prev_line) and not re.match(r'^\*{10,}', prev_line):
                        result['Laboratory'] = prev_line
                break
        
        # Adiciona campos obrigatórios para o webhook
        # FileName = PatientID (código de barras) se disponível, senão ExamNumber
        result['FileName'] = result.get('PatientID', '') or result.get('ExamNumber', '')
        result['ExamCode'] = 'COAGU'
        
        # Verifica se o exame falhou
        if 'FALHOU' in text.upper():
            result['Status'] = 'FAILED'
        else:
            result['Status'] = 'SUCCESS'
        
    except Exception as e:
        logging.error(f"Erro ao parsear exame: {e}")
        return {}
    
    return result


def _enviar_payload_webhook(payload: dict, nome_arquivo: str, tag_identifier: str, exame_num: int = 0) -> bool:
    """
    Envia um payload para o webhook com até MAX_RETRY tentativas.
    Retorna True se o envio foi bem-sucedido, False caso contrário.
    """
    headers = {'Content-Type': 'application/json'}
    prefixo = f"Exame {exame_num} de {nome_arquivo}" if exame_num else nome_arquivo

    for tentativa in range(1, MAX_RETRY + 1):
        try:
            logging.info(f"Enviando {prefixo} (tag_identifier: {tag_identifier}) para o webhook... [Tentativa {tentativa}/{MAX_RETRY}]")
            response = requests.post(
                WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in (200, 201):
                logging.info(f"✓ Sucesso ({response.status_code}): {prefixo} enviado ao Webhook.")
                try:
                    resp_json = response.json()
                    msg = resp_json.get('message', 'OK')
                    logging.info(f"  Mensagem: {msg}")
                except:
                    pass
                return True
            elif response.status_code == 404:
                logging.error(f"✗ ERRO 404: Endpoint não encontrado para {prefixo}.")
                logging.error(f"  Verifique se a URL está correta: {WEBHOOK_URL}")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code == 400:
                logging.error(f"✗ ERRO 400: Requisição inválida para {prefixo} (tag_identifier: {tag_identifier}).")
                logging.error(f"  Possíveis causas: tag_identifier não encontrado, procedimento já liberado, ou conteúdo inválido.")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code == 500:
                logging.error(f"✗ ERRO 500: Erro interno do servidor ao processar {prefixo}.")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code in (401, 403):
                logging.error(f"✗ ERRO {response.status_code}: Falha de autenticação para {prefixo}.")
                logging.error(f"  Verifique o FRANCHISE_CREDENTIAL_ID: {FRANCHISE_CREDENTIAL_ID}")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code in (502, 503):
                logging.error(f"✗ ERRO {response.status_code}: Servidor indisponível para {prefixo}.")
                logging.error(f"  O servidor pode estar fora do ar ou em manutenção.")
            else:
                logging.error(f"✗ Webhook recusou {prefixo}: Status HTTP {response.status_code}")
                logging.error(f"  Resposta: {response.text[:500]}")

        except requests.exceptions.ConnectionError as e:
            logging.error(f"✗ ERRO DE CONEXÃO: Não foi possível conectar ao servidor para {prefixo}.")
            logging.error(f"  URL: {WEBHOOK_URL}")
            logging.error(f"  Detalhe: {e}")
        except requests.exceptions.Timeout as e:
            logging.error(f"✗ TIMEOUT: O servidor não respondeu a tempo para {prefixo} (30s).")
            logging.error(f"  URL: {WEBHOOK_URL}")
        except requests.exceptions.RequestException as e:
            logging.error(f"✗ ERRO DE REDE ao enviar {prefixo}: {type(e).__name__}: {e}")
            logging.error(f"  URL: {WEBHOOK_URL}")

        if tentativa < MAX_RETRY:
            logging.warning(f"  Tentativa {tentativa}/{MAX_RETRY} falhou. Nova tentativa em {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)

    return False


def task_sender_to_webhook():
    """
    Thread em background que monitora a pasta GERADOS_DIR e envia
    os exames para o webhook.
    """
    logging.info("Iniciando monitor de envio para Webhook...")
    logging.info(f"URL do Webhook: {WEBHOOK_URL}")
    logging.info(f"Verificando arquivos a cada {CHECK_FILES_INTERVAL}s na pasta: {GERADOS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
    
    erros_consecutivos = 0
    MAX_ERROS_CONSECUTIVOS = 10
    
    while True:
        try:
            arquivos = [f for f in os.listdir(GERADOS_DIR) if f.endswith('.log')]

            if arquivos:
                logging.info(f"Encontrados {len(arquivos)} arquivo(s) de log para processar.")
                erros_consecutivos = 0  # reset ao encontrar arquivos

            for nome_arquivo in arquivos:
                caminho_origem = os.path.join(GERADOS_DIR, nome_arquivo)
                caminho_destino = os.path.join(ENVIADOS_DIR, nome_arquivo)
                caminho_nao_enviado = os.path.join(REQUISICOES_NAO_ENVIADAS_DIR, nome_arquivo)

                # Lê o arquivo de log
                try:
                    with open(caminho_origem, 'r', encoding='utf-8', errors='ignore') as f:
                        conteudo_log = f.read()
                except PermissionError:
                    logging.error(f"✗ Permissão negada ao ler arquivo: {nome_arquivo} — o arquivo pode estar em uso.")
                    continue
                except FileNotFoundError:
                    logging.warning(f"Arquivo {nome_arquivo} não encontrado (pode ter sido removido por outro processo).")
                    continue
                except Exception as e:
                    logging.error(f"✗ Erro inesperado ao ler arquivo {nome_arquivo}: {type(e).__name__}: {e}")
                    continue
                
                if not conteudo_log or not conteudo_log.strip():
                    logging.warning(f"Arquivo {nome_arquivo} está vazio, movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)
                    continue
                
                # Separa os exames individuais
                exames = split_exams_from_log(conteudo_log)
                
                if not exames:
                    logging.warning(f"Nenhum exame encontrado em {nome_arquivo}")
                    shutil.move(caminho_origem, caminho_destino)
                    continue
                
                logging.info(f"Encontrados {len(exames)} exame(s) em {nome_arquivo}")
                
                # Processa cada exame
                todos_enviados = True
                for i, exame_texto in enumerate(exames, 1):
                    # Parseia o exame
                    payload = parse_coagmaster_exam(exame_texto)
                    
                    if not payload:
                        logging.warning(f"Exame {i} inválido em {nome_arquivo}, ignorando.")
                        continue
                    
                    # Verifica se tem FileName (tag_identifier) para identificar o procedimento
                    if not payload.get('FileName'):
                        logging.error(f"✗ Exame {i} de {nome_arquivo}: FileName (tag_identifier) vazio — impossível identificar o procedimento.")
                        logging.error(f"  ID do paciente não encontrado no exame. Verifique se o equipamento está configurado para enviar o código de barras.")
                        todos_enviados = False
                        continue
                    
                    # Verifica se o exame falhou (TEMPO: FALHOU!) — não envia para o webhook
                    if payload.get('Status') == 'FAILED':
                        logging.warning(f"⚠ Exame {i} de {nome_arquivo} (tag_identifier: {payload.get('FileName')}) FALHOU — não será enviado ao webhook.")
                        logging.warning(f"  O equipamento reportou TEMPO: FALHOU! O exame precisa ser refeito.")
                        # Salva JSON de referência na pasta de requisições não enviadas
                        pasta_nao_enviados_txt = os.path.join(REQUISICOES_NAO_ENVIADAS_DIR, "txt")
                        os.makedirs(pasta_nao_enviados_txt, exist_ok=True)
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        nome_falha = f"{os.path.splitext(nome_arquivo)[0]}_exame_{i}_{timestamp}.json"
                        caminho_falha = os.path.join(pasta_nao_enviados_txt, nome_falha)
                        try:
                            with open(caminho_falha, "w", encoding="utf-8") as f:
                                f.write(json.dumps(payload, indent=2, ensure_ascii=False))
                            logging.info(f"  JSON de falha salvo: {caminho_falha}")
                        except Exception as e:
                            logging.warning(f"  Erro ao salvar JSON de falha: {e}")
                        continue  # Pula o envio, mas não marca como falha de envio
                    
                    # Salva JSON de referência
                    pasta_txt = os.path.join(ENVIADOS_DIR, "txt")
                    os.makedirs(pasta_txt, exist_ok=True)
                    
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    nome_txt = f"{os.path.splitext(nome_arquivo)[0]}_exame_{i}_{timestamp}.json"
                    caminho_txt = os.path.join(pasta_txt, nome_txt)
                    
                    try:
                        with open(caminho_txt, "w", encoding="utf-8") as f:
                            f.write(json.dumps(payload, indent=2, ensure_ascii=False))
                        logging.info(f"JSON salvo: {caminho_txt}")
                    except Exception as e:
                        logging.warning(f"Erro ao salvar JSON de debug para exame {i}: {e}")
                    
                    # Tenta enviar com retry
                    enviado = _enviar_payload_webhook(payload, nome_arquivo, payload.get('FileName', ''), i)
                    
                    if not enviado:
                        todos_enviados = False
                
                # Move o arquivo original: para enviados se todos foram OK, senão para não enviados
                if todos_enviados:
                    shutil.move(caminho_origem, caminho_destino)
                    logging.info(f"Arquivo movido para enviados: {nome_arquivo}")
                else:
                    logging.error(f"✗ Falha definitiva: {nome_arquivo} — um ou mais exames não foram enviados após {MAX_RETRY} tentativas.")
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


def main():
    """
    Loop principal que lê dados da porta serial e salva os exames.
    """
    logging.info("=" * 60)
    logging.info("Analisador Coagmaster - Serviço de Integração")
    logging.info(f"Data/Hora de início: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Porta Serial: {COM_PORT} | Baud Rate: {BAUD_RATE}")
    logging.info(f"Webhook: {WEBHOOK_URL}")
    logging.info(f"Pastas: gerados={GERADOS_DIR} | enviados={ENVIADOS_DIR} | não enviados={REQUISICOES_NAO_ENVIADAS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
    logging.info("=" * 60)
    
    thread_envio = Thread(target=task_sender_to_webhook, daemon=True)
    thread_envio.start()
    logging.info("Thread de envio iniciada.")

    ser = None
    tentativas_porta = 0
    MAX_TENTATIVAS_PORTA = 5
    
    while ser is None and tentativas_porta < MAX_TENTATIVAS_PORTA:
        try:
            ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
            logging.info(f"✓ Conectado à porta {COM_PORT} com sucesso.")
        except serial.SerialException as e:
            tentativas_porta += 1
            logging.error(f"✗ Tentativa {tentativas_porta}/{MAX_TENTATIVAS_PORTA}: Falha ao abrir porta serial {COM_PORT}: {e}")
            if tentativas_porta < MAX_TENTATIVAS_PORTA:
                logging.info(f"  Nova tentativa em 10 segundos...")
                time.sleep(10)
        except Exception as e:
            logging.critical(f"✗ Erro inesperado ao abrir porta serial: {type(e).__name__}: {e}")
            return
    
    if ser is None:
        logging.critical(f"✗ NÃO FOI POSSÍVEL CONECTAR à porta {COM_PORT} após {MAX_TENTATIVAS_PORTA} tentativas.")
        logging.critical(f"  Verifique: (1) Cabo USB conectado? (2) Porta COM correta? (3) Driver instalado?")
        logging.critical(f"  O serviço NÃO será iniciado. Corrija o problema e reinicie.")
        return

    buffer = ""
    bytes_recebidos = 0
    exames_processados = 0
    ultimo_log_status = time.time()
    INTERVALO_LOG_STATUS = 300  # log de status a cada 5 minutos

    logging.info("Escutando dados da porta serial...")
    
    while True:
        try:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                bytes_recebidos += len(data)
                buffer += data.decode('utf-8', errors='ignore')

            # Detecta fim de exame: linha com asteriscos seguida de linhas em branco
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
                    filename = f"exame_{timestamp}.log"
                    file_path = os.path.join(GERADOS_DIR, filename)
                    
                    try:
                        with open(file_path, "w", encoding="utf-8", newline='') as f:
                            f.write(exam_content)
                        exames_processados += 1
                        logging.info(f"✓ Exame salvo: {filename} ({len(exam_content)} bytes)")
                    except OSError as e:
                        logging.error(f"✗ Erro ao salvar arquivo {filename}: {e} (espaço em disco?)")
                        continue
                    
                elif putty_match > 0:
                    # Novo cabeçalho PuTTY indica fim do exame anterior
                    exam_content = buffer[:putty_match]
                    buffer = buffer[putty_match:]
                    
                    if exam_content.strip():
                        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                        filename = f"exame_{timestamp}.log"
                        file_path = os.path.join(GERADOS_DIR, filename)
                        
                        try:
                            with open(file_path, "w", encoding="utf-8", newline='') as f:
                                f.write(exam_content)
                            exames_processados += 1
                            logging.info(f"✓ Exame salvo: {filename} ({len(exam_content)} bytes)")
                        except OSError as e:
                            logging.error(f"✗ Erro ao salvar arquivo {filename}: {e}")
                            continue

            # Log de status periódico (a cada 5 min)
            agora = time.time()
            if agora - ultimo_log_status >= INTERVALO_LOG_STATUS:
                logging.info(f"[STATUS] Uptime: {int(agora - ultimo_log_status)}s | "
                           f"Exames processados: {exames_processados} | "
                           f"Bytes recebidos: {bytes_recebidos} | "
                           f"Buffer atual: {len(buffer)} bytes | "
                           f"Porta aberta: {ser.is_open if ser else 'N/A'}")
                ultimo_log_status = agora

            time.sleep(READ_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Finalizando serviço (KeyboardInterrupt)...")
            break
        except serial.SerialException as e:
            logging.error(f"✗ Erro na porta serial: {e}")
            logging.info("  Tentando reconectar em 5 segundos...")
            try:
                ser.close()
            except:
                pass
            time.sleep(5)
            try:
                ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
                logging.info("  Porta serial reconectada com sucesso.")
            except Exception as recon_err:
                logging.error(f"  Falha ao reconectar: {recon_err}")
                time.sleep(10)
        except Exception as e:
            logging.exception(f"✗ Erro inesperado no loop serial: {type(e).__name__}: {e}")
            time.sleep(1)

    if ser and ser.is_open:
        ser.close()
        logging.info("Porta serial fechada.")
    logging.info(f"Serviço finalizado. Total de exames processados: {exames_processados}")

if __name__ == "__main__":
    main()