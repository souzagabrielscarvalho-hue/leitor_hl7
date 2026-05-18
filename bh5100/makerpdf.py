import serial
import time
import logging
import os
from fpdf import FPDF # fpdf2 usa o mesmo nome de import

# ================= CONFIGURAÇÕES =================
COM_PORT = 'COM5'
BAUD_RATE = 9600
READ_INTERVAL = 1
LOG_FILE = 'analisador_bh5100.log'
# =================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)

SB = chr(0x0B) 
EB = chr(0x1C) 
CR = chr(0x0D) 

def clean_for_pdf(text) -> str:
    """Remove caracteres que não podem ser codificados em Latin-1 para evitar erros no PDF."""
    if not text: return ""
    return str(text).encode('latin-1', 'ignore').decode('latin-1')

def save_to_pdf(data_rows: list) -> None:
    try:
        desktop = os.path.join(os.path.join(os.environ['USERPROFILE']), 'Desktop')
        filename = f"Resultado_Exame_{int(time.time())}.pdf"
        filepath = os.path.join(desktop, filename)

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, txt="Relatorio de Analise Hematologica", ln=True, align='C')
        pdf.ln(10)

        # Cabeçalho
        pdf.set_font("Arial", 'B', 10)
        col_width = [40, 30, 30, 60, 25]
        headers = ['Parametro', 'Valor', 'Unidade', 'Ref. Range', 'Flag']
        
        for i, h in enumerate(headers):
            pdf.cell(col_width[i], 10, h, border=1)
        pdf.ln()

        # Dados
        pdf.set_font("Arial", '', 10)
        for row in data_rows:
            # Verifica se a linha tem dados antes de tentar escrever
            if any(row):
                for i, item in enumerate(row):
                    # Limpa cada célula antes de escrever no PDF
                    safe_text = clean_for_pdf(item)
                    pdf.cell(col_width[i], 10, safe_text, border=1)
                pdf.ln()

        pdf.output(filepath)
        logging.info(f"PDF Gerado: {filename}")
    except Exception as e:
        logging.error(f"Erro ao salvar PDF: {e}")

def parse_hl7_to_list(hl7_message: str) -> list:
    try:
        clean_message = hl7_message.replace(SB, '').replace(EB, '')
        segments = clean_message.split(CR)
        
        results = []
        for segment in segments:
            fields = segment.split('|')
            # Ignora segmentos vazios ou ED (Histogramas/Base64) que quebram o PDF
            if fields[0] == 'OBX' and len(fields) >= 9:
                if fields[2] == 'ED' or 'Histogram' in fields[3]:
                    continue
                
                results.append([
                    fields[3], # ID
                    fields[5], # Valor
                    fields[6], # Unidade
                    fields[7], # Ref
                    fields[8] if len(fields) > 8 and fields[8] else "N" # Flag
                ])
        return results
    except Exception as e:
        logging.error(f"Erro no parse: {e}")
        return []

def main():
    try:
        ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
        # Removi o emoji para evitar erro no terminal Windows
        logging.info(f"Escutando na porta {COM_PORT}...")
    except Exception as e:
        logging.critical(f"Erro ao abrir {COM_PORT}: {e}")
        return

    buffer = ""
    while True:
        try:
            if ser.in_waiting > 0:
                raw = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                buffer += raw

                if SB in buffer and EB in buffer:
                    start = buffer.find(SB)
                    end = buffer.find(EB) + 1
                    msg = buffer[start:end]
                    buffer = buffer[end:]

                    logging.info("Mensagem recebida, processando...")
                    data = parse_hl7_to_list(msg)
                    if data:
                        save_to_pdf(data)
            time.sleep(0.5)
        except KeyboardInterrupt:
            logging.info("Encerrando...")
            ser.close()
            break
        except Exception as e:
            logging.error(f"Erro no loop: {e}")

if __name__ == "__main__":
    main()