"""
Pasta raiz do leitor universal: uma pasta só para tudo o que o programa grava.

Com a raiz definida (Configurações > Pasta raiz do programa), ficam nela:
- os config_<maquina>.json (das máquinas que não têm pasta própria) e o
  leitor_painel.json;
- as pastas de trabalho Analisador<MAQUINA> (gerados/, enviados/, logs/...);
- o log do painel, em <raiz>/Painel.

Sem raiz, nada muda: configs ao lado do leitor.exe e dados na Área de
Trabalho. A escolha fica em leitor_raiz.json, ao lado do leitor.exe (o único
arquivo que não pode ir para a raiz). O painel repassa a raiz às máquinas pelas
variáveis de ambiente do config_loader.
"""

import json
import logging
import os
import sys
from typing import Optional

from shared.config_loader import VARIAVEL_PASTA_CONFIG, VARIAVEL_PASTA_DADOS

ARQUIVO = "leitor_raiz.json"


def pasta_programa() -> str:
    """Pasta do leitor.exe (ou de universal/leitor.py em desenvolvimento)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def existe(programa: Optional[str] = None) -> bool:
    """False no primeiro uso: o painel abre o assistente (senha e pasta raiz)."""
    return os.path.exists(os.path.join(programa or pasta_programa(), ARQUIVO))


def ler(programa: Optional[str] = None) -> str:
    """Raiz escolhida, ou "" (padrão)."""
    caminho = os.path.join(programa or pasta_programa(), ARQUIVO)
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        raiz = dados.get("raiz") if isinstance(dados, dict) else ""
        return os.path.abspath(raiz) if isinstance(raiz, str) and raiz.strip() else ""
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:
        logging.error(f"✗ {caminho} ilegível ({e}); usando as pastas padrão.")
        return ""


def gravar(raiz: str, programa: Optional[str] = None) -> bool:
    caminho = os.path.join(programa or pasta_programa(), ARQUIVO)
    temporario = caminho + ".tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump({"raiz": raiz}, f, indent=4, ensure_ascii=False)
        os.replace(temporario, caminho)
        return True
    except OSError as e:
        logging.error(f"✗ Não foi possível gravar {caminho}: {e}")
        return False


def aplicar(raiz: str) -> None:
    """Põe a raiz no ambiente deste processo (e das máquinas que ele abrir)."""
    if raiz:
        os.environ[VARIAVEL_PASTA_CONFIG] = raiz
        os.environ[VARIAVEL_PASTA_DADOS] = raiz
    else:
        os.environ.pop(VARIAVEL_PASTA_CONFIG, None)
        os.environ.pop(VARIAVEL_PASTA_DADOS, None)


def aplicar_se_ausente(raiz: str) -> None:
    """Máquina aberta fora do painel: usa a raiz sem passar por cima do que o painel já mandou."""
    if raiz:
        os.environ.setdefault(VARIAVEL_PASTA_CONFIG, raiz)
        os.environ.setdefault(VARIAVEL_PASTA_DADOS, raiz)


def pasta_painel(raiz: str) -> str:
    """Log do painel e saída dos processos das máquinas."""
    if raiz:
        return os.path.join(raiz, "Painel")
    return os.path.join(os.path.expanduser("~"), "Desktop", "LeitorHL7")
