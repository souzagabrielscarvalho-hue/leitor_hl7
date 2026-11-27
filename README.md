# Leitor HL7 - Integração de Analisadores Laboratoriais

Sistema de integração para leitura de dados de analisadores laboratoriais via porta serial, parsing de mensagens HL7/logs proprietários e envio para webhook.

## Analisadores Suportados

| Analisador | Pasta | Protocolo | Porta Padrão |
|---|---|---|---|
| **BH5100** (Mindray) | `bh5100/` | HL7 via serial ou TCP/IP | COM3 / TCP 5100 |
| **CA-150** (URIT) | `ca150/` | HL7 via TCP/IP (a máquina não tem serial), com consulta por etiqueta | TCP 5150 |
| **Coagmaster** (Teco) | `Coagmaster/` | Log proprietário via serial ou TCP | COM4 / TCP 5200 |
| **MEK7300** (Nihon Kohden) | `mek7300/` | Texto proprietário via serial ou TCP | COM5 / TCP 7300 |
| **PKL 125** | `pkl/` | ASTM E1394 bidirecional via serial ou TCP | COM3 / TCP 5125 |
| **URIT-8021A** (URIT) | `urit-8021/` | HL7 via TCP/IP (ou serial), com consulta por etiqueta | TCP 8021 |
| **VIDAS 1600** (bioMérieux) | `vidas1600/` | HL7 via serial ou TCP | COM5 / TCP 5160 |

**Serial ou TCP:** toda máquina aceita `"modo": "serial"` (usa `com_port`/`baud_rate`) ou `"modo": "tcp"` no config.
Em TCP o programa escuta em `tcp_host:tcp_port` (`0.0.0.0` = qualquer placa de rede) e a máquina conecta no IP deste
PC, como na tela LIS dela; o protocolo é o mesmo da serial. Libere a porta no Firewall do Windows. Configs antigos
ganham as chaves sozinhos e continuam em `serial`.

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
├── Comandos/                  # build.sh, enviar.sh e release.sh (terminal do VSCode/WSL)
├── .github/workflows/         # release.yml: publica o leitor.exe nas releases
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

## Leitor universal (`leitor.exe`)

Um .exe só para todas as máquinas, com painel próprio. Fica em `build/leitor/leitor.exe`; os .exe de cada máquina
continuam existindo e funcionando como antes.

- **Abrir o painel:** dois cliques no `leitor.exe`. O painel é uma janela nativa do programa, em Qt (PySide6, Qt
  Widgets), sem navegador. Abrir de novo só traz para a frente a janela que já está aberta.
- **Equipamentos:** a lista mostra só as máquinas adicionadas neste computador. "Adicionar equipamento" escolhe o
  modelo, a pasta do `config_<maquina>.json` e os parâmetros; se a pasta já tiver o arquivo, ele é usado como está
  (nunca é sobrescrito). Configs que já existem ao lado do `leitor.exe` entram na lista sozinhos. A lista fica em
  `leitor_painel.json`, ao lado do `leitor.exe`; "Remover da lista" mantém o arquivo.
- **Por equipamento:** status (conexão, enviados hoje, pendentes em `gerados/`, erros, tempo ligado), formulário do
  `config_<maquina>.json` (na pasta escolhida ao adicionar), iniciar/parar/reiniciar e o log ao vivo com filtro.
- **Administrador:** ver, iniciar e parar são livres; alterar a configuração (salvar, adicionar, remover, trocar a
  porta sugerida) pede a senha mestra. A sessão termina após 15 min sem alterações ou ao fechar o painel.
- **Configurações** (barra de título): tema claro, escuro ou do sistema; entrar/sair como administrador; pasta raiz
  do programa; iniciar com o Windows; abrir a pasta do programa; versão e atualização.
- **Primeira abertura:** um assistente pede a senha de administrador e a pasta raiz (dá para criar uma pasta nova ali
  mesmo ou não usar pasta raiz). Só aparece enquanto não existir `leitor_raiz.json` ao lado do `leitor.exe`.
- **Pasta raiz do programa:** uma pasta para configs, `leitor_painel.json`, pastas `Analisador<MAQUINA>` e log do
  painel (`<raiz>\Painel`). Sem ela, configs ficam ao lado do `leitor.exe` e os dados na Área de Trabalho. Ao trocar,
  configs e lista são copiados (o que já existir na pasta nova é usado como está) e os exames pendentes de
  `gerados/` vão junto; o histórico fica na antiga. A escolha fica em `leitor_raiz.json`, ao lado do `leitor.exe`.
- **Iniciar com o Windows:** o painel abre minimizado ao entrar no Windows (`leitor.exe --ao-ligar`, em
  `HKCU\...\Run`) e inicia as máquinas com "Iniciar ao ligar" marcado no cabeçalho delas.
- **Portas seriais:** lista as COM (inclusive virtuais: com0com, VSPE, Eltima) com descrição, fabricante, VID:PID,
  série e posição USB; mostra o equipamento configurado em cada uma e, em "Verificar disponibilidade", se outro
  programa está com ela. Se o Windows trocar o número da COM do adaptador de uma máquina, o painel sugere a COM nova.
- **Cada máquina roda em processo próprio** (`leitor.exe --maquina ca150`), com o mesmo log, `gerados/`, watchdog e
  popup de queda do .exe dela. Fechar a janela encerra só o painel; as máquinas continuam rodando.
- Log do painel: `Desktop\LeitorHL7\leitor_painel.log`.
- **Versão e atualização:** clique na versão (canto inferior direito) → "Sobre esta versão" ou "Verificar
  atualização". A versão nova vem da última release de `souzagabrielscarvalho-hue/leitor_hl7` (tag `vX.Y.Z`, anexo
  `leitor.exe`); o download é conferido pelo SHA-256 que o GitHub publica, o atual fica como `leitor.exe.anterior` e o
  painel reabre na versão nova. As máquinas abertas seguem na versão anterior até serem reiniciadas.

### Senha mestra

A senha de administrador é a mesma em todas as unidades e vai embutida no `leitor.exe` só como hash
PBKDF2-SHA256, em `HASH_SENHA_MESTRA` (`shared/painel/acesso.py`). O repositório é público: nunca escreva a senha em
arquivo, commit ou chat, e use 14+ caracteres. Para definir ou trocar (grava só o hash) e depois recompilar:

```bash
python3 -m shared.painel.acesso
```

Sem hash, o painel abre normalmente, mas não deixa alterar nenhuma configuração.

### Publicar uma versão

As unidades atualizam pelas releases de `souzagabrielscarvalho-hue/leitor_hl7` (`REPOSITORIO` em
`shared/painel/atualizacao.py`): tag `vX.Y.Z` com os anexos `leitor.exe` e `leitor.exe.sha256`. O `.exe` da release
não é o do build local: o GitHub Actions compila o dele a partir do commit da tag.

1. **Versão:** suba a `VERSAO` em `shared/versao.py` e escreva a seção `## X.Y.Z` no `CHANGELOG.md`.
2. **Envie o código** para o repositório (terminal do VSCode/WSL):

   ```bash
   Comandos/enviar.sh pessoal "Versão 7.4.0: ..."
   ```

3. **Lance a release:**

   ```bash
   Comandos/release.sh
   ```

   Ele confere se a tag bate com a `VERSAO`, se o CHANGELOG tem a seção, se não há nada sem commit e se o commit já
   está no GitHub; pede confirmação (`s`) e envia a tag `v<VERSAO>`.
4. **Acompanhe** em `https://github.com/souzagabrielscarvalho-hue/leitor_hl7/actions`. O workflow
   (`.github/workflows/release.yml`) roda os testes, compila o `leitor.exe` no Windows, confere que ele abre e publica
   a release com o `leitor.exe`, o `leitor.exe.sha256` e as notas do CHANGELOG. Se falhar, corrija e clique em
   **Re-run jobs** (os anexos são substituídos).

A partir daí, "Verificar atualização" no painel das unidades encontra a versão nova. Primeira instalação numa
unidade: `https://github.com/souzagabrielscarvalho-hue/leitor_hl7/releases/latest/download/leitor.exe`.

**Testar sem publicar:** todo push numa branch fora da `main` (e todo pull request) roda o mesmo workflow, menos a
publicação. Na página da execução (*Actions*), o `leitor.exe` fica em **Artifacts** por 14 dias para baixar e testar.
Para testar também a publicação, envie uma tag `v<VERSAO>` para um **fork**: a release sai no fork, e as unidades
continuam lendo só o repositório de `REPOSITORIO`. Num fork, o Actions vem desligado: ative na aba *Actions*.

**Antes da primeira release:**

- O `.github/workflows/release.yml` precisa estar no commit da tag: só tag enviada depois dele no GitHub dispara o
  build.
- O `release.sh` e o `enviar.sh` empurram direto para `souzagabrielscarvalho-hue/leitor_hl7`: o git de quem roda
  precisa ter permissão de push lá. De um fork, o caminho é o pull request.
- O repositório precisa permitir Actions com escrita. Se a publicação falhar por permissão: Settings > Actions >
  General > Workflow permissions > "Read and write permissions".

Sem WSL, dá para criar a tag à mão (sem as conferências do `release.sh`):

```bash
git tag -a v7.4.0 -m "Leitor HL7 7.4.0"
git push https://github.com/souzagabrielscarvalho-hue/leitor_hl7.git refs/tags/v7.4.0
```

### Linux

`./linux/build_linux.sh` (precisa de podman ou docker) compila no container `manylinux_2_34` e gera em
`build/linux/` pacotes para Ubuntu 22.04+, Debian 12+, RHEL/Alma/Rocky 9+ e Fedora:

| Pacote | Instalação | Atualização |
|---|---|---|
| `leitor-hl7-<versão>-x86_64.AppImage` | `chmod +x` e abrir | pelo painel |
| `leitor-hl7_<versão>_amd64.deb` | `sudo apt install ./leitor-hl7_*.deb` | pacote novo |
| `leitor-hl7-<versão>-1.x86_64.rpm` | `sudo dnf install ./leitor-hl7-*.rpm` | pacote novo |
| `leitor-hl7-<versão>-linux-x86_64.tar.gz` | extrair e rodar `leitor-hl7/leitor-hl7` | pacote novo |

No Linux empacotado os configs ficam em `~/.config/leitor-hl7`, o log e `gerados/` em `~/Desktop/Analisador<MAQUINA>`,
e o usuário precisa estar no grupo `dialout` para abrir as portas seriais. O popup de queda usa o zenity ou o
kdialog.

Desenvolvimento:

```bash
# janela do painel (precisa do PySide6: pip install -r requirements.txt)
python3 universal/leitor.py

# uma máquina, como o .exe dela
python3 universal/leitor.py --maquina ca150
```

Código: `shared/painel/nucleo.py` (o que a janela faz, sem Qt), `janela.py`, `janela_versao.py` e `tema.py`
(interface), `atualizacao.py`, `config.py`, `processos.py` e `portas.py`. Ícone: `universal/icone/leitor.svg` (o `leitor.ico` é gerado dele).

Máquina nova no leitor universal: registrar em `shared/maquinas.py`, acrescentar ao `hiddenimports` de
`universal/leitor.spec` e expor no módulo `MACHINE_ID`, `MACHINE_NAME`, `HEALTH_PORT`, `CONFIG_DEFAULTS` e
`criar_analisador()`.

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

## BH5100 por rede (modo TCP, opcional)

O BH5100 funciona por serial (default) ou por rede. Para tirar o cabo serial da jogada (as quedas de COM por suspensão de USB somem), ligue a máquina por TCP/IP. Como na CA-150 e na URIT-8021A, **a máquina é o cliente e o `bh5100.exe` é o servidor**: o leitor escuta em `tcp_host:tcp_port` e a máquina conecta no IP do PC.

No `config_bh5100.json`, ao lado do `.exe`:

```jsonc
{
    "modo": "tcp",             // default é "serial"; só a unidade por rede troca para "tcp"
    "tcp_host": "0.0.0.0",     // escuta em todas as interfaces do PC
    "tcp_port": 5100           // porta = número da máquina (liberar no Firewall do Windows)
    // com_port/baud_rate continuam no config e valem quando "modo": "serial"
}
```

Na BH5100 (Mindray), no menu de comunicação/LIS do equipamento: tipo de conexão Ethernet/TCP (LIS), protocolo HL7, IP do host = IP do PC, porta = 5100. Reserve o IP do PC por MAC no roteador (se o DHCP mudar o IP, a máquina perde a conexão).

No modo TCP, uma conexão nova da máquina substitui a anterior. Se a máquina desconectar e não voltar em 5 minutos, vale a mesma regra da queda da COM: o programa envia `gerados/`, mostra o popup e encerra. Antes da primeira conexão não conta como queda. O ACK^R01 sai pela conexão ativa nos dois modos. Health check em `http://localhost:8080/health`.

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

Vale para todas as máquinas, em serial e em TCP. Em TCP, antes da primeira conexão da máquina não conta como queda, e uma conexão nova que substitui a anterior também não. O envio de resultados e imagens roda em uma thread separada da leitura serial, então continua com a COM caída.

1. Enquanto a porta está caída, o programa tenta reconectar e segue enviando o que estiver em `gerados/`. Se a porta voltar em até 5 minutos, nada muda para o usuário.
2. Depois de 5 minutos sem conexão, ele encerra assim que `gerados/` esvaziar.
3. Se `gerados/` ficar 15 minutos sem diminuir (por exemplo, servidor fora), ele encerra mesmo assim. O que sobrou continua em `gerados/` e é enviado na próxima abertura.

Antes de encerrar, aparece um popup com a porta, o erro da queda, o erro da última reconexão e a contagem dos exames enviados, não enviados e ainda pendentes. O programa só fecha depois do clique em OK, mas já solta a porta antes do popup, para poder ser reaberto.

Arquivos que falharam vão para `requisições não enviadas/` e não são reenviados automaticamente.

## Build

O build roda no Windows (PyInstaller). Dá para disparar pelo terminal do VSCode (WSL), como no arquivo `codigo`, ou
direto pelo PowerShell do Windows. O `.exe` vai para `build\<alvo>\` (ex.: `build\leitor\leitor.exe`) e a saída do
PyInstaller para `build_logs\<alvo>.log`.

### Uma vez por PC

Crie o venv na raiz do projeto (os scripts usam `venv\Scripts\python.exe`; sem ele, o Python do PATH):

```powershell
cd C:\caminho\do\leitor_hl7
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

### Pelo terminal do VSCode (WSL)

O `build.sh` chama o PowerShell do Windows por você:

```bash
Comandos/build.sh leitor                        # só o leitor.exe (leitor universal) -> build/leitor/
Comandos/build.sh ca150                         # uma máquina -> build/ca150/
Comandos/build.sh todas                         # leitor.exe e todas as máquinas
LEITOR_BUILD_LIMPO=1 Comandos/build.sh todas    # descarta o cache e compila do zero
```

Alvos: `leitor`, `bh5100`, `ca150`, `coagmaster`, `mek7300`, `pkl`, `urit8021`, `vidas1600` e `todas`.

### Direto no PowerShell (sem WSL)

```powershell
powershell -ExecutionPolicy Bypass -File .\Comandos\build_leitor.ps1   # só o leitor.exe
powershell -ExecutionPolicy Bypass -File .\Comandos\build_todas.ps1    # todas
powershell -ExecutionPolicy Bypass -File .\Comandos\build_ca150.ps1    # uma máquina

$env:LEITOR_BUILD_LIMPO = "1"    # antes do comando, para compilar do zero
```

### Como funciona

- **Velocidade:** cada exe tem cache próprio do PyInstaller em `build_cache\<nome>` (fora do git), reaproveitado no
  build seguinte; o tempo de cada um aparece no resultado. Use `LEITOR_BUILD_LIMPO=1` se o build sair estranho, por
  exemplo depois de trocar o Python.
- **Antes de compilar:** cada alvo confere se o módulo importa (o PyInstaller compila mesmo faltando um módulo, e o
  exe quebraria ao abrir).
- **Executáveis:** `leitor`, `bh5100`, `ca150`, `coagmaster`, `mek7300`, `pkl`, `test_get_only`, `urit8021` e
  `vidas1600` (o `test_get_only.exe` vai para `build/pkl/`). Máquina nova ganha o `Comandos/build_<maquina>.ps1` e
  entra no `build_todas.ps1` (regra 7 do CLAUDE.md).

### Envio

O envio é separado: `Comandos/enviar.sh pessoal` (repositório público `souzagabrielscarvalho-hue/leitor_hl7`) ou
`Comandos/enviar.sh ambos` (também o `Vida-Exames/HL7_integrator`). Ele commita, recusa token preenchido e envia a
branch atual. Para lançar uma versão do `leitor.exe` para as unidades, veja
[Publicar uma versão](#publicar-uma-versão).

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

Python 3.12+ e as dependências de `requirements.txt` (`pip install -r requirements.txt` no venv do Windows):

- `pyserial`
- `requests`
- `pillow` (conversão das imagens BMP do BH5100 para PNG)
- `pyinstaller` (apenas para build)