import serial
import time
import requests
import logging
import datetime
import os
import shutil
import re
import sys
import base64
from threading import Thread
import json

# Import do módulo de limpeza compartilhado
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.file_cleanup import FileCleanupConfig, start_cleanup_thread
from shared.config_loader import load_config

# ================= CONFIGURAÇÕES =================
# As configurações abaixo são valores padrão.
# Para alterar COM_PORT ou FRANCHISE_CREDENTIAL_ID sem recompilar o .exe,
# edite o arquivo config_vidas1600.json que fica ao lado do executável.
# Se o arquivo não existir, ele será criado automaticamente na primeira execução.
_config, _config_status = load_config('vidas1600', {
    'com_port': 'COM5',
    'baud_rate': 9600,
    'franchise_credential_id': '85361c80-9688-47e9-8cb3-ed838a9b1832',
    'webhook_url': 'https://apoio.internal.vidaexame.com/api/integration/vidas1600?franchise_credential_id={franchise_credential_id}',
})

COM_PORT = _config['com_port']
BAUD_RATE = _config['baud_rate']
FRANCHISE_CREDENTIAL_ID = _config['franchise_credential_id']

# Webhook do VIDAS 1600
# Local: http://localhost:8039/api/integration/vidas1600
# Produção: https://apoio.internal.vidaexame.com/api/integration/vidas1600
WEBHOOK_URL = _config['webhook_url'].format(franchise_credential_id=FRANCHISE_CREDENTIAL_ID)

READ_INTERVAL = 0.1
CHECK_FILES_INTERVAL = 5
MAX_RETRY = 5
RETRY_INTERVAL = 60  # segundos entre tentativas de reenvio (1 minuto)
# =================================================

# Pastas de trabalho – na Área de Trabalho
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
BASE_DIR = os.path.join(DESKTOP, "AnalisadorVIDAS1600")
GERADOS_DIR = os.path.join(BASE_DIR, "gerados")
ENVIADOS_DIR = os.path.join(BASE_DIR, "enviados")
REQUISICOES_NAO_ENVIADAS_DIR = os.path.join(BASE_DIR, "requisições não enviadas")
LOG_FILE = os.path.join(BASE_DIR, "analisador_vidas1600.log")
# =================================================

# Garantir que as pastas existam
os.makedirs(GERADOS_DIR, exist_ok=True)
os.makedirs(ENVIADOS_DIR, exist_ok=True)
os.makedirs(REQUISICOES_NAO_ENVIADAS_DIR, exist_ok=True)

# Redirecionar stdout/stderr para evitar travamento em modo --noconsole (PyInstaller)
# Quando não há console, sys.stdout/sys.stderr podem ser None ou causar erro ao escrever
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8')]
)

# Caracteres de controle HL7 (MLLP)
SB = chr(0x0B)   # Start Block (VT)
EB = chr(0x1C)   # End Block (FS)
CR = chr(0x0D)   # Carriage Return

# ================= MAPEAMENTO DE TESTES → EXAM_CODE =================
# O VIDAS 1600 é um analisador bioquímico. Os Test Names abaixo são os
# nomes exatos que o equipamento envia no campo OBX-4 do HL7.
# Cada teste pertence a um tipo de exame (ExamCode) no sistema.
#
# Fonte: Protocolo de Aplicação VIDAS 1600 (REV. 12/23)
TEST_TO_EXAM_CODE = {
    # ── Função Hepática (HEP) ──
    'TGP': 'HEP',          # TGP (ALT)
    'TGO': 'HEP',          # TGO (AST)
    'Bili T': 'HEP',       # Bilirrubina Total
    'Bili D': 'HEP',       # Bilirrubina Direta
    'Falc': 'HEP',         # Fosfatase Alcalina
    'GGT': 'HEP',          # Gama GT
    'Alb': 'HEP',          # Albumina
    'PT': 'HEP',           # Proteína Total
    'ALFA': 'HEP',         # Alfa-1-Glicoproteína Ácida
    'COLIN': 'HEP',        # Colinesterase

    # ── Função Renal (REN) ──
    'Ureia': 'REN',        # Ureia (BUN)
    'Crea': 'REN',         # Creatinina
    'AUR': 'REN',          # Ácido Úrico

    # ── Glicose (GLI) ──
    'Gli': 'GLI',          # Glicose
    'HbA1c': 'GLI',        # HbA1c
    'FRU': 'GLI',          # Frutosamina

    # ── Lipídios (LIP) ──
    'Col': 'LIP',          # Colesterol Total
    'Trig': 'LIP',         # Triglicerídeos
    'HDL': 'LIP',          # HDL Direto

    # ── Eletrólitos (ELE) ──
    'CL': 'ELE',           # Cloreto
    'Ca': 'ELE',           # Cálcio
    'FOSF': 'ELE',         # Fósforo
    'MG': 'ELE',           # Magnésio

    # ── Marcadores Cardíacos (CARD) ──
    'CKNAC': 'CARD',       # CK-NAC
    'CKMB': 'CARD',        # CK-MB
    'LDH': 'CARD',         # LDH
    'PCR': 'CARD',         # PCR Turbidimétrico
    'PCRu': 'CARD',        # PCRu
    'PCRDUO': 'CARD',      # PCR Duo
    'PCRuDUO': 'CARD',     # PCRu Duo

    # ── Amilase/Lipase (AML) ──
    'Ami': 'AML',          # Amilase
    'LIP': 'AML',          # Lipase

    # ── Ferro/Anemia (FER) ──
    'Fe': 'FER',           # Ferro
    'FERRI': 'FER',        # Ferritina

    # ── Outros (OUT) ──
    'ASO': 'OUT',          # ASO Turbidimétrico
    'FR': 'OUT',           # FR Turbidimétrico
    'LAC': 'OUT',          # Lactato
    'PTUR': 'OUT',         # Proteína Urinária
}
# =====================================================================


def parse_hl7_to_dict(hl7_message: str) -> dict:
    """
    Extrai os dados de uma mensagem HL7 ORU^R01 do VIDAS 1600 (E-LAB).
    
    Estrutura esperada:
        MSH|^~\\&|E-LAB|ES-480|||20260520101830||ORU^R01|1|P|2.3.1||||0||UNICODE||
        PID|1||""||Mike||19851001000000|M|...
        OBR|1|12345678|10|E-LAB^ES-480|Y|20070413073253|20070413093253||||||Serum||||||||...
        OBX|1|NM|2|TBil|100|umol/L|0.00-1.00|H|||F||100|20070413093253|||
        OBX|2|NM|5|ALT|98.2|umol/L|||||||98.2|20070413093253|||
        OBX|3|NM|6|AST|26.4|umol/L|||||||26.4|20070413093253|||
    
    Retorna um dicionário com:
        - FileName: barcode (OBR-2) para identificação do procedimento
        - ExamCode: 'QUIM' (mantido para compatibilidade, mas results_by_exam é usado)
        - results_by_exam: dicionário agrupado por ExamCode, cada um contendo
          os testes pertencentes àquele tipo de exame
        - Campos dos testes também no nível raiz (para compatibilidade retroativa)
    """
    result = {}
    raw_tests = {}  # TestName → valor (com flag)
    
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        clean_message = clean_message.replace('\r\n', '\n').replace('\r', '\n')
        segments = clean_message.split('\n')
        
        # Extrai barcode do OBR-2 (Placer Order Number)
        barcode = ""
        sample_id = ""
        sample_type = ""
        for seg in segments:
            fields = seg.split('|')
            if fields[0] == 'OBR':
                if len(fields) > 2:
                    barcode = fields[2].strip()       # OBR-2: Placer Order Number (código de barras)
                if len(fields) > 3:
                    sample_id = fields[3].strip()       # OBR-3: Filler Order Number (número da amostra)
                if len(fields) > 15:
                    sample_type = fields[15].strip()    # OBR-15: Specimen Source (tipo de amostra)
                break
        
        # Prefere barcode, caso contrário usa sample_id
        tag_identifier = barcode if barcode else sample_id
        if not tag_identifier:
            tag_identifier = "DESCONHECIDO"
        
        result['FileName'] = tag_identifier
        result['ExamCode'] = 'QUIM'  # Mantido para compatibilidade retroativa
        
        # Extrai resultados dos segmentos OBX
        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue
            
            value_type = fields[2]
            if value_type == 'ED':
                continue  # ignora imagens
            
            # OBX-3: Observation Identifier (Test ID, pode ser numérico como "2" ou nome como "TBil")
            # OBX-4: Observation Sub-ID (Test Name, ex: "TBil", "ALT", "AST")
            # No formato E-LAB, OBX-3 é o número do teste e OBX-4 é o nome do teste
            test_id = fields[3].split('^')[0].strip() if len(fields) > 3 and fields[3] else ""
            test_name = fields[4].strip() if len(fields) > 4 and fields[4] else ""
            
            # Usa o nome do teste (OBX-4) como chave, fallback para o ID (OBX-3)
            key = test_name if test_name else test_id
            if not key:
                continue
            
            # OBX-5: Observation Value (resultado)
            value = fields[5].strip() if len(fields) > 5 and fields[5] else ""
            if not value:
                continue
            
            # OBX-8: Abnormal Flags (H=High, L=Low, N=Normal)
            abnormal_flag = fields[8].strip() if len(fields) > 8 and fields[8] else ""
            flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''
            
            # Monta o valor com flag se anormal
            result_value = value
            if flag:
                result_value = f"{value}{flag}"
            
            raw_tests[key] = result_value
            result[key] = result_value  # Mantém no nível raiz para compatibilidade
        
        # Agrupa resultados por ExamCode usando o mapeamento TEST_TO_EXAM_CODE
        results_by_exam = {}
        unmapped_tests = []
        
        for test_name, test_value in raw_tests.items():
            exam_code = TEST_TO_EXAM_CODE.get(test_name)
            if exam_code:
                if exam_code not in results_by_exam:
                    results_by_exam[exam_code] = {}
                results_by_exam[exam_code][test_name] = test_value
            else:
                # Teste não mapeado — vai para o ExamCode padrão 'QUIM'
                unmapped_tests.append(test_name)
                if 'QUIM' not in results_by_exam:
                    results_by_exam['QUIM'] = {}
                results_by_exam['QUIM'][test_name] = test_value
        
        result['results_by_exam'] = results_by_exam
        
        if unmapped_tests:
            logging.warning(f"Testes não mapeados para ExamCode (usando QUIM como fallback): {unmapped_tests}")
        
    except Exception as e:
        logging.error(f"Erro ao parsear HL7 do VIDAS 1600: {e}")
        return {}
    
    return result


def parse_hl7_to_txt(hl7_message: str) -> str:
    """
    Converte uma mensagem HL7 ORU^R01 (típica do VIDAS) para um texto simplificado.
    Estrutura do TXT:
        FileName: <barcode ou sample ID>
        <TestName>: <Valor> <Unidade> <Flag>
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
                    barcode = fields[2]
                if len(fields) > 3:
                    sample_id = fields[3]
                break

        # Prefere barcode, caso contrário usa sample_id
        amostra_id = barcode if barcode else sample_id
        if not amostra_id:
            amostra_id = "DESCONHECIDO"

        # Dicionário para acumular resultados (evita duplicatas)
        resultados = {}

        for seg in segments:
            fields = seg.split('|')
            if fields[0] != 'OBX' or len(fields) < 9:
                continue

            value_type = fields[2]
            if value_type == 'ED':
                continue  # ignora imagens

            # Identificador do teste
            test_id = fields[3].split('^')[0] if fields[3] else "TESTE_DESCONHECIDO"
            test_name = fields[4].strip() if len(fields) > 4 and fields[4] else ""

            value = fields[5] if len(fields) > 5 else ""
            unit = fields[6] if len(fields) > 6 else ""
            abnormal_flag = fields[8] if len(fields) > 8 else ""

            # Flag: N = normal, L = baixo, H = alto, outros mantidos
            flag = abnormal_flag if abnormal_flag and abnormal_flag != 'N' else ''

            # Usa nome do teste se disponível, senão usa o ID
            key = test_name if test_name else test_id

            # Se já existir o mesmo teste, mantemos o último
            resultados[key] = (value, unit, flag)

        if not resultados:
            logging.warning("Nenhum resultado encontrado na mensagem.")
            return ""

        # Monta linhas de saída
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


def generate_ack(hl7_message: str) -> bytes:
    """
    Gera um ACK HL7 (ACK^R01) em resposta a uma mensagem ORU do VIDAS 1600.
    Utiliza os identificadores da mensagem original para roteamento correto.
    
    Conforme documentação E-LAB:
    - MSH-3: Sending Application (E-LAB)
    - MSH-4: Sending Facility (ES-480/ES-380/ES-200)
    - MSH-5: Receiving Application (LIS)
    - MSH-9: Message Type (ACK^R01)
    - MSH-10: Message Control ID (mesmo ID da mensagem original)
    - MSH-16: Application Acknowledgment Type (0=paciente, 1=calibração, 2=controle)
    - MSA-1: AA=aceito, AE=erro, AR=rejeitado
    - MSA-2: Message Control ID (mesmo ID da mensagem original)
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
        
        # Extrai campos do MSH original para inverter origem/destino no ACK
        sending_app = fields[2] if len(fields) > 2 else "E-LAB"
        sending_fac = fields[3] if len(fields) > 3 else "ES-480"
        receiving_app = fields[4] if len(fields) > 4 else "LIS"
        receiving_fac = fields[5] if len(fields) > 5 else ""
        msg_id = fields[9] if len(fields) > 9 else ""
        app_ack_type = fields[15] if len(fields) > 15 else "0"
        dt_now = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

        # ACK com inversão de origem/destino
        ack = f"MSH|^~\\&|{receiving_app}|{receiving_fac}|{sending_app}|{sending_fac}|{dt_now}||ACK^R01|{msg_id}|P|2.3.1||||{app_ack_type}||UNICODE||{CR}"
        ack += f"MSA|AA|{msg_id}|Message Accepted|||0{CR}"
        return (SB + ack + EB + CR).encode('utf-8')
    except Exception as e:
        logging.error(f"Erro ao gerar ACK: {e}")
        return b''


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


def _enviar_payload_webhook(payload: dict, nome_arquivo: str, barcode: str) -> bool:
    """
    Envia um payload para o webhook com até MAX_RETRY tentativas.
    Retorna True se o envio foi bem-sucedido, False caso contrário.
    """
    headers = {'Content-Type': 'application/json'}

    for tentativa in range(1, MAX_RETRY + 1):
        try:
            logging.info(f"Enviando {nome_arquivo} (tag_identifier: {barcode}) para o webhook... [Tentativa {tentativa}/{MAX_RETRY}]")
            response = requests.post(
                WEBHOOK_URL,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in (200, 201):
                logging.info(f"✓ Sucesso ({response.status_code}): {nome_arquivo} enviado ao Webhook.")
                try:
                    resp_json = response.json()
                    msg = resp_json.get('message', 'OK')
                    logging.info(f"  Mensagem: {msg}")
                except:
                    pass
                return True
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
            elif response.status_code in (401, 403):
                logging.error(f"✗ ERRO {response.status_code}: Falha de autenticação para {nome_arquivo}.")
                logging.error(f"  Verifique o FRANCHISE_CREDENTIAL_ID: {FRANCHISE_CREDENTIAL_ID}")
                logging.error(f"  Resposta: {response.text[:500]}")
            elif response.status_code in (502, 503):
                logging.error(f"✗ ERRO {response.status_code}: Servidor indisponível para {nome_arquivo}.")
                logging.error(f"  O servidor pode estar fora do ar ou em manutenção.")
            else:
                logging.error(f"✗ Webhook recusou {nome_arquivo}: Status HTTP {response.status_code}")
                logging.error(f"  Resposta: {response.text[:500]}")

        except requests.exceptions.ConnectionError as e:
            logging.error(f"✗ ERRO DE CONEXÃO: Não foi possível conectar ao servidor para {nome_arquivo}.")
            logging.error(f"  URL: {WEBHOOK_URL}")
            logging.error(f"  Detalhe: {e}")
        except requests.exceptions.Timeout as e:
            logging.error(f"✗ TIMEOUT: O servidor não respondeu a tempo para {nome_arquivo} (30s).")
            logging.error(f"  URL: {WEBHOOK_URL}")
        except requests.exceptions.TooManyRedirects as e:
            logging.error(f"✗ ERRO: Muitos redirecionamentos ao acessar {WEBHOOK_URL}: {e}")
        except requests.exceptions.RequestException as e:
            logging.error(f"✗ ERRO DE REDE ao enviar {nome_arquivo}: {type(e).__name__}: {e}")
            logging.error(f"  URL: {WEBHOOK_URL}")

        if tentativa < MAX_RETRY:
            logging.warning(f"  Tentativa {tentativa}/{MAX_RETRY} falhou. Nova tentativa em {RETRY_INTERVAL}s...")
            time.sleep(RETRY_INTERVAL)

    return False


def task_sender_to_webhook():
    logging.info("Iniciando monitor de envio para Webhook...")
    logging.info(f"URL do Webhook: {WEBHOOK_URL}")
    logging.info(f"Verificando arquivos a cada {CHECK_FILES_INTERVAL}s na pasta: {GERADOS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
    
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
                caminho_nao_enviado = os.path.join(REQUISICOES_NAO_ENVIADAS_DIR, nome_arquivo)

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

                if not campos:
                    logging.warning(f"Arquivo {nome_arquivo} NÃO contém dados válidos.")
                    logging.warning(f"  Movendo para enviados sem processar.")
                    shutil.move(caminho_origem, caminho_destino)
                    continue

                # Verifica se tem FileName (tag_identifier) para identificar o procedimento
                if not campos.get('FileName') or campos.get('FileName') == 'DESCONHECIDO':
                    logging.error(f"✗ Arquivo {nome_arquivo}: FileName (barcode) não encontrado no HL7.")
                    logging.error(f"  Não é possível enviar sem código de barras — o tag_identifier é obrigatório.")
                    logging.error(f"  Movendo para '{REQUISICOES_NAO_ENVIADAS_DIR}'.")
                    shutil.move(caminho_origem, caminho_nao_enviado)
                    continue

                # Extrai resultados agrupados por ExamCode
                results_by_exam = campos.get('results_by_exam', {})
                total_tests = sum(len(tests) for tests in results_by_exam.values())
                logging.info(f"Arquivo {nome_arquivo}: {total_tests} teste(s) em {len(results_by_exam)} exame(s) (barcode: {campos.get('FileName')})")

                # Salva TXT em ENVIADOS_DIR/txt/ (debug)
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

                # Salva JSON em ENVIADOS_DIR/json/ (debug)
                try:
                    pasta_json = os.path.join(ENVIADOS_DIR, "json")
                    os.makedirs(pasta_json, exist_ok=True)
                    nome_json = os.path.splitext(nome_arquivo)[0] + ".json"
                    caminho_json = os.path.join(pasta_json, nome_json)
                    
                    with open(caminho_json, "w", encoding="utf-8") as f:
                        f.write(json.dumps(campos, indent=2, ensure_ascii=False))
                    logging.info(f"JSON salvo: {caminho_json}")
                except Exception as e:
                    logging.warning(f"Erro ao salvar JSON de debug para {nome_arquivo}: {e}")

                # Envia para o webhook — uma requisição por ExamCode
                all_success = True
                
                if not results_by_exam:
                    logging.warning(f"Arquivo {nome_arquivo}: Nenhum resultado agrupado por ExamCode. Enviando formato legado...")
                    # Fallback: envia tudo em uma única requisição com ExamCode=QUIM
                    results_by_exam = {'QUIM': {k: v for k, v in campos.items() if k not in ('FileName', 'ExamCode', 'results_by_exam')}}
                
                for exam_code, exam_tests in results_by_exam.items():
                    if not exam_tests:
                        continue
                    
                    payload = {
                        'FileName': campos.get('FileName', 'DESCONHECIDO'),
                        'ExamCode': exam_code,
                    }
                    payload.update(exam_tests)
                    
                    # Tenta enviar com retry
                    enviado = _enviar_payload_webhook(payload, f"{nome_arquivo} → ExamCode={exam_code}", campos.get('FileName', 'DESCONHECIDO'))
                    
                    if not enviado:
                        all_success = False
                
                # Move o arquivo apenas se TODOS os ExamCodes foram enviados com sucesso
                if all_success:
                    shutil.move(caminho_origem, caminho_destino)
                    logging.info(f"  Arquivo movido para: {caminho_destino}")
                else:
                    logging.error(f"✗ Falha definitiva: {nome_arquivo} não foi enviado após {MAX_RETRY} tentativas.")
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
    logging.info("=" * 60)
    logging.info("Analisador VIDAS 1600 - Serviço de Integração HL7")
    logging.info(f"Data/Hora de início: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info(f"Porta Serial: {COM_PORT} | Baud Rate: {BAUD_RATE}")
    logging.info(f"Webhook: {WEBHOOK_URL}")
    logging.info(f"Pastas: gerados={GERADOS_DIR} | enviados={ENVIADOS_DIR} | não enviados={REQUISICOES_NAO_ENVIADAS_DIR}")
    logging.info(f"Reenvio: até {MAX_RETRY} tentativas com intervalo de {RETRY_INTERVAL}s")
    logging.info("=" * 60)

    thread_envio = Thread(target=task_sender_to_webhook, daemon=True)
    thread_envio.start()
    logging.info("Thread de envio iniciada.")

    # Configurar e iniciar thread de limpeza de arquivos
    cleanup_config = FileCleanupConfig()
    cleanup_config.log_file_path = LOG_FILE
    cleanup_config.cleanup_directories = [ENVIADOS_DIR, REQUISICOES_NAO_ENVIADAS_DIR]
    start_cleanup_thread(cleanup_config)

    ser = None
    tentativas_porta = 0
    MAX_TENTATIVAS_PORTA = 5

    while ser is None and tentativas_porta < MAX_TENTATIVAS_PORTA:
        try:
            ser = serial.Serial(
                port=COM_PORT,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1
            )
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

            # Processa mensagens completas delimitadas por <SB> ... <EB><CR>
            while SB in buffer and EB in buffer:
                start_idx = buffer.find(SB)
                end_idx = buffer.find(EB)
                if len(buffer) <= end_idx + 1:
                    break

                # Inclui o <CR> após <EB>
                message_end_idx = end_idx + 2  # EB + CR
                if buffer[end_idx + 1] != CR:
                    # Caso raro: EB sem CR imediatamente após
                    message_end_idx = end_idx + 1

                hl7_message = buffer[start_idx:message_end_idx]
                buffer = buffer[message_end_idx:]

                # Verifica se é uma mensagem ORU^R01 (resultados de paciente)
                # Ignora mensagens QRY, QCK, DSR, ACK
                clean_msg = hl7_message.replace(SB, '').replace(EB, '')
                is_oru = False
                for line in clean_msg.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
                    if line.startswith('MSH|') and 'ORU^R01' in line:
                        is_oru = True
                        break

                if not is_oru:
                    # Não é resultado de paciente — envia ACK se necessário e ignora
                    logging.info(f"Mensagem não-ORU recebida, ignorando (pode ser QRY/QCK/DSR/ACK).")
                    # Tenta enviar ACK genérico mesmo assim
                    ack_bytes = generate_ack(hl7_message)
                    if ack_bytes:
                        try:
                            ser.write(ack_bytes)
                            ser.flush()
                            logging.info(f"  ACK genérico enviado.")
                        except serial.SerialException as e:
                            logging.error(f"✗ Erro ao enviar ACK: {e}")
                    continue

                # Verifica se é resultado de paciente (MSH-16 = 0 ou vazio)
                # MSH-16: 0=paciente, 1=calibração, 2=controle
                is_patient = True
                for line in clean_msg.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
                    if line.startswith('MSH|'):
                        fields = line.split('|')
                        if len(fields) > 16:
                            app_ack = fields[16].strip() if fields[16] else ''
                            if app_ack == '1':
                                is_patient = False
                                logging.info(f"Mensagem de CALIBRAÇÃO recebida, ignorando.")
                            elif app_ack == '2':
                                is_patient = False
                                logging.info(f"Mensagem de CONTROLE DE QUALIDADE recebida, ignorando.")
                        break

                if not is_patient:
                    # Envia ACK mesmo para calibração/controle
                    ack_bytes = generate_ack(hl7_message)
                    if ack_bytes:
                        try:
                            ser.write(ack_bytes)
                            ser.flush()
                            logging.info(f"  ACK enviado (calibração/controle).")
                        except serial.SerialException as e:
                            logging.error(f"✗ Erro ao enviar ACK: {e}")
                    continue

                # Salva arquivo .hl7
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = f"vidas1600_{timestamp}.hl7"
                file_path = os.path.join(GERADOS_DIR, filename)

                try:
                    with open(file_path, "w", encoding="utf-8", newline='') as f:
                        f.write(hl7_message)
                    mensagens_processadas += 1
                    logging.info(f"✓ Mensagem HL7 salva: {filename} ({len(hl7_message)} bytes)")
                except OSError as e:
                    logging.error(f"✗ Erro ao salvar arquivo {filename}: {e} (espaço em disco?)")
                    continue

                # Envia ACK de volta ao equipamento
                ack_bytes = generate_ack(hl7_message)
                if ack_bytes:
                    try:
                        ser.write(ack_bytes)
                        ser.flush()
                        logging.info(f"  ACK enviado ao equipamento ({len(ack_bytes)} bytes).")
                    except serial.SerialException as e:
                        logging.error(f"✗ Erro ao enviar ACK pela serial: {e}")
                        logging.error(f"  Tentando reconectar...")
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