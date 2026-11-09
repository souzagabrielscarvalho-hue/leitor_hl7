"""
COM_simulator.py — Simulador do analisador bioquímico URIT CA Series (HL7/MLLP).

Faz o papel do analisador ("cliente" no manual) e conversa com o
ca_series.py ("servidor"/LIS) por uma porta serial, seguindo os exemplos
da seção 4 do manual "CA Series ... HL7 Interface Development Reference
Manual" V1.01:

  paciente  ORU^R01 (MSH.16=0)  → espera ACK^R01      (seção 4.1)
  qc        ORU^R01 (MSH.16=2)  → espera ACK^R01      (seção 4.2)
  consulta  QRY^Q02             → espera QCK^Q02 e, se vier, DSR^Q03,
                                  respondido com ACK^Q03 (seção 4.3)

O leitor e o simulador não podem abrir a mesma COM: use um par virtual
(com0com, ex.: COM7 <-> COM8), com o ca_series.py numa ponta e o simulador
na outra.

Uso:
    python COM_simulator.py                         # menu interativo
    python COM_simulator.py --port COM8 --etiqueta 151020262929
    python COM_simulator.py --modo qc --port COM8
    python COM_simulator.py --modo consulta --port COM8 --etiqueta 151020262929
    python COM_simulator.py --dry-run               # mostra a mensagem e sai
    python COM_simulator.py --list-ports

Configuração (config_simulador_ca_series.json, ao lado do .exe ou deste
script): porta, velocidade, etiqueta, timeout, modo e a lista de itens do
resultado de paciente. A linha de comando tem prioridade sobre o JSON. O .exe
cria o JSON com os valores padrão se ele não existir.

O .exe espera um Enter antes de fechar, para dar tempo de ler o resultado
(desligue com "pausar_ao_final": false ou --sem-pausa).
"""

import argparse
import datetime
import json
import os
import traceback
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

SB = b"\x0b"
EB = b"\x1c"
CR = b"\x0d"

ETIQUETA_PADRAO = "151020262929"
PORTA_PADRAO = "COM8"
BAUD_PADRAO = 9600
APP = "URIT"
MODELO = "CA-XXX"

# (nº item, nome, resultado, unidade, referência, flag, absorbância)
# Nomes no padrão URIT; valores plausíveis (os do manual são de teste).
ITENS_PACIENTE: List[Tuple[str, str, str, str, str, str, str]] = [
    ("0", "ALB", "42.3", "g/L", "35.0-55.0", "N", "0.4512"),
    ("1", "TP", "71.8", "g/L", "60.0-85.0", "N", "0.3120"),
    ("2", "GLB", "29.5", "g/L", "20.0-40.0", "N", ""),
    ("3", "A/G", "1.43", "", "1.20-2.40", "N", ""),
    ("4", "ALT", "62", "U/L", "0-50", "H", "-0.0213"),
    ("5", "AST", "35", "U/L", "0-40", "N", "-0.0188"),
    ("6", "TB", "12.4", "umol/L", "2.0-20.0", "N", "0.0951"),
    ("7", "DB", "3.1", "umol/L", "0.0-8.0", "N", "0.0402"),
    ("8", "IBIL", "9.3", "umol/L", "0.0-20.0", "N", ""),
    ("9", "GLU", "5.42", "mmol/L", "3.90-6.10", "N", "0.2875"),
    ("10", "UREA", "5.8", "mmol/L", "2.9-8.2", "N", "-0.1102"),
    ("11", "CREA", "78", "umol/L", "44-133", "N", "0.0660"),
    ("12", "TC", "6.12", "mmol/L", "0.00-5.20", "H", "0.3311"),
    ("13", "TG", "1.45", "mmol/L", "0.00-1.70", "N", "0.2044"),
]

NOME_CONFIG = "config_simulador_ca_series.json"
CONFIG_PADRAO: Dict[str, Any] = {
    "port": PORTA_PADRAO,
    "baud_rate": BAUD_PADRAO,
    "etiqueta": ETIQUETA_PADRAO,
    "timeout": 10,
    "modo": "",
    "pausar_ao_final": True,
    "rack": "N0001",
    "tubo": "1",
    "itens": [
        {"nome": nome, "valor": val, "unidade": uni, "referencia": ref,
         "flag": flag, "absorbancia": absb}
        for (_no, nome, val, uni, ref, flag, absb) in ITENS_PACIENTE
    ],
}


def pasta_do_programa() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def carregar_config(caminho: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """
    Lê o JSON de apoio e completa com os padrões. Devolve (config, situação).
    O .exe cria o arquivo com os padrões se ele não existir.
    """
    caminho = caminho or os.path.join(pasta_do_programa(), NOME_CONFIG)
    config = json.loads(json.dumps(CONFIG_PADRAO))
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8-sig") as f:
                dados = json.load(f)
            if not isinstance(dados, dict):
                raise ValueError("o conteudo precisa ser um objeto JSON { ... }")
            config.update({k: v for k, v in dados.items() if not k.startswith("_")})
            return config, f"lido de {caminho}"
        except (OSError, ValueError) as e:
            return config, f"ERRO ao ler {caminho} ({e}); usando valores padrao"
    if getattr(sys, "frozen", False):
        try:
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump(CONFIG_PADRAO, f, indent=4, ensure_ascii=False)
            return config, f"criado com valores padrao em {caminho}"
        except OSError as e:
            return config, f"nao foi possivel criar {caminho} ({e}); usando padrao"
    return config, "nao encontrado; usando valores padrao"


def itens_da_config(config: Dict[str, Any]) -> List[Tuple[str, str, str, str, str, str, str]]:
    itens = []
    for i, item in enumerate(config.get("itens") or [], start=0):
        if not isinstance(item, dict) or not str(item.get("nome", "")).strip():
            continue
        itens.append((
            str(item.get("item_no", i)), str(item["nome"]).strip(),
            str(item.get("valor", "")), str(item.get("unidade", "")),
            str(item.get("referencia", "")), str(item.get("flag", "N") or "N"),
            str(item.get("absorbancia", "")),
        ))
    return itens or list(ITENS_PACIENTE)


def _agora() -> datetime.datetime:
    return datetime.datetime.now()


def _control_id(agora: datetime.datetime) -> str:
    """MSH.10: YYYYMMDD + número de série de 4 dígitos (manual, MSH.10)."""
    return agora.strftime("%Y%m%d") + agora.strftime("%M%S")


def mllp(segmentos: List[str]) -> bytes:
    corpo = "\r".join(segmentos) + "\r"
    return SB + corpo.encode("latin-1", errors="replace") + EB + CR


def msg_paciente(
    etiqueta: str, rack: str = "N0001", tubo: str = "1",
    itens: Optional[List[Tuple[str, str, str, str, str, str, str]]] = None,
) -> bytes:
    agora = _agora()
    ts = agora.strftime("%Y%m%d%H%M%S")
    cid = _control_id(agora)
    data = agora.strftime("%Y/%m/%d")
    segs = [
        f"MSH|^~\\&|{APP}|{MODELO}|||{ts}||ORU^R01|{cid}|p|2.3.1||||0||ANSI|||",
        f"PID|1|OutPatient001|InPatient001|Bed001|Paciente Teste||45|M||||"
        f"Year||||||||||||||{rack}|{tubo}||||",
        f"OBR|1|{etiqueta}|{cid}|{APP}^{MODELO}|0||{data}||||||ClinicalInfo001||"
        f"Serum|Doctor001|Dep001|||||||||||||||||||||||||||||",
    ]
    for i, (no, nome, val, uni, ref, flag, absb) in enumerate(itens or ITENS_PACIENTE, start=1):
        segs.append(
            f"OBX|{i}|NM|{no}|{nome}|{val}|{uni}|{ref}|{flag}|||F||{absb}|{data}||Server||"
        )
    return mllp(segs)


def msg_qc() -> bytes:
    """CQ de ALT nível 2 (exemplo da seção 4.2, com data atual)."""
    agora = _agora()
    ts = agora.strftime("%Y%m%d%H%M%S")
    validade = (agora + datetime.timedelta(days=180)).strftime("%Y%m%d")
    segs = [
        f"MSH|^~\\&|{APP}|{MODELO}|||{ts}||ORU^R01||p|2.3.1||||2||ANSI|||",
        "PID|||||||||||||||||||||||||||||||",
        f"OBR|1|7|ALT|{APP}^{MODELO}|||{ts}||||1|1292UN||1292UN|{validade}||"
        f"Level 2|34.0000|3.0000|35.1000|||||T14,T2|||||||||||||||||||||",
    ]
    return mllp(segs)


def msg_consulta(etiqueta: str) -> bytes:
    agora = _agora()
    ts = agora.strftime("%Y%m%d%H%M%S")
    segs = [
        f"MSH|^~\\&|{APP}|{MODELO}|||{ts}||QRY^Q02|{ts}|p|2.3.1||||||ANSI|||",
        f"QRD|{ts}|R|D|1|||RD|{etiqueta}|OTH|||T|",
        f"QRF|{MODELO}|||||RCT|COR|ALL||",
    ]
    return mllp(segs)


def msg_ack_q03(control_id: str) -> bytes:
    ts = _agora().strftime("%Y%m%d%H%M%S")
    return mllp([
        f"MSH|^~\\&|{APP}|{MODELO}|||{ts}||ACK^Q03|{control_id}|p|2.3.1||||||ANSI|||",
        f"MSA|AA|{control_id}|Message accepted|||0|",
        "ERR|0|",
    ])


# ── leitura das respostas ─────────────────────────────────────

def segmentos(frame: bytes) -> List[List[str]]:
    texto = frame.strip(SB + EB + CR).decode("latin-1", errors="replace")
    return [s.split("|") for s in texto.replace("\n", "\r").split("\r") if s]


def campo(segs: List[List[str]], nome: str, idx: int) -> str:
    for s in segs:
        if s[0] == nome:
            return s[idx] if len(s) > idx else ""
    return ""


def esperar_frame(ser, timeout: float) -> Optional[bytes]:
    """Lê da serial até um bloco <SB> ... <EB><CR> completo ou o timeout."""
    buf = b""
    limite = time.time() + timeout
    while time.time() < limite:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            ini = buf.find(SB)
            fim = buf.find(EB + CR, ini + 1) if ini >= 0 else -1
            if fim >= 0:
                return buf[ini:fim + 2]
        else:
            time.sleep(0.05)
    return None


def mostrar(frame: bytes, prefixo: str = "  ") -> None:
    for s in segmentos(frame):
        print(prefixo + "|".join(s))


def tipo_de(frame: bytes) -> str:
    return campo(segmentos(frame), "MSH", 8)


# ── fluxos ───────────────────────────────────────────────────

def enviar_resultado(ser, frame: bytes, timeout: float) -> bool:
    ser.write(frame)
    ser.flush()
    print(f"  Enviando... OK ({len(frame)} bytes)")
    print(f"  Aguardando ACK^R01 por ate {timeout:.0f}s...", end=" ", flush=True)
    resp = esperar_frame(ser, timeout)
    if resp is None:
        print("NAO RECEBIDO")
        return False
    print("RECEBIDO")
    mostrar(resp)
    segs = segmentos(resp)
    ok = tipo_de(resp).startswith("ACK^R01") and campo(segs, "MSA", 1) == "AA"
    enviado_cid = campo(segmentos(frame), "MSH", 9)
    if ok and campo(segs, "MSA", 2) != enviado_cid:
        print(f"  ! MSA.2 ({campo(segs, 'MSA', 2)}) difere do MSH.10 enviado ({enviado_cid})")
        ok = False
    return ok


def fluxo_consulta(ser, frame: bytes, timeout: float) -> bool:
    ser.write(frame)
    ser.flush()
    print(f"  Enviando QRY^Q02... OK ({len(frame)} bytes)")
    print(f"  Aguardando QCK^Q02 por ate {timeout:.0f}s...", end=" ", flush=True)
    qck = esperar_frame(ser, timeout)
    if qck is None:
        print("NAO RECEBIDO")
        return False
    print("RECEBIDO")
    mostrar(qck)
    if not tipo_de(qck).startswith("QCK^Q02"):
        print(f"  ! Esperado QCK^Q02, veio {tipo_de(qck)}")
        return False
    status = campo(segmentos(qck), "QAK", 2)
    if status != "OK":
        print(f"  QAK.2 = {status}: o LIS nao tem dados desta amostra (sem DSR^Q03).")
        return True

    print("  Aguardando DSR^Q03 (o manual recomenda ~1s apos o QCK)...", end=" ", flush=True)
    dsr = esperar_frame(ser, timeout)
    if dsr is None:
        print("NAO RECEBIDO")
        return False
    print("RECEBIDO")
    mostrar(dsr)
    cid = campo(segmentos(dsr), "MSH", 9)
    ser.write(msg_ack_q03(cid))
    ser.flush()
    print(f"  ACK^Q03 enviado (MSA.2={cid}).")
    return True


# ── CLI ─────────────────────────────────────────────────────

def listar_portas() -> None:
    from serial.tools import list_ports
    portas = list(list_ports.comports())
    if not portas:
        print("Nenhuma porta serial encontrada.")
    for p in portas:
        print(f"{p.device}\t{p.description}")


def _executar(args: argparse.Namespace, config: Dict[str, Any], situacao: str) -> int:
    if args.list_ports:
        listar_portas()
        return 0

    porta = args.port or str(config["port"])
    baud = args.baud or int(config["baud_rate"])
    timeout = args.timeout if args.timeout is not None else float(config["timeout"])
    etiqueta_padrao = str(config.get("etiqueta") or ETIQUETA_PADRAO)
    itens = itens_da_config(config)

    print(f"Configuracao: {situacao}")

    modo = args.modo or (str(config.get("modo") or "").strip().lower() or None)
    if modo not in (None, "paciente", "qc", "consulta"):
        print(f"  ! modo '{modo}' invalido no JSON (use paciente, qc ou consulta)")
        modo = None
    if modo is None:
        print("Escolha a mensagem:")
        print("  1 - Resultado de paciente (ORU^R01, MSH.16=0)")
        print("  2 - Resultado de CQ       (ORU^R01, MSH.16=2)")
        print("  3 - Consulta de amostra   (QRY^Q02)")
        modo = {"2": "qc", "3": "consulta"}.get(input("Opcao [1]: ").strip(), "paciente")

    etiqueta = args.etiqueta
    if etiqueta is None and modo != "qc":
        etiqueta = input(f"Numero da etiqueta [{etiqueta_padrao}]: ").strip() or etiqueta_padrao
    etiqueta = etiqueta or etiqueta_padrao

    if modo == "paciente":
        frame = msg_paciente(etiqueta, str(config.get("rack", "")), str(config.get("tubo", "")), itens)
    elif modo == "qc":
        frame = msg_qc()
    else:
        frame = msg_consulta(etiqueta)

    print("=" * 62)
    print(f"  ENVIO HL7 -> CA SERIES ({modo})")
    print("=" * 62)
    if modo != "qc":
        print(f"  Etiqueta : {etiqueta}")
    print(f"  Porta    : {porta} @ {baud}")
    if modo == "paciente":
        print(f"  Mensagem : {len(itens)} resultado(s)")
    print("-" * 62)
    mostrar(frame)
    print("-" * 62)

    if args.dry_run:
        print("DRY-RUN: nada foi enviado.")
        return 0

    import serial
    try:
        ser = serial.Serial(porta, baud, timeout=1)
    except serial.SerialException as e:
        print(f"Erro ao abrir {porta}: {e}")
        print("Portas disponiveis:")
        listar_portas()
        print("Confira o \"port\" no JSON: deve ser a outra ponta do par com0com,")
        print("e nao a porta em que o ca_series.exe esta escutando.")
        return 2

    try:
        ser.reset_input_buffer()
        ok = fluxo_consulta(ser, frame, timeout) if modo == "consulta" \
            else enviar_resultado(ser, frame, timeout)
    finally:
        ser.close()

    print("=" * 62)
    print("FLUXO CONCLUIDO." if ok else "FLUXO COM FALHA: veja as mensagens acima.")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Simulador HL7 do analisador URIT CA Series")
    ap.add_argument("--modo", choices=["paciente", "qc", "consulta"])
    ap.add_argument("--etiqueta", help="codigo de barras (OBR.2 / QRD.8)")
    ap.add_argument("--port", help="porta COM (padrao: JSON ou COM8)")
    ap.add_argument("--baud", type=int, help="velocidade (padrao: JSON ou 9600)")
    ap.add_argument("--timeout", type=float, help="espera pela resposta, em s (padrao: JSON ou 10)")
    ap.add_argument("--config", help=f"caminho do JSON [{NOME_CONFIG} ao lado do programa]")
    ap.add_argument("--dry-run", action="store_true", help="so mostra a mensagem, sem abrir a serial")
    ap.add_argument("--list-ports", action="store_true", help="lista as portas e sai")
    ap.add_argument("--sem-pausa", action="store_true", help="nao espera Enter antes de fechar")
    args = ap.parse_args(argv)

    config, situacao = carregar_config(args.config)
    pausar = getattr(sys, "frozen", False) and bool(config.get("pausar_ao_final", True)) \
        and not args.sem_pausa

    try:
        codigo = _executar(args, config, situacao)
    except KeyboardInterrupt:
        print("\nInterrompido.")
        codigo = 130
    except Exception:
        print("ERRO INESPERADO:")
        traceback.print_exc()
        codigo = 3

    if pausar:
        try:
            input("\nPressione Enter para fechar...")
        except (EOFError, KeyboardInterrupt):
            pass
    return codigo


if __name__ == "__main__":
    sys.exit(main())
