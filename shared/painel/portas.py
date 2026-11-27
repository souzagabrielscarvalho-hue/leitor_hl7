"""
Portas seriais do computador, com o que o Windows informa de cada uma.

Também lembra o adaptador USB de cada máquina (VID, PID, número de série e
posição USB) em leitor_painel.json, ao lado do .exe. Quando o Windows troca o
número da COM (ex.: o adaptador foi para outra entrada USB), o painel acha o
mesmo adaptador na COM nova e sugere a troca. Nada muda sem alguém confirmar.
"""

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

import serial
from serial.tools import list_ports

ARQUIVO_PAINEL = "leitor_painel.json"


def _ordem(porta: str) -> tuple:
    """COM2 antes de COM10."""
    m = re.match(r"^(.*?)(\d+)$", porta)
    return (m.group(1), int(m.group(2))) if m else (porta, 0)


# Drivers de porta virtual conhecidos (nome do dispositivo no registro ou hwid).
VIRTUAIS = ("com0com", "cncport", "vspe", "eltima", "vsbsp", "vspd", "virtual", "hhd", "ftdibus\\virtual")


def _parece_virtual(*textos: str) -> bool:
    texto = " ".join(t for t in textos if t).lower()
    return any(v in texto for v in VIRTUAIS)


def _descrever(p: Any) -> Dict[str, Any]:
    descricao = p.description if p.description and p.description != "n/a" else ""
    return {
        "porta": p.device,
        "descricao": descricao,
        "fabricante": p.manufacturer or "",
        "vid": p.vid,
        "pid": p.pid,
        "serie": p.serial_number or "",
        "local": p.location or "",
        "usb": p.vid is not None,
        "virtual": _parece_virtual(descricao, p.hwid or "", p.manufacturer or ""),
    }


def _portas_do_registro() -> Dict[str, str]:
    """
    HKLM\\HARDWARE\\DEVICEMAP\\SERIALCOMM: toda COM criada por algum driver,
    inclusive as virtuais que não aparecem como dispositivo "Ports" no SetupAPI.
    Devolve {"COM10": "\\Device\\com0com10"}.
    """
    try:
        import winreg
    except ImportError:
        return {}
    encontradas: Dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as chave:
            i = 0
            while True:
                try:
                    dispositivo, porta, _ = winreg.EnumValue(chave, i)
                except OSError:
                    break
                if isinstance(porta, str) and porta.strip():
                    encontradas[porta.strip()] = dispositivo
                i += 1
    except OSError:
        pass
    return encontradas


def _portas_dos() -> List[str]:
    """Nomes COMn que o Windows conhece (QueryDosDevice), para o que nenhum registro lista."""
    if not os.name == "nt":
        return []
    try:
        import ctypes
        tamanho = 65536
        while tamanho <= 4 * 1024 * 1024:
            buf = ctypes.create_unicode_buffer(tamanho)
            n = ctypes.windll.kernel32.QueryDosDeviceW(None, buf, tamanho)
            if n:
                nomes = buf[:n].split("\x00")
                return [x for x in nomes if re.fullmatch(r"COM\d+", x, re.I)]
            if ctypes.GetLastError() != 122:  # ERROR_INSUFFICIENT_BUFFER
                return []
            tamanho *= 4
    except Exception as e:
        logging.error(f"✗ QueryDosDevice falhou ao listar as portas: {e}")
    return []


def _extra(porta: str, dispositivo: str) -> Dict[str, Any]:
    if dispositivo:
        virtual = _parece_virtual(dispositivo)
        descricao = f"{'Porta virtual' if virtual else 'Porta serial'} ({dispositivo})"
    else:
        virtual = True
        descricao = "Porta sem dispositivo no Gerenciador (provável virtual)"
    return {"porta": porta, "descricao": descricao, "fabricante": "", "vid": None, "pid": None,
            "serie": "", "local": "", "usb": False, "virtual": virtual}


def listar_sistema() -> List[Dict[str, Any]]:
    """
    Portas do pyserial (SetupAPI) completadas pelas do registro SERIALCOMM e do
    QueryDosDevice: assim entram as portas virtuais (com0com, VSPE, Eltima...).
    """
    lista = {p["porta"].upper(): p for p in (_descrever(x) for x in list_ports.comports())}
    for porta, dispositivo in _portas_do_registro().items():
        if porta.upper() in lista:
            if _parece_virtual(dispositivo):
                lista[porta.upper()]["virtual"] = True
        else:
            lista[porta.upper()] = _extra(porta, dispositivo)
    for porta in _portas_dos():
        if porta.upper() not in lista:
            lista[porta.upper()] = _extra(porta, "")
    return sorted(lista.values(), key=lambda p: _ordem(p["porta"]))


def testar_abertura(porta: str) -> Optional[str]:
    """None se a porta abriu (e foi fechada em seguida); senão, o erro."""
    try:
        s = serial.Serial(porta, timeout=0)
        s.close()
        return None
    except (serial.SerialException, OSError, ValueError) as e:
        return str(e)


# ── ADAPTADOR LEMBRADO ─────────────────────────────────────────

def _caminho(pasta: str) -> str:
    return os.path.join(pasta, ARQUIVO_PAINEL)


def ler_estado(pasta: str) -> Dict[str, Any]:
    try:
        with open(_caminho(pasta), "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def gravar_estado(pasta: str, dados: Dict[str, Any]) -> bool:
    """Grava leitor_painel.json (adaptadores e máquinas adicionadas) sem corromper no meio."""
    caminho = _caminho(pasta)
    temporario = caminho + ".tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        os.replace(temporario, caminho)
        return True
    except OSError as e:
        logging.error(f"✗ Não foi possível gravar {caminho}: {e}")
        return False


def lembrar_adaptador(pasta: str, maquina_id: str, porta: str, sistema: List[Dict[str, Any]]) -> None:
    """Grava a identidade do adaptador da porta escolhida (só USB)."""
    atual = next((p for p in sistema if p["porta"] == porta), None)
    if not atual or not atual["usb"]:
        return
    dados = ler_estado(pasta)
    dados.setdefault("adaptadores", {})[maquina_id] = {
        k: atual[k] for k in ("porta", "vid", "pid", "serie", "local", "descricao")
    }
    if gravar_estado(pasta, dados):
        logging.info(f"✓ Adaptador de {maquina_id} lembrado: {atual['descricao'] or porta} em {porta}.")


def _mesmo_adaptador(lembrado: Dict[str, Any], p: Dict[str, Any], sistema: List[Dict[str, Any]]) -> bool:
    if not p["usb"] or (p["vid"], p["pid"]) != (lembrado.get("vid"), lembrado.get("pid")):
        return False
    if lembrado.get("serie"):
        return p["serie"] == lembrado["serie"]
    if lembrado.get("local"):
        return p["local"] == lembrado["local"]
    # Sem série nem posição: só se for o único adaptador desse modelo.
    iguais = [q for q in sistema if (q["vid"], q["pid"]) == (p["vid"], p["pid"])]
    return len(iguais) == 1


def sugerir_porta(
    pasta: str, maquina_id: str, porta_config: str, sistema: List[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """Se a COM do config sumiu e o adaptador lembrado está em outra, sugere a nova."""
    if not porta_config or any(p["porta"] == porta_config for p in sistema):
        return None
    lembrado = ler_estado(pasta).get("adaptadores", {}).get(maquina_id)
    if not lembrado:
        return None
    for p in sistema:
        if _mesmo_adaptador(lembrado, p, sistema):
            return {"porta_antiga": porta_config, "porta_nova": p["porta"], "descricao": p["descricao"]}
    return None


def listar(
    configs: Dict[str, Dict[str, Any]],
    rodando: Callable[[str], bool],
    testar: bool = False,
    sistema: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Portas do sistema, cada uma com as máquinas configuradas nela.

    Args:
        configs: {maquina_id: valores do config} das máquinas.
        rodando: diz se a máquina está aberta (a porta dela não é testada).
        testar: abre e fecha cada porta livre para ver se outro programa a usa.
    """
    sistema = listar_sistema() if sistema is None else sistema
    resultado = []
    for p in sistema:
        item = dict(p)
        item["maquinas"] = [
            mid for mid, v in configs.items()
            if v.get("modo", "serial") == "serial" and v.get("com_port") == p["porta"]
        ]
        abertas = [mid for mid in item["maquinas"] if rodando(mid)]
        item["em_uso_por"] = abertas[0] if abertas else None
        item["situacao"] = "em_uso" if abertas else "nao_testada"
        item["erro"] = ""
        if testar and not abertas:
            erro = testar_abertura(p["porta"])
            item["situacao"] = "livre" if erro is None else "ocupada"
            item["erro"] = erro or ""
        resultado.append(item)
    return resultado
