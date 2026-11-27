"""
Atualização do leitor pelas releases do GitHub (sem nada de interface).

1. verificar(): consulta a última release de REPOSITORIO e compara a tag
   (v7.3.0) com a VERSAO deste programa;
2. baixar(): baixa o leitor.exe da release ao lado do atual, como
   leitor.exe.novo, e confere o SHA-256 publicado pelo GitHub (campo "digest"
   do anexo) ou, na falta dele, o anexo leitor.exe.sha256;
3. instalar(): renomeia o .exe em execução para leitor.exe.anterior (o Windows
   permite renomear, não sobrescrever) e põe o novo no lugar.

Nada acontece sem o usuário pedir. As máquinas abertas continuam rodando na
versão anterior até serem reiniciadas. Cada passo vai para o log do painel.
"""

import hashlib
import logging
import os
import re
import sys
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from shared.versao import VERSAO

# Repositório público de onde as unidades instalam (o mesmo da tela Máquinas do
# lab-apoio). Fixo no código: ninguém aponta a atualização para outro lugar por config.
REPOSITORIO = "souzagabrielscarvalho-hue/leitor_hl7"
URL_ULTIMA = f"https://api.github.com/repos/{REPOSITORIO}/releases/latest"
NOME_ANEXO = "leitor.exe"
TIMEOUT = 15
SUFIXO_NOVO = ".novo"
SUFIXO_ANTERIOR = ".anterior"


def versao_tupla(texto: str) -> Optional[Tuple[int, int, int]]:
    """'v7.10.0' -> (7, 10, 0); None se não for MAJOR.MINOR.PATCH."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (texto or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def mais_nova(candidata: str, atual: str = VERSAO) -> bool:
    a, b = versao_tupla(candidata), versao_tupla(atual)
    return bool(a and b and a > b)


def notas_da_versao(caminho_changelog: str, versao: str = VERSAO) -> str:
    """Trecho do CHANGELOG.md da versão ("## 7.2.0 - data" até o próximo "## ")."""
    try:
        with open(caminho_changelog, "r", encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return ""
    m = re.search(rf"^## {re.escape(versao)}\b.*?$(.*?)(?=^## |\Z)", texto, re.M | re.S)
    return m.group(1).strip() if m else ""


def pode_atualizar_sozinho() -> bool:
    """Só o .exe (PyInstaller) se troca; em desenvolvimento, só verifica."""
    return bool(getattr(sys, "frozen", False))


def verificar(sessao: Any = requests, versao_atual: str = VERSAO) -> Dict[str, Any]:
    """
    Devolve {"disponivel": bool, "versao", "notas", "pagina", "url", "tamanho",
    "sha256", "url_sha256"} ou {"erro": mensagem}.
    """
    try:
        resp = sessao.get(URL_ULTIMA, timeout=TIMEOUT, headers={"Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        logging.error(f"✗ Verificação de atualização: sem acesso ao GitHub ({e}).")
        return {"erro": "Sem acesso ao GitHub. Verifique a internet deste computador."}
    if resp.status_code == 404:
        logging.info("Verificação de atualização: o repositório ainda não tem release publicada.")
        return {"disponivel": False, "versao": versao_atual, "sem_release": True}
    if resp.status_code != 200:
        logging.error(f"✗ Verificação de atualização: GitHub respondeu HTTP {resp.status_code}.")
        return {"erro": f"O GitHub respondeu HTTP {resp.status_code}. Tente mais tarde."}
    try:
        dados = resp.json()
    except ValueError:
        logging.error("✗ Verificação de atualização: resposta do GitHub não é JSON.")
        return {"erro": "Resposta inválida do GitHub."}

    tag = dados.get("tag_name", "")
    if not versao_tupla(tag):
        logging.error(f"✗ Verificação de atualização: tag fora do padrão ({tag!r}).")
        return {"erro": f"A última release tem uma tag fora do padrão: {tag!r}."}

    info: Dict[str, Any] = {
        "disponivel": mais_nova(tag, versao_atual),
        "versao": tag.lstrip("v"),
        "notas": (dados.get("body") or "").strip(),
        "pagina": dados.get("html_url", ""),
        "url": "", "tamanho": 0, "sha256": "", "url_sha256": "",
    }
    for anexo in dados.get("assets") or []:
        nome = anexo.get("name", "")
        if nome == NOME_ANEXO:
            info["url"] = anexo.get("browser_download_url", "")
            info["tamanho"] = int(anexo.get("size") or 0)
            digest = anexo.get("digest") or ""
            if digest.startswith("sha256:"):
                info["sha256"] = digest.split(":", 1)[1].lower()
        elif nome == NOME_ANEXO + ".sha256":
            info["url_sha256"] = anexo.get("browser_download_url", "")

    if info["disponivel"]:
        logging.info(f"✓ Atualização disponível: {info['versao']} (instalada: {versao_atual}).")
        if not info["url"]:
            logging.error(f"✗ A release {info['versao']} não tem o anexo {NOME_ANEXO}.")
            info["erro_anexo"] = f"A release {info['versao']} não tem o arquivo {NOME_ANEXO}."
    else:
        logging.info(f"✓ Verificação de atualização: {versao_atual} é a mais recente (última release: {info['versao']}).")
    return info


def _sha256_esperado(info: Dict[str, Any], sessao: Any) -> str:
    if info.get("sha256"):
        return info["sha256"]
    if info.get("url_sha256"):
        resp = sessao.get(info["url_sha256"], timeout=TIMEOUT)
        resp.raise_for_status()
        # Formato do sha256sum: "<hash>  leitor.exe"
        m = re.search(r"\b([0-9a-fA-F]{64})\b", resp.text)
        if m:
            return m.group(1).lower()
    return ""


def baixar(
    info: Dict[str, Any],
    exe_atual: str,
    progresso: Optional[Callable[[int, int], None]] = None,
    sessao: Any = requests,
) -> Dict[str, Any]:
    """Baixa para <exe>.novo e confere o SHA-256. Devolve {"ok", "arquivo"} ou {"ok": False, "mensagem"}."""
    versao = info.get("versao", "?")
    destino = exe_atual + SUFIXO_NOVO
    if not info.get("url"):
        return {"ok": False, "mensagem": info.get("erro_anexo") or "A release não tem o arquivo para baixar."}
    try:
        esperado = _sha256_esperado(info, sessao)
    except requests.RequestException as e:
        logging.error(f"✗ Atualização {versao}: não foi possível ler o SHA-256 ({e}).")
        return {"ok": False, "mensagem": "Não foi possível ler o SHA-256 da release."}
    if not esperado:
        # Sem como conferir, não instala: um .exe corrompido pararia a unidade.
        logging.error(f"✗ Atualização {versao}: release sem SHA-256 publicado; download cancelado.")
        return {"ok": False, "mensagem": "A release não publica o SHA-256 do arquivo; a atualização foi cancelada."}

    logging.info(f"Atualização {versao}: baixando {info['url']} para {destino}.")
    calc = hashlib.sha256()
    baixado = 0
    try:
        with sessao.get(info["url"], stream=True, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or info.get("tamanho") or 0)
            with open(destino, "wb") as f:
                for bloco in resp.iter_content(chunk_size=256 * 1024):
                    if not bloco:
                        continue
                    f.write(bloco)
                    calc.update(bloco)
                    baixado += len(bloco)
                    if progresso:
                        progresso(baixado, total)
    except (requests.RequestException, OSError) as e:
        _remover(destino)
        logging.error(f"✗ Atualização {versao}: download falhou ({e}).")
        return {"ok": False, "mensagem": f"O download falhou: {e}"}

    if calc.hexdigest() != esperado:
        _remover(destino)
        logging.error(f"✗ Atualização {versao}: SHA-256 não confere ({calc.hexdigest()} ≠ {esperado}); arquivo descartado.")
        return {"ok": False, "mensagem": "O arquivo baixado não confere com o SHA-256 da release e foi descartado."}

    logging.info(f"✓ Atualização {versao}: {baixado} bytes baixados e SHA-256 conferido.")
    return {"ok": True, "arquivo": destino}


def instalar(exe_atual: str, novo: str) -> Dict[str, Any]:
    """Troca o .exe: atual -> .anterior, novo -> atual. Desfaz se a segunda etapa falhar."""
    anterior = exe_atual + SUFIXO_ANTERIOR
    try:
        _remover(anterior)
        os.replace(exe_atual, anterior)
    except OSError as e:
        logging.error(f"✗ Atualização: não foi possível renomear {exe_atual} ({e}).")
        return {"ok": False, "mensagem": f"Não foi possível renomear o programa atual: {e}"}
    try:
        os.replace(novo, exe_atual)
    except OSError as e:
        try:
            os.replace(anterior, exe_atual)
        except OSError:
            pass
        logging.error(f"✗ Atualização: não foi possível pôr o novo arquivo no lugar ({e}); versão anterior mantida.")
        return {"ok": False, "mensagem": f"Não foi possível instalar o novo arquivo: {e}"}
    logging.info(f"✓ Atualização instalada em {exe_atual} (anterior guardado em {anterior}).")
    return {"ok": True, "anterior": anterior}


def _remover(caminho: str) -> None:
    try:
        os.remove(caminho)
    except FileNotFoundError:
        pass
