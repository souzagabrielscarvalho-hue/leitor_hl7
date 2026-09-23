#!/usr/bin/env python3
"""
### 2. Requisição GET (Bidirecional)
Recebe frame ASTM 'Q' (query) da máquina PKL via serial,
extrai o número da etiqueta (tag_id) e consulta a API VIDA.

Uso:
    python test_get_only.py
"""

import json
import time

import requests
import serial

# ═══════════════════════════════════════════════════════════
# CONSTANTES ASTM E1394-97
# ═══════════════════════════════════════════════════════════

STX = chr(0x02)
ETX = chr(0x03)
ENQ = chr(0x05)
ACK = chr(0x06)
NAK = chr(0x15)
EOT = chr(0x04)
CR  = chr(0x0D)
LF  = chr(0x0A)

# ═══════════════════════════════════════════════════════════
# CONFIGURAÇÕES (carregadas do config_pkl.json)
# ═══════════════════════════════════════════════════════════

with open("config_pkl.json", "r", encoding="utf-8") as f:
    _CONFIG = json.load(f)

COM_PORT     = _CONFIG["com_port"]
BAUD_RATE    = _CONFIG["baud_rate"]
FRANCHISE_ID = _CONFIG["franchise_credential_id"]
PKL_MACHINE  = _CONFIG["pkl_machine_id"]

ORDERS_API_URL = (
    "https://apoio.internal.vidaexame.com/api/integration/pkl-125"
    "?franchise_credential_id={franchise_credential_id}"
    "&tag_id={tag_id}"
    "&pkl_machine_id={pkl_machine_id}"
)

# ═══════════════════════════════════════════════════════════
# FUNÇÕES ASTM
# ═══════════════════════════════════════════════════════════

def calculate_checksum(data: str) -> str:
    """Soma dos bytes módulo 256, hex de 2 dígitos."""
    total = sum(ord(c) for c in data) % 256
    return f"{total:02X}"


def detect_astm_frame(buffer: str):
    """
    Procura no buffer um frame ASTM completo:
    STX ... ETX + checksum(2) + CR + LF
    Retorna (frame, buffer_restante) ou (None, buffer).
    """
    while STX in buffer and ETX in buffer:
        stx_idx = buffer.index(STX)
        etx_idx = buffer.index(ETX, stx_idx)
        frame_end = etx_idx + 5          # ETX + 2 checksum + CR + LF
        if frame_end <= len(buffer):
            frame = buffer[stx_idx:frame_end]
            remaining = buffer[frame_end:]
            return (frame, remaining) if len(frame) >= 7 else (None, remaining)
        return None, buffer
    return None, buffer


def parse_astm_frame(frame: str) -> dict | None:
    """Faz o parse de um frame ASTM já isolado."""
    stx_idx = frame.find(STX)
    etx_idx = frame.find(ETX, stx_idx + 1)
    if stx_idx == -1 or etx_idx == -1 or etx_idx <= stx_idx + 2:
        return None

    frame_number = ord(frame[stx_idx + 1]) - 48
    content = frame[stx_idx + 2:etx_idx]
    fields = content.split("|")
    record_type = content[0] if content else ""

    checksum_received = frame[etx_idx + 1:etx_idx + 3] if etx_idx + 2 < len(frame) else ""
    data_for_checksum = frame[stx_idx + 1:etx_idx + 1]
    checksum_calculated = calculate_checksum(data_for_checksum)
    checksum_valid = checksum_received.upper() == checksum_calculated.upper()

    return {
        "type": record_type,
        "content": content,
        "fields": fields,
        "seq": frame_number,
        "checksum_received": checksum_received,
        "checksum_calculated": checksum_calculated,
        "checksum_valid": checksum_valid,
    }


# ═══════════════════════════════════════════════════════════
# REQUISIÇÃO GET
# ═══════════════════════════════════════════════════════════

def get_exams_by_tag(tag_id: str) -> dict | None:
    """
    Consulta a API VIDA com franchise_credential_id, tag_id e pkl_machine_id.
    Retorna o JSON da resposta ou None em caso de erro.
    """
    url = ORDERS_API_URL.format(
        franchise_credential_id=FRANCHISE_ID,
        tag_id=tag_id,
        pkl_machine_id=PKL_MACHINE,
    )
    print(f"\n[GET] {url}")

    try:
        resp = requests.get(url, timeout=10)
        print(f"[RESPONSE] HTTP {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            print(f"[RESPONSE] Body:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
            return data

        print(f"[RESPONSE] Erro: {resp.text[:500]}")
        return None

    except requests.exceptions.Timeout:
        print("[ERROR] Timeout na requisição GET")
    except Exception as e:
        print(f"[ERROR] {e}")
    return None


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("PKL GET Test — Recebe frame Q e consulta API VIDA")
    print(f"Porta: {COM_PORT} @ {BAUD_RATE} baud")
    print("=" * 60)

    ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
    buffer = ""

    try:
        while True:
            # Lê tudo o que chegou na serial
            if ser.in_waiting > 0:
                chunk = ser.read(ser.in_waiting).decode("ascii", errors="replace")
                buffer += chunk

            # Tenta isolar um frame ASTM completo
            frame, buffer = detect_astm_frame(buffer)
            if not frame:
                time.sleep(0.05)
                continue

            parsed = parse_astm_frame(frame)
            if not parsed:
                print("[ASTM] Frame inválido — ignorado")
                continue

            print(f"\n[ASTM] Frame recebido | tipo={parsed['type']} | seq={parsed['seq']} | "
                  f"checksum={'OK' if parsed['checksum_valid'] else 'FALHA'}")

            # Processa apenas frames do tipo Q (Query)
            if parsed["type"] == "Q":
                fields = parsed["fields"]
                specimen_raw = fields[2] if len(fields) > 2 else ""
                tag_id = specimen_raw.lstrip("^") if specimen_raw else ""

                print(f"[ASTM] Query | tag_id extraído: {tag_id}")
                if tag_id:
                    get_exams_by_tag(tag_id)
                else:
                    print("[WARN] Query sem tag_id — nada a consultar")
            else:
                print(f"[ASTM] Frame tipo '{parsed['type']}' ignorado (aguardando Q)")

    except KeyboardInterrupt:
        print("\n\n[INFO] Interrompido pelo usuário. Encerrando...")
    finally:
        ser.close()
        print("[INFO] Porta serial fechada.")


if __name__ == "__main__":
    main()
