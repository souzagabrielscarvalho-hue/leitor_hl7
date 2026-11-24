"""
Versão dos leitores HL7 (uma só para todas as máquinas).

Siga MAJOR.MINOR.PATCH e anote cada versão no CHANGELOG.md:
- MAJOR: muda o que precisa ser feito na instalação (config, rota, protocolo);
- MINOR: função nova que não exige mudar a instalação;
- PATCH: correção.

A versão aparece na abertura do log (analisador_<maquina>.log) e no GET /health.
"""

VERSAO = "7.0.0"
