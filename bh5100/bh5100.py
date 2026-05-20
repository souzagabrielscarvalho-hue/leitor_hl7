import serial
import time
import requests
import logging
import datetime
import os
import shutil
from threading import Thread
import base64

# ================= CONFIGURAÇÕES =================
COM_PORT = 'COM3'
BAUD_RATE = 9600

# ID da franquia configurado no banco de dados
FRANCHISE_CREDENTIAL_ID = 'f47d9a16-df12-4091-b759-79648d13e371'

# Webhook do Vida Exame
# Local: http://localhost/api/integration/bh5100
# Produção: https://apoio.internal.vidaexame.com/api/integration/bh5100
WEBHOOK_URL = f'https://apoio.internal.vidaexame.com/api/integration/bh5100?franchise_credential_id={FRANCHISE_CREDENTIAL_ID}'

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
# =================================================

# Pastas de trabalho – agora na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorBH5100")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
LOG_FILE = os.path.join(BASE_DIR, "analisador_bh5100.log")
# =================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)

SB = chr(0x0B)
EB = chr(0x1C)
CR = chr(0x0D)

def extrair_imagens_de_hl7(conteudo: str, diretorio_saida: str, prefixo: str = ""):
    limpo = conteudo.replace(SB, '').replace(EB, '')
    limpo = limpo.replace('\r\n', '\n').replace('\r', '\n')
    segmentos = limpo.split('\n')
    imagens_extraidas = 0

    for segmento in segmentos:
        campos = segmento.split('|')
        if campos[0] != 'OBX' or len(campos) < 6:
            continue

        # O tipo de dado está no campo[2] (posição 2 do split)
        tipo = campos[2]
        if not tipo.startswith('ED'):
            continue

        # Exemplo do campo[5]: "5-Diff^Image^PNG^Base64^iVBORw0KG..."
        dados_encapsulados = campos[5]
        partes = dados_encapsulados.split('^')
        # Precisamos de pelo menos 5 partes: Fonte, Image, Formato, Base64, Dados
        if len(partes) < 5:
            continue

        fonte = partes[0]       # e.g., "5-Diff" ou "UT5160"
        tipo_imagem = partes[1] # deve ser "Image"
        formato = partes[2]     # "PNG" ou "BMP"
        codificacao = partes[3] # "Base64"
        b64_data = partes[4]    # string base64

        if tipo_imagem.upper() != 'IMAGE' or codificacao.upper() != 'BASE64':
            continue

        # Aceita tanto PNG quanto BMP
        if formato.upper() not in ('PNG', 'BMP'):
            continue

        # Corrigir padding do Base64 (comprimento deve ser múltiplo de 4)
        missing_padding = len(b64_data) % 4
        if missing_padding:
            b64_data += '=' * (4 - missing_padding)

        try:
            imagem_bytes = base64.b64decode(b64_data)
        except Exception as e:
            print(f"Erro ao decodificar Base64: {e}")
            continue

        # Monta um nome de arquivo descritivo usando o nome do teste (campo[3])
        nome_teste = campos[3].split('^')[0] if campos[3] else f"imagem_{imagens_extraidas+1}"
        nome_teste = nome_teste.replace(' ', '_').replace('\\', '_').replace('/', '_')
        extensao = formato.lower()  # png ou bmp
        nome_arquivo = f"{prefixo}{nome_teste}.{extensao}"
        caminho_completo = os.path.join(diretorio_saida, nome_arquivo)

        with open(caminho_completo, 'wb') as f:
            f.write(imagem_bytes)
        print(f"Imagem salva: {caminho_completo}")
        imagens_extraidas += 1

    return imagens_extraidas

def parse_hl7_to_txt(hl7_message: str) -> str:
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        # Normaliza quebras e remove CR do final, mas mantém como separador
        clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
        segments = clean_message.split('\n')

        # --- Extrair código de barras (OBR-3 = Sample Number / tag_identifier) ---
        barcode = ""
        for seg in segments:
            fields = seg.split('|')
            if fields[0] == 'OBR' and len(fields) > 3:
                barcode = fields[3] if len(fields) > 3 else ""
                break

        # Mapeamento: test_id (campo OBX-3, parte antes do ^) → nome simplificado
        MAPEAMENTO = {
            'WBC':  'WBC',
            'NEU#': 'NE',
            'NEU':  'NE_Percent',
            'LYM#': 'LY',
            'LYM':  'LY_Percent',
            'MON#': 'MO',
            'MON':  'MO_Percent',
            'EOS#': 'EO',
            'EOS':  'EO_Percent',
            'BASO#':'BA',
            'BASO': 'BA_Percent',
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
        }

        # Coletar resultados: { nome_simplificado: (valor, flag) }
        resultados = {}
        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue

            value_type = fields[2]
            if value_type == 'ED':
                continue  # ignora imagens

            # test_id é a parte antes do ^ no campo 3
            test_id_raw = fields[3]
            test_id = test_id_raw.split('^')[0] if test_id_raw else ""

            nome = MAPEAMENTO.get(test_id)
            if nome is None:
                continue

            value = fields[5]
            abnormal_flag = fields[8] if len(fields) > 8 else ""

            # Flag: N → sem flag; outros (H, L, *, etc.) → anexar ao valor
            flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''
            resultados[nome] = (value, flag)

        if not resultados:
            return ""

        # Ordem de saída conforme o formato desejado
        ORDEM = [
            'WBC', 'NE', 'NE_Percent', 'LY', 'LY_Percent',
            'MO', 'MO_Percent', 'EO', 'EO_Percent', 'BA', 'BA_Percent',
            'RBC', 'HGB', 'HCT', 'MCV', 'MCH', 'MCHC', 'RDW_CV', 'RDW_SD',
            'PLT', 'PCT', 'MPV', 'PDW', 'P_LCR',
        ]

        lines = [f"FileName: {barcode}"]
        for nome in ORDEM:
            if nome in resultados:
                valor, flag = resultados[nome]
                lines.append(f"{nome}: {valor}{flag}")

        return "\n".join(lines)
    except Exception as e:
        logging.error(f"Erro ao converter HL7: {e}")
        return ""

def parse_hl7_to_dict(hl7_message: str) -> dict:
    """Extrai os campos individuais do hemograma da mensagem HL7."""
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
        segments = clean_message.split('\n')

        MAPEAMENTO = {
            'WBC':  'WBC',
            'NEU#': 'NE',
            'NEU':  'NE_Percent',
            'LYM#': 'LY',
            'LYM':  'LY_Percent',
            'MON#': 'MO',
            'MON':  'MO_Percent',
            'EOS#': 'EO',
            'EOS':  'EO_Percent',
            'BASO#':'BA',
            'BASO': 'BA_Percent',
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
        }

        resultados = {}
        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue

            value_type = fields[2]
            if value_type == 'ED':
                continue  # ignora imagens

            test_id_raw = fields[3]
            test_id = test_id_raw.split('^')[0] if test_id_raw else ""

            nome = MAPEAMENTO.get(test_id)
            if nome is None:
                continue

            value = fields[5]
            abnormal_flag = fields[8] if len(fields) > 8 else ""
            flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''
            resultados[nome] = f"{value}{flag}"

        return resultados
    except Exception as e:
        logging.error(f"Erro ao extrair campos do HL7: {e}")
        return {}

def generate_ack(hl7_message: str) -> bytes:
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
        segments = clean_message.split('\n')
        msh = next((s for s in segments if s.startswith('MSH')), "")
        if not msh:
            return b''

        fields = msh.split('|')
        msg_id = fields[9] if len(fields) > 9 else ""
        dt_now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        # Para BH5100, assumimos que o equipamento espera receber como:
        # Sending Application: LIS, Sending Facility: PC
        # Receiving Application: BH5100 (ou o modelo exato), Receiving Facility: (vazio ou Analyzer)
        ack = f"MSH|^~\\&|LIS|PC|BH5100||{dt_now}||ACK^R01|{msg_id}|P|2.3.1{CR}"
        ack += f"MSA|AA|{msg_id}{CR}"
        return (SB + ack + EB + CR).encode('utf-8')
    except Exception as e:
        logging.error(f"Erro ao gerar ACK: {e}")
        return b''
    

def task_sender_to_webhook():
    logging.info("Iniciando monitor de envio para Webhook...")
    logging.info(f"URL do Webhook: {WEBHOOK_URL}")
    logging.info(f"Verificando arquivos a cada {CHECK_FILES_INTERVAL}s na pasta: {GERADOS_DIR}")
    
    erros_consecutivos = 0
    MAX_ERROS_CONSECUTIVOS = 10
    
    while True:
        try:
            arquivos = [f for f in os.listdir(GERADOS_DIR) if f.endswith('.hl7')]

            if arquivos:
                logging.info(f"Encontrados {len(arquivos)} arquivo(s) HL7 para processar.")
                erros_consecutivos = 0  # reset ao encontrar arquivos

            for nome_arquivo in arquivos:
                caminho_origem = os.path.join(GERADOS_DIR, nome_arquivo)
                caminho_destino = os.path.join(ENVIADOS_DIR, nome_arquivo)

                # Lê o arquivo HL7
                try:
                    with open(caminho_origem, 'r', encoding='utf-8', newline='') as f:
                        conteudo_hl7 = f.read()
                except PermissionError:
                    logging.error(f"✗ Permissão negada ao ler arquivo: {nome_arquivo} — o arquivo pode estar em uso.")
                    continue
                except FileNotFoundError:
                    logging.warning(f"Arquivo {nome_arquivo} não encontrado (pode ter sido removido por outro processo).")
                    continue
                except Exception as e:
                    logging.error(f"✗ Erro inesperado ao ler arquivo {nome_arquivo}: {type(e).__name__}: {e}")
                    continue
                
                if not conteudo_hl7 or not conteudo_hl7.strip():
                    logging.warning(f"Arquivo {nome_arquivo} está vazio, movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)
                    continue
                
                # Extrai imagens (se houver)
                try:
                    pasta_imagens = os.path.join(ENVIADOS_DIR, "imagens")
                    os.makedirs(pasta_imagens, exist_ok=True)
                    prefixo = os.path.splitext(nome_arquivo)[0] + "_"
                    qtd_imagens = extrair_imagens_de_hl7(conteudo_hl7, pasta_imagens, prefixo)
                    if qtd_imagens > 0:
                        logging.info(f"{qtd_imagens} imagem(ns) extraída(s) do arquivo {nome_arquivo}.")
                except Exception as e:
                    logging.warning(f"Erro ao extrair imagens de {nome_arquivo}: {type(e).__name__}: {e}")
                    # Não interrompe o fluxo — imagens são opcionais
                
                # Converte HL7 para TXT (debug)
                txt_data = parse_hl7_to_txt(conteudo_hl7)

                # Extrai campos para o webhook
                campos = parse_hl7_to_dict(conteudo_hl7)

                if campos:
                    logging.info(f"Arquivo {nome_arquivo}: {len(campos)} campo(s) de hemograma extraídos com sucesso.")
                    
                    # Salvar TXT em ENVIADOS_DIR/txt/ (debug)
                    if txt_data:
                        try:
                            pasta_txt = os.path.join(ENVIADOS_DIR, "txt")
                            os.makedirs(pasta_txt, exist_ok=True)
                            nome_txt = os.path.splitext(nome_arquivo)[0] + ".txt"
                            caminho_txt = os.path.join(pasta_txt, nome_txt)
                            
                            with open(caminho_txt, "w", encoding="utf-8") as f:
                                f.write(txt_data)
                            logging.info(f"TXT salvo: {caminho_txt}")
                        except Exception as e:
                            logging.warning(f"Erro ao salvar TXT de debug para {nome_arquivo}: {e}")

                    # Extrai o código de barras (OBR-3 = Sample Number / tag_identifier) do HL7
                    barcode = ""
                    clean_message = conteudo_hl7.replace(SB, '').replace(EB, '')
                    clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
                    segments = clean_message.split('\n')
                    
                    for seg in segments:
                        fields = seg.split('|')
                        if fields[0] == 'OBR' and len(fields) > 3:
                            barcode = fields[3] if len(fields) > 3 else ""
                            break
                    
                    if not barcode:
                        logging.error(f"Arquivo {nome_arquivo}: código de barras (OBR-3 / Sample Number) não encontrado no HL7.")
                        logging.error(f"  Não é possível enviar sem código de barras — o tag_identifier é obrigatório para identificar o procedimento.")
                        logging.error(f"  Movendo para enviados sem processar.")
                        shutil.move(caminho_origem, caminho_destino)
                        continue
                    
                    # Monta o payload com franchise_credential_id incluso no corpo
                    payload = {
                        'franchise_credential_id': FRANCHISE_CREDENTIAL_ID,
                        'FileName': barcode,
                        'ExamCode': 'HEMO',
                        **campos  # espalha WBC, NE, NE_Percent, etc. como chaves individuais
                    }
                    
                    headers = {'Content-Type': 'application/json'}
                    
                    try:
                        logging.info(f"Enviando {nome_arquivo} (tag_identifier: {barcode}) para o webhook...")
                        response = requests.post(
                            WEBHOOK_URL, 
                            json=payload,
                            headers=headers, 
                            timeout=30
                        )

                        if response.status_code in (200, 201):
                            logging.info(f"✓ Sucesso ({response.status_code}): {nome_arquivo} enviado ao Webhook.")
                            logging.info(f"  Resposta: {response.text[:500]}")
                            shutil.move(caminho_origem, caminho_destino)
                            logging.info(f"  Arquivo movido para: {caminho_destino}")
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
                        elif response.status_code == 401 or response.status_code == 403:
                            logging.error(f"✗ ERRO {response.status_code}: Falha de autenticação para {nome_arquivo}.")
                            logging.error(f"  Verifique o FRANCHISE_CREDENTIAL_ID: {FRANCHISE_CREDENTIAL_ID}")
                            logging.error(f"  Resposta: {response.text[:500]}")
                        elif response.status_code == 502 or response.status_code == 503:
                            logging.error(f"✗ ERRO {response.status_code}: Servidor indisponível para {nome_arquivo}.")
                            logging.error(f"  O servidor pode estar fora do ar ou em manutenção. Nova tentativa em {CHECK_FILES_INTERVAL}s.")
                        else:
                            logging.error(f"✗ Webhook recusou {nome_arquivo}: Status HTTP {response.status_code}")
                            logging.error(f"  Resposta: {response.text[:500]}")
                            # Não move o arquivo para permitir reenvio
                    except requests.exceptions.ConnectionError as e:
                        logging.error(f"✗ ERRO DE CONEXÃO: Não foi possível conectar ao servidor para {nome_arquivo}.")
                        logging.error(f"  URL: {WEBHOOK_URL}")
                        logging.error(f"  Detalhe: {e}")
                        logging.error(f"  Verifique: (1) Servidor está ligado? (2) Porta 8040 está acessível? (3) Firewall liberado?")
                    except requests.exceptions.Timeout as e:
                        logging.error(f"✗ TIMEOUT: O servidor não respondeu a tempo para {nome_arquivo} (30s).")
                        logging.error(f"  URL: {WEBHOOK_URL}")
                        logging.error(f"  Detalhe: {e}")
                    except requests.exceptions.TooManyRedirects as e:
                        logging.error(f"✗ ERRO: Muitos redirecionamentos ao acessar {WEBHOOK_URL}: {e}")
                    except requests.exceptions.RequestException as e:
                        logging.error(f"✗ ERRO DE REDE ao enviar {nome_arquivo}: {type(e).__name__}: {e}")
                        logging.error(f"  URL: {WEBHOOK_URL}")
                        # Não move o arquivo para permitir reenvio
                        
                else:
                    logging.warning(f"Arquivo {nome_arquivo} NÃO contém campos de hemograma reconhecíveis.")
                    logging.warning(f"  Conteúdo (primeiros 200 chars): {conteudo_hl7[:200]}")
                    logging.warning(f"  Movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)

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
    logging.info("=" * 60)
    logging.info("Analisador BH5100 - Serviço de Integração HL7")
    logging.info(f"Data/Hora de início: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Porta Serial: {COM_PORT} | Baud Rate: {BAUD_RATE}")
    logging.info(f"Webhook: {WEBHOOK_URL}")
    logging.info(f"Pastas: gerados={GERADOS_DIR} | enviados={ENVIADOS_DIR}")
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
    mensagens_processadas = 0
    ultimo_log_status = time.time()
    INTERVALO_LOG_STATUS = 300  # log de status a cada 5 minutos

    logging.info("Escutando dados da porta serial...")
    
    while True:
        try:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                bytes_recebidos += len(data)
                buffer += data.decode('utf-8', errors='ignore')

            while SB in buffer and EB in buffer:
                start_idx = buffer.find(SB)
                end_idx = buffer.find(EB)
                if len(buffer) <= end_idx + 1:
                    break

                message_end_idx = end_idx + 2
                hl7_message = buffer[start_idx:message_end_idx]
                buffer = buffer[message_end_idx:]

                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = f"exame_{timestamp}.hl7"
                file_path = os.path.join(GERADOS_DIR, filename)

                try:
                    with open(file_path, "w", encoding="utf-8", newline='') as f:
                        f.write(hl7_message)
                    mensagens_processadas += 1
                    logging.info(f"✓ Mensagem HL7 salva: {filename} ({len(hl7_message)} bytes)")
                except OSError as e:
                    logging.error(f"✗ Erro ao salvar arquivo {filename}: {e} (espaço em disco?)")
                    continue

                ack_bytes = generate_ack(hl7_message)
                if ack_bytes:
                    try:
                        ser.write(ack_bytes)
                        ser.flush()
                        logging.info(f"  ACK enviado ao equipamento ({len(ack_bytes)} bytes).")
                    except serial.SerialException as e:
                        logging.error(f"✗ Erro ao enviar ACK pela serial: {e}")
                        logging.error(f"  A porta serial pode ter sido desconectada. Tentando reconectar...")
                        try:
                            ser.close()
                        except:
                            pass
                        try:
                            ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=0.1)
                            logging.info(f"  Porta serial reconectada com sucesso.")
                        except Exception as recon_err:
                            logging.critical(f"✗ Falha ao reconectar porta serial: {recon_err}")
                            break
                else:
                    logging.warning(f"  ACK não gerado para {filename} (MSH não encontrado na mensagem).")

            # Log de status periódico (a cada 5 min)
            agora = time.time()
            if agora - ultimo_log_status >= INTERVALO_LOG_STATUS:
                logging.info(f"[STATUS] Uptime: {int(agora - ultimo_log_status)}s | "
                           f"Mensagens processadas: {mensagens_processadas} | "
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
    logging.info(f"Serviço finalizado. Total de mensagens processadas: {mensagens_processadas}")

if __name__ == "__main__":
    main()