"""
Leitura, validação e gravação do config_<maquina>.json pelo painel.

O arquivo é o mesmo que o shared/config_loader.py lê ao abrir a máquina, ao
lado do .exe. Ao gravar, as chaves que o painel não conhece (ex.: *_teste_local)
são mantidas.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from shared import maquinas
from shared.config_loader import CHAVES_OPCIONAIS, _get_app_dir

CHAVES_BASE = ("com_port", "baud_rate", "franchise_credential_id", "webhook_url")

BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]

UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Ordem, rótulo e tipo de cada chave na tela. "grupo" separa a conexão com a
# máquina do envio ao lab-apoio; "quando" esconde o campo conforme o modo.
CAMPOS: Dict[str, Dict[str, Any]] = {
    "modo": {
        "grupo": "maquina", "rotulo": "Tipo de conexão", "tipo": "opcoes",
        "opcoes": [["tcp", "Rede (TCP)"], ["serial", "Serial (COM)"]],
    },
    "com_port": {
        "grupo": "maquina", "rotulo": "Porta serial", "tipo": "porta", "quando": "serial",
        "ajuda": "Onde o cabo da máquina está ligado neste computador.",
    },
    "baud_rate": {
        "grupo": "maquina", "rotulo": "Velocidade (baud)", "tipo": "opcoes", "quando": "serial",
        "opcoes": [[b, str(b)] for b in BAUD_RATES],
        "ajuda": "Tem que ser igual à configurada na máquina.",
    },
    "tcp_host": {
        "grupo": "maquina", "rotulo": "Endereço de escuta", "tipo": "texto", "quando": "tcp",
        "ajuda": "0.0.0.0 aceita a máquina por qualquer placa de rede.",
    },
    "tcp_port": {
        "grupo": "maquina", "rotulo": "Porta TCP", "tipo": "numero", "quando": "tcp",
        "ajuda": "A mesma porta configurada na tela LIS da máquina.",
    },
    "pkl_machine_id": {
        "grupo": "maquina", "rotulo": "ID da máquina PKL", "tipo": "texto",
    },
    "franchise_credential_id": {
        "grupo": "apoio", "rotulo": "Credencial da unidade", "tipo": "texto",
        "ajuda": "franchise_credential_id (UUID) da unidade no lab-apoio.",
    },
    "webhook_url": {
        "grupo": "apoio", "rotulo": "Envio dos resultados", "tipo": "url",
        "ajuda": "Rota própria da máquina no lab-apoio. Só o endereço pode mudar (ex.: teste local).",
    },
    "consult_url": {
        "grupo": "apoio", "rotulo": "Consulta por etiqueta", "tipo": "url",
    },
    "exam_images_url": {
        "grupo": "apoio", "rotulo": "Envio de imagens", "tipo": "url",
        "ajuda": "Vazio: usa o mesmo endereço do envio dos resultados.",
    },
    "exam_images_machine_token": {
        "grupo": "apoio", "rotulo": "Token das imagens", "tipo": "segredo",
        "ajuda": "Vazio desliga o envio das imagens.",
    },
}

# Chaves que não podem vir preenchidas de fábrica: identificam a unidade.
CHAVES_DA_UNIDADE = ("franchise_credential_id", "exam_images_machine_token", "pkl_machine_id")


def app_dir() -> str:
    """Pasta do config, a mesma do config_loader (ao lado do .exe)."""
    return _get_app_dir("")


def caminho_config(maquina_id: str, pasta: Optional[str] = None) -> str:
    return os.path.join(pasta or app_dir(), f"config_{maquina_id}.json")


def chaves(maquina_id: str) -> List[str]:
    """Chaves que o config_loader grava para a máquina, na ordem da tela."""
    defaults = maquinas.config_defaults(maquina_id)
    presentes = set(CHAVES_BASE) | {c for c in CHAVES_OPCIONAIS if c in defaults}
    return [c for c in CAMPOS if c in presentes]


def _ler_arquivo(caminho: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not os.path.exists(caminho):
        return None, None
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        if not isinstance(dados, dict):
            return None, "o arquivo não é um objeto JSON"
        return dados, None
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)


def ler(maquina_id: str, pasta: Optional[str] = None) -> Dict[str, Any]:
    """
    Valores para a tela: os do arquivo, completados pelos defaults. Sem arquivo,
    as chaves da unidade ficam vazias, para não sugerir a credencial de outra.
    """
    defaults = maquinas.config_defaults(maquina_id)
    caminho = caminho_config(maquina_id, pasta)
    arquivo, erro = _ler_arquivo(caminho)

    valores: Dict[str, Any] = {}
    for chave in chaves(maquina_id):
        if arquivo is not None and chave in arquivo:
            valores[chave] = arquivo[chave]
        elif chave in CHAVES_DA_UNIDADE:
            valores[chave] = ""
        else:
            valores[chave] = defaults.get(chave, "")

    _, erros = validar(maquina_id, valores)
    return {
        "existe": arquivo is not None,
        "caminho": caminho,
        "erro_leitura": erro,
        "valores": valores,
        "erros": erros,
        "campos": [dict(CAMPOS[c], chave=c) for c in chaves(maquina_id)],
    }


def _validar_url(valor: str, rota: Optional[str], obrigatoria: bool) -> Optional[str]:
    if not valor:
        return "Obrigatório." if obrigatoria else None
    partes = urlparse(valor)
    if partes.scheme not in ("http", "https") or not partes.netloc:
        return "Use um endereço completo, com http:// ou https://."
    if rota is not None and partes.path != rota:
        return f"A rota desta máquina é {rota}. Só o endereço (host) pode mudar."
    return None


def validar(maquina_id: str, valores: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Devolve (valores limpos, erros por chave). Chave desconhecida é erro."""
    defaults = maquinas.config_defaults(maquina_id)
    conhecidas = chaves(maquina_id)
    limpos: Dict[str, Any] = {}
    erros: Dict[str, str] = {}

    for chave in valores:
        if chave not in conhecidas:
            erros[chave] = "Chave que esta máquina não usa."

    def texto(chave: str) -> str:
        v = valores.get(chave, "")
        return "" if v is None else str(v).strip()

    modo = texto("modo") if "modo" in conhecidas else "serial"
    if "modo" in conhecidas:
        if modo not in ("tcp", "serial"):
            erros["modo"] = "Escolha rede (TCP) ou serial."
        limpos["modo"] = modo

    limpos["com_port"] = texto("com_port")
    if modo == "serial" and not limpos["com_port"]:
        erros["com_port"] = "Escolha a porta serial."

    try:
        limpos["baud_rate"] = int(valores.get("baud_rate"))
        if limpos["baud_rate"] <= 0:
            raise ValueError
    except (TypeError, ValueError):
        erros["baud_rate"] = "Velocidade inválida."
        limpos["baud_rate"] = valores.get("baud_rate")

    if "tcp_host" in conhecidas:
        limpos["tcp_host"] = texto("tcp_host")
        if modo == "tcp" and not limpos["tcp_host"]:
            erros["tcp_host"] = "Obrigatório (0.0.0.0 para qualquer placa de rede)."
    if "tcp_port" in conhecidas:
        try:
            limpos["tcp_port"] = int(valores.get("tcp_port"))
            if not 1 <= limpos["tcp_port"] <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            erros["tcp_port"] = "Porta entre 1 e 65535."
            limpos["tcp_port"] = valores.get("tcp_port")

    limpos["franchise_credential_id"] = texto("franchise_credential_id")
    if not UUID.match(limpos["franchise_credential_id"]):
        erros["franchise_credential_id"] = "Informe o UUID da unidade (formato 8-4-4-4-12)."

    for chave, obrigatoria in (("webhook_url", True), ("consult_url", True), ("exam_images_url", False)):
        if chave not in conhecidas:
            continue
        limpos[chave] = texto(chave)
        padrao = defaults.get(chave) or ""
        rota = urlparse(padrao).path if padrao else None
        erro = _validar_url(limpos[chave], rota, obrigatoria)
        if erro:
            erros[chave] = erro

    for chave in ("exam_images_machine_token", "pkl_machine_id"):
        if chave in conhecidas:
            limpos[chave] = texto(chave)

    return limpos, erros


def pronta(maquina_id: str, pasta: Optional[str] = None) -> Tuple[bool, str]:
    """A máquina pode ser iniciada? Exige o config gravado e sem erros."""
    dados = ler(maquina_id, pasta)
    if not dados["existe"]:
        return False, "Configure e salve a máquina antes de iniciar."
    if dados["erro_leitura"]:
        return False, f"O config não pôde ser lido: {dados['erro_leitura']}"
    if dados["erros"]:
        lista = ", ".join(sorted(dados["erros"]))
        return False, f"Corrija o config antes de iniciar ({lista})."
    return True, ""


def salvar(maquina_id: str, valores: Dict[str, Any], pasta: Optional[str] = None) -> Dict[str, Any]:
    """Valida e grava. Devolve {"ok", "erros", "caminho"}; registra no log."""
    titulo = maquinas.obter(maquina_id).titulo
    caminho = caminho_config(maquina_id, pasta)
    limpos, erros = validar(maquina_id, valores)
    if erros:
        logging.warning(f"✗ Config da {titulo} não salvo: {', '.join(sorted(erros))} com erro.")
        return {"ok": False, "erros": erros, "caminho": caminho}

    arquivo, erro = _ler_arquivo(caminho)
    if erro:
        # Arquivo corrompido: não sobrescreve sem que alguém veja o motivo.
        logging.error(f"✗ Config da {titulo} não salvo: {caminho} ilegível ({erro}).")
        return {"ok": False, "erros": {"_arquivo": f"{caminho} ilegível: {erro}"}, "caminho": caminho}

    dados = dict(arquivo or {})
    dados.update(limpos)
    temporario = caminho + ".tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        os.replace(temporario, caminho)
    except OSError as e:
        logging.error(f"✗ Config da {titulo} não salvo em {caminho}: {e}")
        return {"ok": False, "erros": {"_arquivo": str(e)}, "caminho": caminho}

    logging.info(f"✓ Config da {titulo} salvo em {caminho}.")
    return {"ok": True, "erros": {}, "caminho": caminho}
