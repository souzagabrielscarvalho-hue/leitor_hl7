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
COM_PORT = 'COM5'          # Ajustar conforme necessário
BAUD_RATE = 9600           # VIDAS 1600 geralmente usa 9600
WEBHOOK_URL = 'https://webhook.site/205ed01c-6ba8-4963-9dc8-9a6a727a0196'
READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5   # segundos para checar a pasta de pendentes

# Pastas de trabalho – agora na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorVIDAS1600")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
LOG_FILE = os.path.join(BASE_DIR, "analisador_vidas1600.log")
# =================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)

# Caracteres de controle HL7 (MLLP)
SB = chr(0x0B)   # Start Block
EB = chr(0x1C)   # End Block
CR = chr(0x0D)   # Carriage Return

def extrair_imagens_de_hl7(conteudo: str, diretorio_saida: str, prefixo: str = ""):
    """Extrai imagens embutidas em segmentos OBX com tipo ED (Base64)."""
    limpo = conteudo.replace(SB, '').replace(EB, '')
    limpo = limpo.replace('\r\n', '\n').replace('\r', '\n')
    segmentos = limpo.split('\n')
    imagens_extraidas = 0

    for segmento in segmentos:
        campos = segmento.split('|')
        if campos[0] != 'OBX' or len(campos) < 6:
            continue

        tipo = campos[2]
        if not tipo.startswith('ED'):
            continue

        dados_encapsulados = campos[5]
        partes = dados_encapsulados.split('^')
        if len(partes) < 5:
            continue

        fonte = partes[0]
        tipo_imagem = partes[1]
        formato = partes[2]
        codificacao = partes[3]
        b64_data = partes[4]

        if tipo_imagem.upper() != 'IMAGE' or codificacao.upper() != 'BASE64':
            continue
        if formato.upper() not in ('PNG', 'BMP', 'JPEG', 'JPG'):
            continue

        # Corrigir padding do Base64
        missing_padding = len(b64_data) % 4
        if missing_padding:
            b64_data += '=' * (4 - missing_padding)

        try:
            imagem_bytes = base64.b64decode(b64_data)
        except Exception as e:
            logging.error(f"Erro ao decodificar Base64: {e}")
            continue

        # Nome do teste (OBX-3) ou fallback genérico
        nome_teste = campos[3].split('^')[0] if campos[3] else f"imagem_{imagens_extraidas+1}"
        nome_teste = nome_teste.replace(' ', '_').replace('\\', '_').replace('/', '_')
        extensao = formato.lower()
        nome_arquivo = f"{prefixo}{nome_teste}.{extensao}"
        caminho_completo = os.path.join(diretorio_saida, nome_arquivo)

        with open(caminho_completo, 'wb') as f:
            f.write(imagem_bytes)
        logging.info(f"Imagem salva: {caminho_completo}")
        imagens_extraidas += 1

    return imagens_extraidas

def parse_hl7_to_txt(hl7_message: str) -> str:
    """
    Converte uma mensagem HL7 ORU^R01 (típica do VIDAS) para um texto simplificado.
    Estrutura do TXT:
        FileName: <barcode ou sample ID>
        <TestID>: <Valor> <Unidade> <Flag>
        ...
    """
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
        segments = clean_message.split('\n')

        # Extrai identificadores da amostra a partir do OBR
        barcode = ""
        sample_id = ""
        for seg in segments:
            fields = seg.split('|')
            if fields[0] == 'OBR':
                if len(fields) > 2:
                    barcode = fields[2]          # OBR-2: Placer Order Number (código de barras)
                if len(fields) > 3:
                    sample_id = fields[3]        # OBR-3: Filler Order Number (número da amostra)
                break

        # Prefere barcode, caso contrário usa sample_id
        amostra_id = barcode if barcode else sample_id
        if not amostra_id:
            amostra_id = "DESCONHECIDO"

        # Dicionário para acumular resultados (evita duplicatas)
        resultados = {}   # chave: test_id (string original do OBX-3), valor: (value, unit, flag)

        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue

            value_type = fields[2]
            if value_type == 'ED':
                continue   # ignora imagens

            # Identificador do teste (OBX-3) – pode ter subcomponentes com ^
            observation_id = fields[3]
            # Remove sub-identificadores se vier "teste^subid", pegamos a primeira parte
            test_name = observation_id.split('^')[0] if observation_id else "TESTE_DESCONHECIDO"

            value = fields[5] if len(fields) > 5 else ""
            unit = fields[6] if len(fields) > 6 else ""
            abnormal_flag = fields[8] if len(fields) > 8 else ""

            # Flag: N = normal, L = baixo, H = alto, outros mantidos
            flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''

            # Se já existir o mesmo teste (ex.: repetido), mantemos o último
            resultados[test_name] = (value, unit, flag)

        if not resultados:
            logging.warning("Nenhum resultado encontrado na mensagem.")
            return ""

        # Monta linhas de saída
        lines = [f"FileName: {amostra_id}"]
        for test_name, (val, uni, flg) in sorted(resultados.items()):
            linha = f"{test_name}: {val}"
            if uni:
                linha += f" {uni}"
            if flg:
                linha += f" ({flg})"
            lines.append(linha)

        return "\n".join(lines)

    except Exception as e:
        logging.error(f"Erro ao converter HL7: {e}")
        return ""

def generate_ack(hl7_message: str) -> bytes:
    """
    Gera um ACK HL7 (ACK^R01) em resposta a uma mensagem ORU do VIDAS 1600.
    Utiliza os identificadores da mensagem original para roteamento correto.
    """
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
        segments = clean_message.split('\n')

        # Localiza o segmento MSH
        msh = next((s for s in segments if s.startswith('MSH')), "")
        if not msh:
            logging.error("ACK não gerado: segmento MSH ausente.")
            return b''

        fields = msh.split('|')
        # Conforme HL7 2.3.1:
        # MSH-1: field separator
        # MSH-2: encoding characters
        # MSH-3: sending application   <- origem da mensagem
        # MSH-4: sending facility
        # MSH-5: receiving application <- destino original (LIS)
        # MSH-6: receiving facility
        # MSH-9: message type (ORU^R01)
        # MSH-10: message control ID
        # MSH-11: processing ID (P)
        # MSH-12: version ID (2.3.1)

        sending_app = fields[2] if len(fields) > 2 else "VIDAS"
        sending_fac = fields[3] if len(fields) > 3 else "VIDAS1600"
        receiving_app = fields[4] if len(fields) > 4 else "LIS"
        receiving_fac = fields[5] if len(fields) > 5 else ""
        msg_id = fields[9] if len(fields) > 9 else ""
        dt_now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        # ACK segue a recomendação: inverter origem/destino
        # Sender do ACK será o LIS (quem está recebendo).
        ack = f"MSH|^~\\&|{receiving_app}|{receiving_fac}|{sending_app}|{sending_fac}|{dt_now}||ACK^R01|{msg_id}|P|2.3.1{CR}"
        ack += f"MSA|AA|{msg_id}{CR}"
        return (SB + ack + EB + CR).encode('utf-8')
    except Exception as e:
        logging.error(f"Erro ao gerar ACK: {e}")
        return b''

def task_sender_to_webhook():
    """Thread que monitora a pasta GERADOS, converte HL7->TXT e envia ao webhook."""
    logging.info("Iniciando monitor de envio para Webhook...")
    while True:
        try:
            arquivos = [f for f in os.listdir(GERADOS_DIR) if f.endswith('.hl7')]

            for nome_arquivo in arquivos:
                caminho_origem = os.path.join(GERADOS_DIR, nome_arquivo)
                caminho_destino = os.path.join(ENVIADOS_DIR, nome_arquivo)

                with open(caminho_origem, 'r', encoding='utf-8', newline='') as f:
                    conteudo_hl7 = f.read()

                # Extrai imagens (se houver)
                pasta_imagens = os.path.join(ENVIADOS_DIR, "imagens")
                os.makedirs(pasta_imagens, exist_ok=True)
                prefixo = os.path.splitext(nome_arquivo)[0] + "_"
                extrair_imagens_de_hl7(conteudo_hl7, pasta_imagens, prefixo)

                # Converte HL7 para TXT
                txt_data = parse_hl7_to_txt(conteudo_hl7)

                if txt_data:
                    # Salva TXT localmente
                    pasta_txt = os.path.join(ENVIADOS_DIR, "txt")
                    os.makedirs(pasta_txt, exist_ok=True)
                    nome_txt = os.path.splitext(nome_arquivo)[0] + ".txt"
                    caminho_txt = os.path.join(pasta_txt, nome_txt)
                    with open(caminho_txt, "w", encoding="utf-8") as f:
                        f.write(txt_data)
                    logging.info(f"TXT salvo: {caminho_txt}")

                    # Envia ao Webhook
                    headers = {'Content-Type': 'text/plain; charset=utf-8'}
                    response = requests.post(
                        WEBHOOK_URL,
                        data=txt_data.encode('utf-8'),
                        headers=headers,
                        timeout=10
                    )

                    if response.status_code in (200, 201):
                        logging.info(f"Sucesso: {nome_arquivo} enviado ao Webhook.")
                        shutil.move(caminho_origem, caminho_destino)
                    else:
                        logging.error(f"Webhook recusou {nome_arquivo}: Status {response.status_code}")
                else:
                    logging.warning(f"Arquivo {nome_arquivo} sem dados válidos, movendo assim mesmo.")
                    shutil.move(caminho_origem, caminho_destino)

        except Exception as e:
            logging.error(f"Erro na thread de envio: {e}")

        time.sleep(CHECK_FILES_INTERVAL)

def main():
    # Inicia thread de processamento de arquivos
    thread_envio = Thread(target=task_sender_to_webhook, daemon=True)
    thread_envio.start()

    # Abre porta serial
    try:
        ser = serial.Serial(
            port=COM_PORT,
            baudrate=BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1
        )
        logging.info(f"Conectado à porta {COM_PORT} ({BAUD_RATE}bps). Aguardando dados do VIDAS 1600...")
    except Exception as e:
        logging.critical(f"Falha ao abrir porta serial: {e}")
        return

    buffer = ""

    while True:
        try:
            if ser.in_waiting > 0:
                buffer += ser.read(ser.in_waiting).decode('utf-8', errors='ignore')

            # Processa mensagens completas delimitadas por <SB> ... <EB><CR>
            while SB in buffer and EB in buffer:
                start_idx = buffer.find(SB)
                end_idx = buffer.find(EB)
                if len(buffer) <= end_idx + 1:
                    break

                # Inclui o <CR> após <EB>
                message_end_idx = end_idx + 2   # EB + CR
                if buffer[end_idx + 1] != CR:
                    # Caso raro: EB sem CR imediatamente após
                    message_end_idx = end_idx + 1

                hl7_message = buffer[start_idx:message_end_idx]
                buffer = buffer[message_end_idx:]

                # Salva arquivo .hl7
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = f"exame_vidas_{timestamp}.hl7"
                file_path = os.path.join(GERADOS_DIR, filename)

                with open(file_path, "w", encoding="utf-8", newline='') as f:
                    f.write(hl7_message)
                logging.info(f"Mensagem salva: {filename}")

                # Envia ACK de volta ao equipamento
                ack_bytes = generate_ack(hl7_message)
                if ack_bytes:
                    ser.write(ack_bytes)
                    ser.flush()
                    logging.info("ACK enviado ao VIDAS 1600.")

            time.sleep(READ_INTERVAL)

        except KeyboardInterrupt:
            logging.info("Finalizando por interrupção do usuário...")
            if ser.is_open:
                ser.close()
            break
        except Exception as e:
            logging.exception(f"Erro no loop serial: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()