# Leitor HL7 - Integração de Analisadores Laboratoriais

Sistema de integração para leitura de dados de analisadores laboratoriais via porta serial, parsing de mensagens HL7/logs proprietários e envio para webhook.

## Analisadores Suportados

| Analisador | Pasta | Protocolo | Porta Padrão |
|---|---|---|---|
| **BH5100** (Mindray) | `bh5100/` | HL7 via serial | COM3 |
| **Coagmaster** (Teco) | `Coagmaster/` | Log proprietário via serial | COM4 |
| **MEK7300** (Nihon Kohden) | `mek7300/` | Texto proprietário via serial | COM4 |
| **VIDAS 1600** (bioMérieux) | `vidas1600/` | HL7 via serial | COM5 |

> ⚠️ **Importante:** A porta COM de cada analisador **depende de onde o cabo serial/USB foi conectado fisicamente ao PC**. As portas listadas acima são os valores padrão definidos no código-fonte. Para alterar a porta COM de um analisador, edite a variável `COM_PORT` no arquivo `.py` correspondente **antes** de gerar o executável com PyInstaller. No Windows, você pode verificar quais portas COM estão em uso pelo Gerenciador de Dispositivos (Painel de Controle → Gerenciador de Dispositivos → Portas (COM e LPT)).

## Estrutura do Projeto

```
leitor_hl7/
├── bh5100/                    # Analisador BH5100 (hemograma)
│   ├── bh5100.py              # Código principal
│   ├── bh5100.spec            # Config de build PyInstaller
│   └── dist/                  # Executável gerado
├── Coagmaster/                # Analisador Coagmaster (coagulação)
│   ├── coagmaster.py          # Código principal
│   ├── coagmaster.spec        # Config de build PyInstaller
│   └── dist/                  # Executável gerado
├── mek7300/                   # Analisador MEK7300 (hemograma)
│   ├── mek7300.py             # Código principal
│   ├── mek7300.spec           # Config de build PyInstaller
│   └── dist/                  # Executável gerado
├── vidas1600/                 # Analisador VIDAS 1600 (imunoensaio)
│   ├── vidas1600.py           # Código principal
│   └── dist/                  # Executável gerado
├── shared/                    # Módulos compartilhados
│   └── file_cleanup.py        # Limpeza automática de arquivos
├── venv/                      # Ambiente virtual Python
├── .gitignore
└── README.md
```

## Funcionamento

1. O serviço lê dados da porta serial do equipamento
2. Faz o parsing da mensagem (HL7 para BH5100 e VIDAS 1600, formato proprietário para Coagmaster e MEK7300)
3. Extrai resultados de exames e imagens (quando disponíveis)
4. Salva arquivos na pasta `gerados/` na Área de Trabalho
5. Uma thread em background envia os dados para o webhook
6. Arquivos enviados com sucesso vão para `enviados/`; falhas vão para `requisições não enviadas/`

## Sistema de Limpeza de Arquivos (`shared/file_cleanup.py`)

Thread daemon que roda em background com as seguintes políticas:

| Funcionalidade | Configuração Padrão |
|---|---|
| Rotação de logs | Quando o log atinge **5 MB**, é renomeado com timestamp |
| Retenção de rotações | Mantém as **5** rotações mais recentes |
| Limpeza por idade | Remove arquivos com mais de **30 dias** em `enviados/` e `requisições não enviadas/` |
| Intervalo de verificação | A cada **1 hora** |
| Diretórios vazios | Removidos automaticamente após limpeza |

## Build (PyInstaller)

```powershell
# BH5100
cd bh5100
pyinstaller bh5100.spec --noconfirm

# Coagmaster
cd Coagmaster
pyinstaller coagmaster.spec --noconfirm

# MEK7300
cd mek7300
pyinstaller mek7300.spec --noconfirm

# VIDAS 1600
cd vidas1600
pyinstaller vidas1600.spec --noconfirm
```

O executável é gerado em `dist/` dentro da pasta do respectivo analisador.

> ⚠️ **Antes de gerar o executável**, verifique se a variável `COM_PORT` no arquivo `.py` corresponde à porta onde o cabo do equipamento está conectado. Consulte o Gerenciador de Dispositivos do Windows para confirmar.

## Pastas de Trabalho

Os analisadores criam as seguintes pastas na Área de Trabalho do usuário:

- `Área de Trabalho/AnalisadorBH5100/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorCoagmaster/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorMEK7300/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorVIDAS1600/` — gerados, enviados, log

## Requisitos

- Python 3.13+
- `pyserial`
- `requests`
- `pyinstaller` (apenas para build)