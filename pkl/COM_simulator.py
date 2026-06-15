"""
COM_simulator.py — Simulador ASTM E1394-97 para o analisador PKL 125

Envia mensagens ASTM (STX/ETX frames) via porta serial para testar o pkl.py.
Simula o protocolo de handshake ENQ/ACK e envio de frames numerados.

Uso:
    python COM_simulator.py COM3

Se nenhuma porta for informada, usa 'COM1' como padrão.
"""

import serial
import time
import sys

# Constantes ASTM
STX = chr(0x02)   # Start of Text
ETX = chr(0x03)   # End of Text
ENQ = chr(0x05)   # Enquiry
ACK = chr(0x06)   # Acknowledge
NAK = chr(0x15)   # Not Acknowledge
EOT = chr(0x04)   # End of Transmission
CR  = chr(0x0D)   # Carriage Return
LF  = chr(0x0A)   # Line Feed


def calculate_checksum(data: str) -> str:
    """Calcula o checksum ASTM: soma dos bytes módulo 256, hex de 2 dígitos."""
    total = sum(ord(c) for c in data) % 256
    return f"{total:02X}"


def build_astm_frame(frame_number: int, content: str) -> bytes:
    """
    Constrói um frame ASTM completo em bytes:
    STX + frame_number(ASCII) + content + ETX + checksum(2 hex) + CR + LF
    """
    fn_char = chr(frame_number + 48)  # 1→'1', 2→'2', ..., 7→'7', 0→'0'
    data_for_checksum = fn_char + content + ETX
    checksum = calculate_checksum(data_for_checksum)
    frame = STX + fn_char + content + ETX + checksum + CR + LF
    return frame.encode('ascii', errors='ignore')


def wait_for_ack(ser: serial.Serial, timeout_ms: int = 5000) -> bool:
    """Aguarda ACK do receptor."""
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        if ser.in_waiting > 0:
            data = ser.read(1)
            if data.decode('ascii', errors='ignore') == ACK:
                return True
        time.sleep(0.05)
    return False


def enviar_astm_serial(porta_com: str, mensagem: str) -> None:
    """
    Envia uma mensagem ASTM completa via porta serial com handshake ENQ/ACK.

    Protocolo:
    1. Envia ENQ, aguarda ACK
    2. Para cada linha: envia frame, aguarda ACK
    3. Envia EOT
    """
    try:
        ser = serial.Serial(porta_com, 19200, timeout=1)
        print(f"Conectado à porta {porta_com} @ 19200 baud")

        lines = mensagem.strip().split('\n')
        print(f"Enviando {len(lines)} frames ASTM...")

        # 1. ENQ → aguardar ACK
        print("[1] Enviando ENQ...")
        ser.write(ENQ.encode('ascii'))
        ser.flush()

        if not wait_for_ack(ser):
            print("✗ ACK não recebido após ENQ — abortando")
            ser.close()
            return
        print("  ✓ ACK recebido")

        # 2. Enviar cada frame
        for i, line in enumerate(lines):
            frame_number = (i + 1) % 8  # 1-7, 0, 1-7, 0...
            frame_bytes = build_astm_frame(frame_number, line)

            print(f"[{i+2}] Enviando frame {frame_number}: {line[:60]}...")
            ser.write(frame_bytes)
            ser.flush()

            if not wait_for_ack(ser):
                print(f"  ✗ ACK não recebido para frame {frame_number} — abortando")
                ser.close()
                return
            print(f"  ✓ ACK recebido para frame {frame_number}")

        # 3. EOT
        print("[FIM] Enviando EOT...")
        ser.write(EOT.encode('ascii'))
        ser.flush()

        ser.close()
        print("✓ Mensagem ASTM enviada com sucesso!")

    except serial.SerialException as e:
        print(f"✗ Erro serial: {e}")
    except Exception as e:
        print(f"✗ Erro: {e}")


# ═══════════════════════════════════════════════════════════
# Mensagens de exemplo
# ═══════════════════════════════════════════════════════════

# Exemplo 1: Hemograma completo (1 paciente, 1 ordem, vários resultados)
MSG_HEMOGRAMA = (
    # H - Header
    "H|\\^&|||PKL125^1.0|||||||P|1|20260428120000\n"
    # P - Patient
    "P|1||123456||SILVA^MARIA||19850315|F|||||||||||||||||||\n"
    # O - Order (specimen_id = tag_id)
    "O|2|ABC1234567^^^^N||^^^HEMO^WBC\\^^^HEMO^RBC\\^^^HEMO^HGB\\^^^HEMO^HCT\\^^^HEMO^PLT|R|20260428120000|||||||||SANGUE||||||||||O\n"
    # R - Results
    "R|3|^^^HEMO^WBC|WBC|7.5|10^9/L|4.00-10.00|N|F|||ABC1234567\n"
    "R|4|^^^HEMO^RBC|RBC|4.49|10^12/L|3.50-5.50|N|F|||ABC1234567\n"
    "R|5|^^^HEMO^HGB|HGB|135|g/L|115-155|N|F|||ABC1234567\n"
    "R|6|^^^HEMO^HCT|HCT|42.1|%|37.0-50.0|N|F|||ABC1234567\n"
    "R|7|^^^HEMO^PLT|PLT|250|10^9/L|150-400|N|F|||ABC1234567\n"
    # L - Terminator
    "L|1|N"
)

# Exemplo 2: Múltiplos pacientes (simula 2 exames em sequência)
MSG_MULTIPLOS = (
    # --- Paciente 1 ---
    "H|\\^&|||PKL125^1.0|||||||P|1|20260428120000\n"
    "P|1||TAG001||SOUZA^JOSE||19900120|M|||||||||||||||||||\n"
    "O|2|TAG001^^^^N||^^^HEMO^WBC\\^^^HEMO^RBC\\^^^HEMO^HGB|R|20260428120000|||||||||SANGUE||||||||||O\n"
    "R|3|^^^HEMO^WBC|WBC|5.2|10^9/L|4.00-10.00|N|F|||TAG001\n"
    "R|4|^^^HEMO^RBC|RBC|4.80|10^12/L|3.50-5.50|N|F|||TAG001\n"
    "R|5|^^^HEMO^HGB|HGB|142|g/L|115-155|N|F|||TAG001\n"
    "L|1|N\n"
    # --- Paciente 2 ---
    "H|\\^&|||PKL125^1.0|||||||P|1|20260428120100\n"
    "P|1||TAG002||LIMA^ANA||19750710|F|||||||||||||||||||\n"
    "O|2|TAG002^^^^N||^^^HEMO^WBC\\^^^HEMO^RBC\\^^^HEMO^HGB|R|20260428120100|||||||||SANGUE||||||||||O\n"
    "R|3|^^^HEMO^WBC|WBC|11.2|10^9/L|4.00-10.00|H|F|||TAG002\n"
    "R|4|^^^HEMO^RBC|RBC|3.90|10^12/L|3.50-5.50|N|F|||TAG002\n"
    "R|5|^^^HEMO^HGB|HGB|98|g/L|115-155|L|F|||TAG002\n"
    "L|1|N"
)


if __name__ == "__main__":
    porta = sys.argv[1] if len(sys.argv) > 1 else 'COM1'
    print("=" * 60)
    print("Simulador ASTM E1394-97 para PKL 125")
    print("=" * 60)
    print("Escolha uma mensagem para enviar:")
    print("  1 - Hemograma simples (1 paciente)")
    print("  2 - Múltiplos pacientes (2 exames)")
    print()

    escolha = input("Opção [1]: ").strip() or "1"

    if escolha == "2":
        msg = MSG_MULTIPLOS
    else:
        msg = MSG_HEMOGRAMA

    print()
    enviar_astm_serial(porta, msg)
