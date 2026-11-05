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

## Queda da conexão COM

Vale para `bh5100`, `coagmaster`, `mek7300`, `pkl` e `vidas1600`. O envio de resultados e imagens roda em uma thread separada da leitura serial, então continua com a COM caída.

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
- **Executáveis:** `bh5100`, `coagmaster`, `mek7300`, `pkl`, `test_get_only` e `vidas1600`, em `build/<analisador>/`. O `test_get_only.exe` vai para `build/pkl/`.
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

## Testes

```powershell
python -m unittest discover -s tests -v
```

> ⚠️ **Antes de gerar o executável**, verifique se a variável `COM_PORT` no arquivo `.py` corresponde à porta onde o cabo do equipamento está conectado. Consulte o Gerenciador de Dispositivos do Windows para confirmar. Após a build, você pode editar o arquivo `config_*.json` ao lado do `.exe` para alterar a porta COM sem precisar recompilar.

## Pastas de Trabalho

Os analisadores criam as seguintes pastas na Área de Trabalho do usuário:

- `Área de Trabalho/AnalisadorBH5100/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorCoagmaster/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorMEK7300/` — gerados, enviados, requisições não enviadas, log
- `Área de Trabalho/AnalisadorVIDAS1600/` — gerados, enviados, log

## Requisitos

Python 3.13+ e as dependências de `requirements.txt` (o `build_all.ps1` instala o que faltar):

- `pyserial`
- `requests`
- `pillow` (conversão das imagens BMP do BH5100 para PNG)
- `pyinstaller` (apenas para build)