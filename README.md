# Leitor HL7 - Integração de Analisadores Laboratoriais

Sistema de integração para leitura de dados de analisadores laboratoriais via porta serial, parsing de mensagens HL7/logs proprietários e envio para webhook.

## Analisadores Suportados

| Analisador | Pasta | Protocolo | Porta Padrão |
|---|---|---|---|
| **BH5100** (Mindray) | `bh5100/` | HL7 via serial | COM3 |
| **CA-150** (URIT) | `ca150/` | HL7 via TCP/IP (a máquina não tem serial), com consulta por etiqueta | TCP 5150 |
| **Coagmaster** (Teco) | `Coagmaster/` | Log proprietário via serial | COM4 |
| **MEK7300** (Nihon Kohden) | `mek7300/` | Texto proprietário via serial | COM4 |
| **URIT-8021A** (URIT) | `urit-8021/` | HL7 via TCP/IP (ou serial), com consulta por etiqueta | TCP 8021 |
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
├── ca150/                     # Código fonte — Analisador URIT CA-150 (bioquímica)
│   ├── ca150.py               # Código principal (serial ou TCP, consulta por etiqueta)
│   └── ca150.spec             # Config de build PyInstaller
├── Coagmaster/                # Código fonte — Analisador Coagmaster (coagulação)
│   ├── coagmaster.py          # Código principal
│   ├── coagmaster.spec        # Config de build PyInstaller
│   ├── COM_simulator.py       # Simulador de dados para testes
│   └── test_parser.py         # Testes de parsing
├── urit-8021/                 # Código fonte — Analisador URIT-8021A (bioquímica)
│   ├── urit8021.py            # Código principal (TCP ou serial, consulta por etiqueta)
│   └── urit8021.spec          # Config de build PyInstaller
├── mek7300/                   # Código fonte — Analisador MEK7300 (hemograma)
│   ├── mek7300.py             # Código principal
│   └── mek7300.spec           # Config de build PyInstaller
├── vidas1600/                 # Código fonte — Analisador VIDAS 1600 (imunoensaio)
│   └── vidas1600.py           # Código principal
├── shared/                    # Módulos compartilhados entre analisadores
│   ├── __init__.py
│   ├── base_analisador.py     # Serial, envio ao webhook e queda da COM
│   ├── config_loader.py      # Carregamento de config JSON externa
│   ├── exam_images_uploader.py # Envio das imagens ao S3 (só BH5100)
│   ├── file_cleanup.py        # Limpeza automática de arquivos
│   └── health_server.py       # Health check HTTP
├── tests/                     # Testes (unittest)
├── build_all.ps1              # Compila todos os executáveis
├── enviar.ps1                 # Envia a branch atual para um repositório
├── requirements.txt
├── build/                     # Executáveis prontos e seus JSONs de configuração
│   ├── bh5100/
│   │   ├── bh5100.exe
│   │   ├── config_bh5100.json          # não versionado (tem o token)
│   │   ├── config_bh5100.example.json
│   │   ├── envio_com.exe               # não versionado
│   │   └── config_envio_com.json       # não versionado
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
| Arquivamento diário | Move `enviados/` e `requisições não enviadas/` para `logs/`; `gerados/` fica de fora, para não tirar exames da fila de envio |
| Rotação de logs | Quando o log atinge **5 MB**, é renomeado com timestamp |
| Retenção de rotações | Mantém as **5** rotações mais recentes |
| Limpeza por idade | Remove arquivos com mais de **30 dias** em `enviados/` e `requisições não enviadas/` |
| Intervalo de verificação | A cada **1 hora** |
| Diretórios vazios | Removidos automaticamente após limpeza |

## Envio de imagens do BH5100 ao S3

O BH5100 extrai os histogramas/scattergrams do HL7 e, depois que o hemograma é aceito pelo webhook, envia as imagens direto ao S3 pelo fluxo do lab-apoio (`initiate` → `PUT` → `confirm`). O `.exe` não recebe credencial da AWS.

No `config_bh5100.json`, ao lado do `.exe` (modelo com placeholders em
`build/bh5100/config_bh5100.example.json`):

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

Não existe token global no `.env` do lab-apoio: o token é sempre o do grupo `tipo + unidade` (ou por PC com `--force`), configurado no `config_bh5100.json`.

**Coagmaster, MEK7300, PKL e VIDAS 1600 não enviam imagens e funcionam sem token:** o lab-apoio só exige o token nas rotas `/api/integration/images/*`, e os `config_*.json` dessas máquinas não têm as chaves `exam_images_*`.

Para um tipo novo (`vidas1600`, `mek7300`, ...), acrescente o tipo em `config/exam_images.php` → `machines` e crie o token do grupo com `--machine=vidas1600` (ou `mek7300`). Hoje só o BH5100 tem o código de envio de imagens. A máquina passa a ter as chaves de imagem no config ao declarar `exam_images_url` e `exam_images_machine_token` nos seus `config_defaults`.

Se o token estiver vazio, o hemograma continua sendo enviado normalmente e as imagens são ignoradas. A conversão BMP → PNG usa Pillow (`pip install pillow`).

Se o `exam_images_url` apontar para o lab-apoio local (ex.: `http://192.168.176.17:8039/api/integration/images`), verifique se o endpoint do S3/MinIO é alcançável pelo PC Windows. Em producao AWS, nao defina `EXAM_IMAGES_ENDPOINT` (usa o endpoint padrao da AWS).

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

## URIT CA-150 (interfaceamento por etiqueta)

Segue o manual *CA Series Automatic Chemistry Analyzer HL7 Interface Development Reference Manual* V1.01. A máquina é o cliente e o `ca150.exe` é o servidor:

- **Consulta:** a máquina lê o tubo e manda `QRY^Q02` (etiqueta em `QRD.8`). O programa consulta o lab-apoio em `GET /api/interfacing/tag` e responde `QCK^Q02` e, cerca de 1 s depois, `DSR^Q03` com o paciente e os itens (os `attribute_code` dos atributos de origem INTERFACE). Etiqueta inexistente ou sem item por interface: só `QCK^Q02` com `QAK|SR|NF|`. Lab-apoio fora do ar: `QAK|SR|AE|`.
- **Resultado:** `ORU^R01` recebe `ACK^R01` na hora, vai para `gerados/` e é enviado a `POST /api/integration/ca-150` como `{"tag_id", "results": [{"code", "value", "unit"}]}`. Controle de qualidade (`MSH.16 = 2`) recebe ACK, mas não é enviado. Itens calculados com valor `10000000` são descartados.
- **Códigos:** o código do item configurado na CA-150 precisa ser igual ao `attribute_code` no sistema.

O `config_ca150.json` escolhe a conexão:

```json
{
    "modo": "tcp",             // a CA-150 só fala por rede; "serial" fica para testes
    "com_port": "COM6",
    "baud_rate": 9600,
    "tcp_host": "0.0.0.0",     // no modo tcp: a máquina conecta no IP do PC
    "tcp_port": 5150,          //   e nesta porta, a mesma da tela LIS da URIT (liberar no Firewall)
    "franchise_credential_id": "<franchise_id da unidade>",
    "webhook_url": ".../api/integration/ca-150?franchise_credential_id={franchise_credential_id}",
    "consult_url": ".../api/interfacing/tag?franchise_credential_id={franchise_credential_id}"
}
```

No modo TCP, uma conexão nova da máquina substitui a anterior. Se a máquina desconectar e não voltar em 5 minutos, vale a mesma regra da queda da COM (abaixo): o programa envia `gerados/`, mostra o popup e encerra. Antes da primeira conexão não conta como queda. Health check em `http://localhost:8085/health`.

## URIT-8021A (interfaceamento por etiqueta)

Segue o *Urit HL7 LIS Interface Dev Reference Manual* (LISHL7Interface.dll, o mesmo das URIT 8030/8060/8031). Quem fala com o LIS é o software da URIT, no PC que controla o analisador (a serial do manual de operação liga o instrumento a esse PC). O software é o cliente e o `urit8021.exe` é o servidor:

- **Consulta:** o software manda `QRY^Q02` (etiqueta em `QRD.8`). O programa consulta `GET /api/interfacing/tag` e responde `QCK^Q02` e, cerca de 1 s depois, `DSR^Q03` (DSP 1 a 17 do manual e os itens a partir do DSP 18). Etiqueta inexistente, sem item por interface ou consulta por período (sem etiqueta): só `QCK^Q02` com `QAK|SR|NF|`. Lab-apoio fora do ar: `QAK|SR|AE|`.
- **Resultado:** `ORU^R01` recebe `ACK^R01` na hora, com o Sample ID (`OBR.3`) no `MSA.2`, como manda o manual. A mensagem vai para `gerados/` e é enviada a `POST /api/integration/urit-8021` (endpoint próprio; grava pelo `attribute_code`, com a mesma regra da CA-150) como `{"tag_id", "results": [{"code", "value", "unit"}]}`. Etiqueta em `OBR.2`, código do item em `OBX.4`. O valor vai com vírgula (`11.8` → `11,8`). Resultado sem código de barras (`OBR.2` vazio ou `null`) recebe ACK, mas não é enviado.
- **Imagens:** o manual não prevê imagem nem base64 (os OBX são `NM`). Se chegar um OBX `ED`, ele fica fora dos resultados e o log registra.
- **Códigos:** o nome do item no software da URIT (ALB, GLU...) precisa ser igual ao `attribute_code` no sistema.

O `config_urit8021.json` escolhe a conexão:

```json
{
    "modo": "tcp",             // "tcp" (padrão do manual) ou "serial"
    "com_port": "COM7",        // modo serial
    "baud_rate": 9600,
    "tcp_host": "0.0.0.0",     // modo tcp: o software da URIT conecta no IP deste PC (127.0.0.1 se for o mesmo PC)
    "tcp_port": 8021,          //   e nesta porta, a mesma configurada no LIS da URIT (liberar no Firewall)
    "franchise_credential_id": "<franchise_id da unidade>",
    "webhook_url": ".../api/integration/urit-8021?franchise_credential_id={franchise_credential_id}",
    "consult_url": ".../api/interfacing/tag?franchise_credential_id={franchise_credential_id}"
}
```

No TCP valem as mesmas regras da CA-150: uma conexão nova substitui a anterior, antes da primeira conexão não há queda e, se o software desconectar e não voltar em 5 minutos, o programa envia `gerados/`, mostra o popup e encerra. Health check em `http://localhost:8086/health`.

## Queda da conexão COM

Vale para `bh5100`, `ca150` (serial e TCP), `coagmaster`, `mek7300`, `pkl`, `urit8021` (serial e TCP) e `vidas1600`. O envio de resultados e imagens roda em uma thread separada da leitura serial, então continua com a COM caída.

1. Enquanto a porta está caída, o programa tenta reconectar e segue enviando o que estiver em `gerados/`. Se a porta voltar em até 5 minutos, nada muda para o usuário.
2. Depois de 5 minutos sem conexão, ele encerra assim que `gerados/` esvaziar.
3. Se `gerados/` ficar 15 minutos sem diminuir (por exemplo, servidor fora), ele encerra mesmo assim. O que sobrou continua em `gerados/` e é enviado na próxima abertura.

Antes de encerrar, aparece um popup com a porta, o erro da queda, o erro da última reconexão e a contagem dos exames enviados, não enviados e ainda pendentes. O programa só fecha depois do clique em OK, mas já solta a porta antes do popup, para poder ser reaberto.

Arquivos que falharam vão para `requisições não enviadas/` e não são reenviados automaticamente.

## Build

```powershell
cd c:\repositorio\leitor_hl7
powershell -ExecutionPolicy Bypass -File .\build_all.ps1
```

O comando compila o código que está na branch atual do clone: confira a branch e o commit que ele mostra no início.

- **Antes de compilar:** instala o que faltar de `requirements.txt` no `venv` e confere se cada analisador importa. Falha aqui interrompe o build.
- **Executáveis:** `bh5100`, `ca150`, `coagmaster`, `mek7300`, `pkl`, `test_get_only`, `urit8021` e `vidas1600`, em `build/<analisador>/`. O `test_get_only.exe` vai para `build/pkl/`.
- **`envio_com`:** compilado se existir `..\read\envio_com.spec`, copiado para `build/bh5100/`. Sem essa pasta aparece como `PULADO`, sem falhar o build. Não é versionado.
- **Resultado:** o script mostra uma tabela `OK`/`ERRO`/`PULADO`, grava a saída de cada executável em `build_logs/<nome>.log` e, com qualquer `ERRO`, sai com código 1 sem commitar.
- **Commit:** feito só com todos os executáveis OK, e cancelado se houver token preenchido em arquivo versionado. Use `-SkipGit` para só compilar.

O envio ao GitHub é um comando separado para cada repositório. Ele envia a branch atual, e recusa se houver alteração sem commit ou token preenchido:

```powershell
# Vida-Exames/HL7_integrator
powershell -ExecutionPolicy Bypass -File .\enviar.ps1 empresa

# souzagabrielscarvalho-hue/leitor_hl7 (repositório PÚBLICO)
powershell -ExecutionPolicy Bypass -File .\enviar.ps1 pessoal
```

O `build/bh5100/config_bh5100.json` tem o token da máquina e não é versionado. Em um PC novo, crie-o a partir de `config_bh5100.example.json`.

## Testes no ambiente local

**Sempre teste no lab-apoio local antes de usar produção.** Ele roda no Docker, na porta `8039`, e o PC Windows o alcança por `http://localhost:8039`. No `config_*.json` ao lado do `.exe` de teste, troque o host do `webhook_url` (e, na CA-150 e na URIT-8021A, do `consult_url`) por `http://localhost:8039`, mantendo a rota da máquina:

| Máquina | `webhook_url` local |
|---|---|
| BH5100 | `http://localhost:8039/api/integration/bh5100?franchise_credential_id={franchise_credential_id}` |
| CA-150 | `http://localhost:8039/api/integration/ca-150?franchise_credential_id={franchise_credential_id}` |
| Coagmaster | `http://localhost:8039/api/integration/coagmaster?franchise_credential_id={franchise_credential_id}` |
| MEK7300 | `http://localhost:8039/api/integration/mek7300/v2?franchise_credential_id={franchise_credential_id}` |
| URIT-8021A | `http://localhost:8039/api/integration/urit-8021?franchise_credential_id={franchise_credential_id}` |
| VIDAS 1600 | `http://localhost:8039/api/integration/vidas1600?franchise_credential_id={franchise_credential_id}` |

Na CA-150 e na URIT-8021A, a consulta por etiqueta também vai para o local:
`"consult_url": "http://localhost:8039/api/interfacing/tag?franchise_credential_id={franchise_credential_id}"`.

Os configs versionados em `build/` já trazem essas URLs nas chaves `webhook_url_teste_local` (e `consult_url_teste_local` na CA-150 e na URIT-8021A, `exam_images_url_teste_local` na BH5100). O programa ignora essas chaves: para testar, copie o valor para a chave principal no config ao lado do `.exe` de teste.

Cada máquina tem a sua rota (regra 6 do `CLAUDE.md`): a BH5100 grava hemograma, e mandar outra máquina para `/integration/bh5100` não grava os resultados. O `franchise_credential_id` precisa ser o `franchise_id` de uma unidade cadastrada no banco local. Os configs versionados em `build/` continuam apontando para produção (`https://apoio.internal.vidaexame.com`).

Para conferir se o Windows alcança o lab-apoio local:

```powershell
Invoke-WebRequest -UseBasicParsing "http://localhost:8039/api/interfacing/tag?franchise_credential_id=<franchise_id>&tag_id=<etiqueta>"
```

## Testes

```powershell
python -m unittest discover -s tests -v
```

> ⚠️ **Antes de gerar o executável**, verifique se a variável `COM_PORT` no arquivo `.py` corresponde à porta onde o cabo do equipamento está conectado. Consulte o Gerenciador de Dispositivos do Windows para confirmar. Após a build, você pode editar o arquivo `config_*.json` ao lado do `.exe` para alterar a porta COM sem precisar recompilar.

## Pastas de Trabalho

Os analisadores criam as seguintes pastas na Área de Trabalho do usuário:

- `Área de Trabalho/AnalisadorBH5100/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorCA150/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorCoagmaster/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorMEK7300/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorURIT8021/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorVIDAS1600/` — gerados, enviados, log

## Requisitos

Python 3.13+ e as dependências de `requirements.txt` (o `build_all.ps1` instala o que faltar):

- `pyserial`
- `requests`
- `pillow` (conversão das imagens BMP do BH5100 para PNG)
- `pyinstaller` (apenas para build)