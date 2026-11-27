"""
Registro das máquinas para o leitor universal (universal/leitor.py).

Cada máquina continua no seu módulo (bh5100/bh5100.py etc.), com MACHINE_ID,
MACHINE_NAME, HEALTH_PORT, CONFIG_DEFAULTS e criar_analisador(). Aqui ficam só
os dados que o painel mostra (nome, protocolo, sentido) e o import do módulo.

Cada máquina é importada como nos testes: "pasta.modulo" a partir da raiz
(bh5100.bh5100, Coagmaster.coagmaster...), e a URIT-8021A pelo nome do arquivo
(urit8021), porque a pasta urit-8021 tem hífen. São os mesmos nomes do
hiddenimports do universal/leitor.spec.
"""

import importlib
import os
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Dict, List, Optional
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class Maquina:
    id: str          # MACHINE_ID e nome do config_<id>.json
    modulo: str      # nome de import do módulo da máquina
    pasta: str       # pasta do módulo na raiz do projeto
    titulo: str      # nome mostrado no painel
    tipo: str        # setor do laboratório
    protocolo: str
    sentido: str     # "recebe" ou "consulta e recebe"


MAQUINAS: List[Maquina] = [
    Maquina("bh5100", "bh5100.bh5100", "bh5100", "BH5100 (Mindray)", "Hemograma",
            "HL7/MLLP serial ou TCP", "recebe"),
    Maquina("ca150", "ca150.ca150", "ca150", "CA-150 (URIT)", "Bioquímica",
            "HL7/MLLP TCP ou serial", "consulta e recebe"),
    Maquina("coagmaster", "Coagmaster.coagmaster", "Coagmaster", "Coagmaster (Teco)", "Coagulação",
            "Texto próprio, serial ou TCP", "recebe"),
    Maquina("mek7300", "mek7300.mek7300", "mek7300", "MEK7300 (Nihon Kohden)", "Hemograma",
            "Texto com ETX, serial ou TCP", "recebe"),
    Maquina("pkl", "pkl.pkl", "pkl", "PKL 125", "Bioquímica",
            "ASTM E1394, serial ou TCP", "consulta e recebe"),
    Maquina("urit8021", "urit8021", "urit-8021", "URIT-8021A (URIT)", "Bioquímica",
            "HL7/MLLP TCP ou serial", "consulta e recebe"),
    Maquina("vidas1600", "vidas1600.vidas1600", "vidas1600", "VIDAS 1600 (bioMérieux)", "Imunoensaio",
            "HL7/MLLP serial ou TCP", "recebe"),
]

POR_ID: Dict[str, Maquina] = {m.id: m for m in MAQUINAS}


def _preparar_path() -> None:
    """Raiz (shared e pastas das máquinas) e a pasta com hífen da URIT-8021A."""
    for pasta in (RAIZ, os.path.join(RAIZ, "urit-8021")):
        if os.path.isdir(pasta) and pasta not in sys.path:
            sys.path.append(pasta)


def obter(maquina_id: str) -> Maquina:
    """Máquina pelo id; KeyError com a lista das válidas se não existir."""
    try:
        return POR_ID[maquina_id]
    except KeyError:
        validas = ", ".join(POR_ID)
        raise KeyError(f"máquina desconhecida: {maquina_id!r} (válidas: {validas})") from None


def modulo(maquina_id: str) -> ModuleType:
    """Importa o módulo da máquina (bh5100.bh5100, urit8021...)."""
    _preparar_path()
    return importlib.import_module(obter(maquina_id).modulo)


def nome_maquina(maquina_id: str) -> str:
    """MACHINE_NAME: dá nome à pasta Desktop\\Analisador<NOME> e ao popup."""
    return modulo(maquina_id).MACHINE_NAME


def health_port(maquina_id: str) -> int:
    return int(modulo(maquina_id).HEALTH_PORT)


def config_defaults(maquina_id: str) -> Dict:
    return dict(modulo(maquina_id).CONFIG_DEFAULTS)


def rota_webhook(maquina_id: str) -> str:
    """Caminho da rota própria da máquina no lab-apoio (ex.: /api/integration/ca-150)."""
    return urlparse(config_defaults(maquina_id).get("webhook_url", "")).path


def pasta_base(maquina_id: str, home: Optional[str] = None) -> str:
    """
    Analisador<NOME> na Área de Trabalho (ou na pasta raiz do painel), a mesma
    pasta do BaseAnalisador._setup_dirs.
    """
    from shared.config_loader import pasta_dados
    base = os.path.join(home, "Desktop") if home else pasta_dados()
    return os.path.join(base, f"Analisador{nome_maquina(maquina_id)}")


def arquivo_log(maquina_id: str) -> str:
    return os.path.join(pasta_base(maquina_id), f"analisador_{maquina_id}.log")


def pasta_gerados(maquina_id: str) -> str:
    return os.path.join(pasta_base(maquina_id), "gerados")
