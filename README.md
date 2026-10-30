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
├── bh5100/                    # Código fonte — Analisador BH5100 (hemograma)
│   ├── bh5100.py              # Código principal
│   ├── bh5100.spec            # Config de build PyInstaller
│   ├── bmptopng.py            # Conversão BMP → PNG
│   ├── makeimage.py           # Geração de imagens
│   ├── makerpdf.py            # Geração de PDFs
│   └── COM_simulator.py       # Simulador HL7 para testes
├── Coagmaster/                # Código fonte — Analisador Coagmaster (coagulação)
│   ├── coagmaster.py          # Código principal
│   ├── coagmaster.spec        # Config de build PyInstaller
│   ├── COM_simulator.py       # Simulador de dados para testes
│   └── test_parser.py         # Testes de parsing
├── mek7300/                   # Código fonte — Analisador MEK7300 (hemograma)
│   ├── mek7300.py             # Código principal
│   └── mek7300.spec           # Config de build PyInstaller
├── vidas1600/                 # Código fonte — Analisador VIDAS 1600 (imunoensaio)
│   └── vidas1600.py           # Código principal
├── shared/                    # Módulos compartilhados entre analisadores
│   ├── __init__.py
│   ├── config_loader.py      # Carregamento de config JSON externa
│   └── file_cleanup.py        # Limpeza automática de arquivos
├── build/                     # Executáveis prontos e seus JSONs de configuração
│   ├── bh5100/
│   │   ├── bh5100.exe
│   │   ├── config_bh5100.json
│   │   ├── envio_com.exe
│   │   └── config_envio_com.json
│   ├── COAGMASTER/
│   │   ├── coagmaster.exe
│   │   └── config_coagmaster.json
│   └── mek7300/
│       ├── mek7300.exe
│       └── config_mek7300.json
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

## Envio de imagens do BH5100 ao S3

O BH5100 extrai os histogramas/scattergrams do HL7 e, depois que o hemograma é aceito pelo webhook, envia as imagens direto ao S3 pelo fluxo do lab-apoio (`initiate` → `PUT` → `confirm`). O `.exe` não recebe credencial da AWS.

No `config_bh5100.json`, ao lado do `.exe`:

- `exam_images_url`: base das rotas; se ficar vazia, é derivada do `webhook_url` (`.../api/integration/images`)
- `exam_images_machine_token`: o token do grupo **tipo + unidade**; vazio desliga o envio.

**Modelo adotado: um token por tipo + unidade.** Todos os PCs que rodam o mesmo tipo (ex.: `bh5100`) na mesma unidade usam o **mesmo token**. Não é um token por PC. O token identifica o tipo e a unidade no lab-apoio; a unidade do `config_bh5100.json` precisa ser a mesma vinculada ao token.

```bash
# criar o token do grupo (uma vez por tipo + unidade; aparece uma única vez)
php artisan exam-images:machine --name="BH5100 - Unidade 88cf" \
    --machine=bh5100 \
    --franchise=<uuid-da-unidade>

# listar / rotacionar / desativar o token do grupo
php artisan exam-images:machine --list
php artisan exam-images:machine --rotate=<id>
php artisan exam-images:machine --disable=<id>
```

O comando recusa criar outro token ativo para o mesmo tipo + unidade. Se precisar de um token por PC (isolamento/revogação individual), use `--force`; nesse caso cada PC tem o seu token.

Para rotacionar um token de grupo, atualize o `config_bh5100.json` de **todos** os PCs que usam aquele token. Se um PC for perdido, rotacione o token do grupo e atualize os demais; para revogar só um PC, use o modelo por PC (`--force`).

O `EXAM_IMAGES_MACHINE_TOKEN` global do PDF continua aceito apenas enquanto `EXAM_IMAGES_ALLOW_LEGACY_TOKEN=true`; depois de migrar todos os PCs, defina `EXAM_IMAGES_ALLOW_LEGACY_TOKEN=false` e deixe o token global vazio.

Para um tipo novo (`vidas1600`, `mek7300`, ...), acrescente o tipo em `config/exam_images.php` → `machines` e crie o token do grupo com `--machine=vidas1600` (ou `mek7300`). Hoje só o BH5100 tem o código de envio de imagens.

Se o token estiver vazio, o hemograma continua sendo enviado normalmente e as imagens são ignoradas. A conversão BMP → PNG usa Pillow (`pip install pillow`).

Se o `exam_images_url` apontar para o lab-apoio local (ex.: `http://192.168.176.17:8039/api/integration/images`), ajuste também `EXAM_IMAGES_PUBLIC_ENDPOINT=http://192.168.176.17:9005` no `.env` do lab-apoio e reinicie o container. Sem isso, a URL pré-assinada do MinIO sai como `127.0.0.1:9005` e o PC Windows não consegue fazer o PUT da imagem.

## Envio de HL7 de teste (`envio_com`)

Para testar o fluxo completo sem o analisador físico, use o `envio_com.py` (fonte em `../read/envio_com.py`) ou o `envio_com.exe` (em `build/bh5100/`). Ele simula o BH5100: envia uma mensagem HL7 real do repositório para a COM em que o `bh5100.exe` está escutando e espera o ACK.

### Par virtual (com0com)

O `bh5100.exe` e o `envio_com.exe` **não podem abrir a mesma porta COM**. Use um par virtual, por exemplo `COM7 <-> COM8`:

1. Crie o par no com0com.
2. No `config_bh5100.json`, deixe `com_port` na ponta do `bh5100.exe` (ex.: `COM7`).
3. No `config_envio_com.json`, deixe a outra ponta (ex.: `COM8`).
4. Abra o `bh5100.exe` e, em seguida, o `envio_com.exe`.
5. Digite o número da etiqueta. O valor padrão é `151020262929` (HEMO, `WRITTEN`, unidade `88cf9273-5044-47f4-b8f6-01160345a190`); pressionar Enter usa esse valor.

O `envio_com` localiza o `20261014_141145_480857.hl7` no repositório (ou usa a cópia embutida no `.exe`), troca o **OBR-3** pela etiqueta informada, atualiza **MSH-7/MSH-10**, envia em MLLP (`VT ... FS CR`) e mostra o ACK devolvido pelo `bh5100.exe` (`MSA|AA|...`).

```text
Numero da etiqueta [151020262929]:
==============================================================
  ENVIO HL7 -> BH5100
==============================================================
  Etiqueta : 151020262929
  Porta    : COM8 @ 9600
  Unidade  : 88cf9273-5044-47f4-b8f6-01160345a190
  Mensagem : 31 resultado(s), 7 imagem(ns)
--------------------------------------------------------------
  Enviando... OK (37987 bytes)
  Aguardando ACK por ate 10s... RECEBIDO
  MSA|AA|20261030091414343224
==============================================================
FLUXO CONCLUIDO: mensagem enviada e ACK recebido.
```

> O `MSA|AA` é o ACK do BH5100 (a mensagem chegou na serial). A aceitação no lab-apoio é outro passo: se a etiqueta não existir ou o exame estiver `RELEASED`, o webhook responde 400 e o arquivo vai para `requisições não enviadas`. Confira o resultado no log `Desktop/AnalisadorBH5100/analisador_bh5100.log`.

Opções úteis:

```powershell
# testar sem abrir a serial
envio_com.exe --dry-run --etiqueta 151020262929

# sobrescrever a porta/velocidade
envio_com.exe --etiqueta 151020262929 --port COM5 --baud 9600

# listar as portas e sair
envio_com.exe --list-ports
```

> A mensagem de referência já contém 31 resultados e 4 imagens úteis (WBC, RBC, PLT e WBC90). Se a etiqueta já tiver imagens de um teste anterior, um novo envio cria outro conjunto de `procedure_images` para os mesmos códigos; use uma etiqueta ainda sem imagens no primeiro teste.

## Queda da conexão COM

O envio de resultados e imagens roda em uma thread separada da leitura serial. Se a COM cair, o programa **continua vivo** e termina de enviar o que estiver em `gerados/`.

O processo só encerra quando as duas condições acontecem juntas:

1. a porta COM está sem conexão há 5 minutos;
2. não há nenhum `.hl7` pendente em `gerados/`.

Nesse caso, aparece um popup no Windows avisando que o interfaceamento parou e o programa é encerrado. Se houver exames pendentes, ele continua tentando enviar mesmo com a serial desconectada.

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

# Envio COM (ferramenta de teste, fonte em ../read)
cd ../../read
pyinstaller envio_com.spec --noconfirm
```

O executável e seu JSON de configuração são gerados em `build/<analisador>/` na raiz do projeto. O `envio_com.exe` é copiado para `build/bh5100/` (junto do `bh5100.exe` e dos `config_*.json`).

> ⚠️ **Antes de gerar o executável**, verifique se a variável `COM_PORT` no arquivo `.py` corresponde à porta onde o cabo do equipamento está conectado. Consulte o Gerenciador de Dispositivos do Windows para confirmar. Após a build, você pode editar o arquivo `config_*.json` ao lado do `.exe` para alterar a porta COM sem precisar recompilar.

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
- `pillow` (conversão das imagens BMP do BH5100 para PNG)
- `pyinstaller` (apenas para build)