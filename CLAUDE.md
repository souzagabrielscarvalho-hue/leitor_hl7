# Guia para IA: leitor_hl7

Leitores das máquinas de laboratório (BH5100, CA-150, Coagmaster, MEK7300, PKL, URIT-8021A, VIDAS 1600). Cada leitor vira um `.exe`
(PyInstaller) que roda no Windows ao lado da máquina, lê os resultados (serial ou TCP) e envia ao lab-apoio.

Responda, comente o código e escreva commits em português do Brasil.

## Onde trabalhar

- Este repositório (público) é onde os leitores são **criados e testados**. O `HL7_integrator` (empresa) só recebe o
  código quando tudo estiver pronto e funcionando, e só quando o usuário pedir.
- A IA não cria branch, não faz commit nem push: deixa as alterações no working tree e sugere o texto do commit.
- A tela **Laboratório Interno > Máquinas** (`/laboratory/machines`) baixa o `.exe` e o JSON direto da branch `main`
  deste repositório no GitHub, a cada clique (`back_vida_exame/config/laboratory_machines.php`). O que está na
  `main` é o que as unidades instalam.
- A base comum fica em `shared/base_analisador.py` e serve a todas as máquinas. Não mude o comportamento das outras
  máquinas para atender uma: prefira um gancho (método com padrão vazio) e sobrescreva na máquina.

## Regras obrigatórias

### 1. Todo passo vai para o log de execução

- O log é o arquivo de texto `Desktop\Analisador<MAQUINA>\analisador_<maquina>.log`, gravado pelo `logging`. Os dias
  anteriores (e o trecho de abertura) ficam em `logs\`.
- Cada etapa, **com sucesso ou com falha**, deixa uma linha: recebeu da máquina, consultou, respondeu à máquina, a
  máquina confirmou, enviou ao lab-apoio, o que foi gravado e o que ficou de fora. Use `✓` para sucesso e `✗` para
  falha, e cite a etiqueta.
- Não registre "enviado" sem confirmar que a escrita deu certo (ex.: `_escrever` da CA-150 devolve `bool`).
- O `.exe` não tem console: exceção em thread some sem rastro. Envolva as threads com `try/except` e
  `logging.exception`.
- Nada de `print`. `shared/config_loader.py` não usa `logging` (roda antes do `basicConfig`).

### 2. Popup quando a conexão cair, só depois de enviar `gerados/`

- Vale para serial e TCP. Quem cuida é o watchdog da base (`_start_connection_watchdog`):
  1. a conexão cai e o programa tenta reconectar por `CONNECTION_DEADLINE` (5 min);
  2. se não voltar, espera a pasta `gerados/` ser enviada (ou ir para "requisições não enviadas");
  3. só então mostra o popup "Conexão perdida" (com erro e contagem de exames) e encerra.
- Exceção mantida de propósito: se `gerados/` ficar `DRAIN_STALL_TIMEOUT` (15 min) sem diminuir, mostra o popup e
  encerra mesmo assim, informando os pendentes.
- Em TCP, antes da primeira conexão da máquina não é queda, e uma conexão substituída por outra também não.
- Popup que não deve encerrar (ex.: porta TCP ocupada) usa `_popup_sem_travar` e nunca abre dois ao mesmo tempo.
- Toda máquina nova precisa ter teste de queda (veja `tests/test_interfaceamento.py::TestQuedaCOM` e
  `tests/test_ca150.py::TestTCP` ou `tests/test_urit8021.py::TestTCP`).

### 3. Nunca commitar chave real

- Nos JSONs versionados em `build/`, `franchise_credential_id`, tokens e `pkl_machine_id` vão sempre como
  `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX` (ou `XXXX...`). Os valores reais ficam só no config ao lado do `.exe`, na
  máquina.
- Antes de cada commit: `git diff --cached` e procure UUIDs/tokens. O mesmo vale para README, docs, testes e logs
  colados.

### 4. Endpoint de teste local nos JSONs

- Cada `build/<maquina>/config_*.json` tem as URLs de produção (`https://apoio.internal.vidaexame.com`) nas chaves
  usadas pelo programa e as de teste local nas chaves `*_teste_local` (`http://localhost:8039`, o lab-apoio no
  Docker).
- O `config_loader` ignora as chaves `*_teste_local`; elas são referência. Para testar localmente, copie o valor para
  a chave principal (`webhook_url`, `consult_url`, `exam_images_url`) no config ao lado do `.exe` de teste.
- Máquina nova: acrescente as duas versões no JSON e a linha na tabela "Testes no ambiente local" do README.
- Teste sempre no lab-apoio local antes de usar produção.

### 5. Sempre deixar o número da versão

- A versão é uma só para todos os leitores, em `shared/versao.py` (`VERSAO`), no formato MAJOR.MINOR.PATCH. A atual
  é **7.2.0**.
- Toda mudança que gera `.exe` novo sobe a versão (MAJOR: muda a instalação, como config, rota ou protocolo;
  MINOR: função nova; PATCH: correção) e ganha uma entrada no `CHANGELOG.md`.
- A versão aparece na abertura do log (`Analisador <MAQUINA> - Serviço de Integração - versão X.Y.Z`) e no
  `GET /health` (`version`). Não remova esses pontos.
- Depois de subir a versão, recompile os `.exe` afetados para que eles mostrem o número novo.

### 6. Sempre criar e utilizar um endpoint separado para cada máquina

- Cada máquina envia para a sua própria rota no lab-apoio (`POST /api/integration/<maquina>`), com o seu
  `IntegrationNameEnum` (tentativa na Atividade de Integração), canal de log e reenvio em
  `IntegrationAttemptController`. Nunca aponte o `webhook_url` de uma máquina para a rota de outra, mesmo com o
  mesmo payload.
- Se a regra de gravação for igual à de outra máquina, reaproveite o service (ex.: `IntegrationUrit8021Service`
  estende `IntegrationCa150Service` e só troca o canal de log), mas mantenha rota, controller e tentativa próprios.
- Máquina nova: crie a rota e o teste no lab-apoio (`tests/Feature/Integration<Maquina>Test.php`) junto com o leitor,
  e use essa rota no `webhook_url` do `config_defaults` e do `build/<maquina>/config_*.json`.

### 7. Sempre criar o código para compilar a máquina nova e compilá-la junto às demais

- Ao criar uma máquina nova, crie também o script para compilá-la: adicione `Comandos/build_<maquina>.ps1`
  (seguindo os existentes), registre a máquina no mapa `$mapa` de `Comandos/_lib_build.ps1` (nome do
  analisador -> pasta do spec) e inclua-a na lista `$maquinas` de `Comandos/build_todas.ps1`.
- Assim a máquina nova é compilada tanto sozinha (`build.sh <maquina>`) quanto junto com todas as outras
  (`build.sh todas`). Toda máquina deve aparecer no build de todas; nunca deixe uma máquina de fora.

## Testes e build

- Testes (WSL, na raiz): `python3 -m unittest discover -s tests`. O teste de imagem da BH5100 precisa do Pillow
  (sem ele, fica pulado).
- Os testes usam servidor HTTP, serial e TCP locais; nada sai da máquina.
- Mudou código de uma máquina: recompile o `.exe` e atualize `build/<maquina>/<maquina>.exe`. O build e o
  envio ficam em `Comandos/` e rodam no terminal do VSCode (WSL): `Comandos/build.sh <maquina|todas>`
  (chama o PyInstaller no Windows via `powershell.exe`) e `Comandos/enviar.sh <pessoal|ambos>` (git do WSL).
  O arquivo `codigo` tem os comandos prontos.
- Manuais em PDF: extraia o texto com
  `python3 -I ~/.claude/tools/pdf/extrair_pdf.py "<arquivo.pdf>" <saida.txt>`.
