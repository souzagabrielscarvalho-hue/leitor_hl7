# Changelog

Versão única para todos os leitores, definida em `shared/versao.py`. Ela aparece na abertura do
`analisador_<maquina>.log` e no `GET /health`.

## 7.4.0 - 2026-11-27

- Leitor universal `leitor.exe` (`universal/leitor.py`): um .exe para todas as máquinas, com painel em janela
  nativa (Qt Widgets, PySide6). O painel lista as máquinas adicionadas neste computador, edita o
  `config_<maquina>.json` em formulário, inicia, para e reinicia cada uma e mostra o estado (pelo `GET /health`), os
  exames em `gerados/` e o log ao vivo. Abrir o .exe de novo traz a janela aberta para a frente. Ícone próprio
  (`universal/icone/leitor.svg`). Dependência nova: `PySide6-Essentials`.
- Painel: "Adicionar equipamento" escolhe o modelo, a pasta do arquivo de configuração e os parâmetros. Se a pasta já
  tem o `config_<maquina>.json`, ele é usado como está, sem ser sobrescrito. A lista e a pasta de cada máquina ficam
  em `leitor_painel.json`; configs que já existem ao lado do .exe entram na lista sozinhos. "Remover da lista" mantém o
  arquivo. A máquina aberta pelo painel lê o config da pasta dela (variável `LEITOR_HL7_CONFIG_DIR`, que só o painel
  define; sem ela, os .exe de cada máquina continuam lendo ao lado do .exe).
- Painel: clicar na versão (canto inferior direito) abre o menu com "Sobre esta versão" (trecho do CHANGELOG) e
  "Verificar atualização". A versão nova vem da última release do GitHub (`souzagabrielscarvalho-hue/leitor_hl7`,
  anexo `leitor.exe`); o download é conferido pelo SHA-256 publicado (sem SHA-256, não instala), o executável atual
  fica como `leitor.exe.anterior` e o painel reabre na versão nova. As máquinas abertas seguem na versão anterior até
  serem reiniciadas.
- Painel: portas virtuais (com0com, VSPE, Eltima...) aparecem na lista. Além do pyserial, o painel lê o registro
  `HARDWARE\DEVICEMAP\SERIALCOMM` e os nomes `COMn` do Windows (`QueryDosDevice`).
- Cada máquina continua em processo próprio (`leitor.exe --maquina <id>`), com o mesmo comportamento do .exe dela:
  log em `Desktop\Analisador<MAQUINA>`, watchdog e popup de queda. Fechar o painel não para as máquinas. O painel tem
  log próprio em `Desktop\LeitorHL7\leitor_painel.log`.
- Painel: valida o config antes de salvar (UUID da unidade, nada de `XXXX...`; o `webhook_url` só pode trocar o host,
  nunca a rota da máquina) e não deixa iniciar máquina sem config válido.
- Painel: tela "Portas seriais" com o que o Windows informa de cada porta (descrição, fabricante, VID:PID, número de
  série, posição USB), as máquinas configuradas em cada uma e, sob demanda, se outro programa está usando a porta.
  O painel lembra o adaptador USB de cada máquina (`leitor_painel.json`) e, se o Windows trocar o número da COM,
  sugere a COM nova.
- Os .exe de cada máquina continuam sendo gerados e funcionando como antes. Cada módulo de máquina agora expõe
  `MACHINE_ID`, `MACHINE_NAME`, `HEALTH_PORT`, `CONFIG_DEFAULTS` e `criar_analisador()`.
- `GET /health` (todas as máquinas): novo campo `pid`, e `port_open` passa a ser atualizado pelo watchdog também nas
  máquinas sem `SerialListener` (antes ficava sempre `false` na BH5100, CA-150, Coagmaster, URIT-8021A e VIDAS).
  Em TCP, `port_open` é a máquina conectada agora.
- Serial ou TCP em todas as máquinas: Coagmaster, MEK7300, PKL e VIDAS 1600 ganham `"modo": "tcp"` (a máquina
  conecta neste PC em `tcp_host:tcp_port`), como a CA-150, a URIT-8021A e a BH5100 (7.2.0, TCP próprio) já tinham. O protocolo não muda: a
  conexão TCP (`shared/conexao_tcp.py`) se comporta como uma porta serial para os mesmos laços de leitura, inclusive
  o ASTM bidirecional do PKL. Configs existentes ganham `modo: "serial"`, `tcp_host` e `tcp_port` sozinhos ao abrir
  e continuam na serial. Portas TCP padrão: Coagmaster 5200, MEK7300 7300, PKL 5125, VIDAS 1600 5160.
  Queda em TCP segue a regra da serial (envia `gerados/`, popup, encerra); antes da primeira conexão e conexão
  substituída não são queda; porta TCP ocupada avisa com `_popup_sem_travar` e tenta de novo.
- Painel: configuração só com administrador. Salvar, adicionar, remover equipamento e trocar a porta sugerida pedem a
  senha mestra (o núcleo recusa também, não só a tela). A senha fica no código só como hash PBKDF2-SHA256
  (`shared/painel/acesso.py`, definida com `python3 -m shared.painel.acesso`); sem hash no build, a edição fica
  bloqueada. A sessão termina após 15 min sem alterações ou ao fechar o painel; 5 senhas erradas bloqueiam por 60 s.
  Ver, iniciar e parar continuam livres.
- Painel: botão "Configurações" na barra de título, com Tema (seguir o sistema, claro, escuro; fica em
  `leitor_painel.json`), entrar/sair como administrador, abrir a pasta do programa e versão/atualização. O indicador
  "Somente leitura"/"Administrador" mostra o acesso atual. Campos bloqueados aparecem apagados.
- Painel: Configurações > "Pasta raiz do programa". Uma pasta para tudo o que o leitor grava: configs,
  `leitor_painel.json`, as pastas `Analisador<MAQUINA>` (antes sempre na Área de Trabalho) e o log do painel
  (`<raiz>\Painel`). Ao trocar, os configs e a lista são copiados (sem sobrescrever o que já houver na pasta nova) e
  os exames pendentes de `gerados/` vão junto; o histórico fica na pasta antiga. Exige administrador e as máquinas
  paradas. A escolha fica em `leitor_raiz.json`, ao lado do `leitor.exe`; sem ele, nada muda. A base lê a pasta de
  dados de `LEITOR_HL7_DADOS_DIR` (`config_loader.pasta_dados()`).
- Painel: primeira abertura neste computador (sem `leitor_raiz.json`) abre um assistente antes da janela: 1) senha
  de administrador; 2) pasta raiz do programa (sugestão `C:\LeitorHL7`), com "Procurar…", "Criar nova pasta…" ou
  "Não usar pasta raiz". Fechar o assistente fecha o painel, que pergunta de novo na próxima vez. Ao ligar com o
  Windows nunca pergunta; build sem senha mestra pula o assistente.
- Painel: Configurações > "Iniciar com o Windows" registra o `leitor.exe --ao-ligar` em
  `HKCU\...\CurrentVersion\Run`. Ao entrar no Windows, o painel abre minimizado e inicia as máquinas marcadas com
  "Iniciar ao ligar" (caixa no cabeçalho de cada máquina; marcada por padrão) que estejam paradas e com config
  pronto. Exige administrador.
- Build (`Comandos/`): alvo `leitor` (`build.sh leitor`, e primeiro no `build.sh todas`) e cache do PyInstaller
  por exe em `build_cache\<nome>` (sem `--clean`, reaproveitado no build seguinte; `LEITOR_BUILD_LIMPO=1` compila do
  zero). Mostra o tempo de cada exe.
- Releases: `Comandos/release.sh` confere VERSAO, CHANGELOG e o commit já enviado e cria a tag `vX.Y.Z` no
  repositório pessoal; o workflow `.github/workflows/release.yml` roda os testes, compila o `leitor.exe` no Windows,
  confere que ele abre e publica a release só com o `leitor.exe` (SHA-256 no `digest` do anexo e no texto), de onde o painel das unidades
  atualiza. `.github/` não vai para a empresa.

## 7.3.0 - 2026-11-27

- De-para de códigos de exame configurável, agora na **base** (todas as máquinas), pela chave
  `depara_codigos` do config, no formato `{"código da máquina": "código do Vida"}`, ex.:
  `{"HDLE": "HDL"}`. Resolve o caso de itens de Sistema Fechado da URIT (ex.: `HDLE`), cujo código
  não pode ser alterado na máquina, sem precisar recriar o item.
  - **Envio (todas):** antes do POST ao lab-apoio, troca o código da máquina pelo do Vida nos
    resultados que trazem lista `results` com o código em `code` (CA-150, URIT-8021) ou `test` (PKL).
    Payloads de hemograma (BH5100, MEK7300, VIDAS1600), que não têm código por item em `results`,
    passam inalterados.
  - **Consulta (quem busca por etiqueta):** CA-150 e URIT-8021 traduzem o código do atributo do Vida
    para o código da máquina antes de montar o DSR (senão a máquina não reconhece o exame).
  - Códigos iguais nos dois lados não precisam entrar no mapa. A chave é lida de qualquer
    `config_<maquina>.json` e, se faltar, é acrescentada vazia automaticamente.

## 7.2.0 - 2026-11-26

- Modo TCP opcional na BH5100 (`"modo": "tcp"`, porta padrão 5100); default continua serial.
  Em TCP a máquina é o cliente e o leitor (PC) é o servidor, como na CA-150 e na URIT-8021,
  tirando o cabo serial da jogada (fim das quedas de COM por suspensão de USB). O ACK^R01 passa
  a sair pela conexão ativa nos dois modos. Queda de conexão TCP segue a mesma regra: espera
  `gerados/` ser enviada antes do popup e do encerramento.

## 7.1.1 - 2026-11-26

- BH5100: corrige o `ValueError: substring not found` no parsing serial (`detect_complete_message`).
  Um `EB` órfão antes do `SB` no buffer (frame parcial/ruído deixado por uma queda da COM) fazia o
  loop serial ficar preso reprocessando o mesmo lixo (rajadas de `Erro no loop serial` ~1x/s). Agora
  usa `str.find`, descarta o lixo anterior ao `SB` e ignora frames inválidos (`EB` sem `CR`).
- Log de envio (todas as máquinas): quando o lab-apoio recusa por regra de negócio (HTTP 400, ex.:
  etiqueta não cadastrada/inválida), a linha deixa de dizer "após N tentativas" e passa a indicar a
  causa real, citando a etiqueta, orientando a corrigir antes de reenviar.

## 7.1.0 - 2026-11-24

- URIT-8021A (bioquímica): leitor novo `urit8021.exe`, pelo "Urit HL7 LIS Interface Dev Reference Manual".
  TCP na porta 8021 (padrão) ou serial (`"modo": "serial"`), com consulta por etiqueta
  (`GET /api/interfacing/tag`) e envio para `POST /api/integration/urit-8021`, endpoint próprio no lab-apoio
  (tentativa `urit8021` na Atividade de Integração e log `storage/logs/urit8021.log`).
- URIT-8021A: o ACK^R01 leva o Sample ID (OBR.3) no MSA.2, como manda o manual; os valores vão com vírgula
  (`11.8` → `11,8`); o `null` da DLL da URIT conta como vazio; resultado sem código de barras não é enviado.
- URIT-8021A: consulta por período (sem etiqueta) responde `QAK|SR|NF` com o motivo no log.
- URIT-8021A: o manual não prevê imagem (os OBX são `NM`). OBX com base64 (`ED`) fica fora dos resultados e
  vai para o log.
- Depende do lab-apoio com a rota `POST /api/integration/urit-8021`.

## 7.0.1 - 2026-11-24

- Coagmaster: um arquivo por teste em `gerados/` (`<etiqueta>_<teste>_<nº>.txt`). Antes o nome era só a
  etiqueta, e o TTPA apagava o TP do mesmo tubo se os dois chegassem antes do envio.
- Coagmaster: os valores vão com vírgula, como a máquina imprime (antes `INR`, `%` e `RELAÇÃO` iam com ponto).
- Coagmaster: a porcentagem no formato do manual (`%:   81,4 %`, com espaço antes do `%`) era perdida, e o `AP`
  do TP nunca chegava ao laudo.
- Coagmaster: TT deixou de ir para `TCO` (Tempo de Coagulação) e teste desconhecido deixou de cair em `COAGU`.
  O `ExamCode` agora é só informativo: o lab-apoio escolhe os procedimentos da etiqueta pelo teste (TP → TAP e
  COAGU; TTPA → KPTT e COAGU). FIB, TT e Hep ainda não têm atributo no sistema e são recusados com o motivo no log.
- Coagmaster: exame sem ID impresso é ignorado com `✗` no log (antes o número do exame era enviado como etiqueta).
- Coagmaster: o log mostra cada atributo gravado no laudo e os que ficaram de fora.
- Depende do lab-apoio com o `ReadBloodCountMachineCoagmaster` novo (atributos `TAP`, `AP`, `RNI`,
  `Controle`/`Control` e `KPTT`).

## 7.0.0 - 2026-11-24

Primeira versão numerada.

- CA-150 (URIT, bioquímica): leitor novo por TCP na porta 5150, com consulta por etiqueta
  (`GET /api/interfacing/tag`) e envio para `POST /api/integration/ca-150`.
- Log: cada etapa com sucesso ou falha (consulta, resposta à máquina, confirmação da máquina, o que o lab-apoio
  gravou e o que ficou de fora). Erros em threads vão para o log.
- Queda da conexão (serial e TCP): popup e encerramento só depois de enviar `gerados/`.
- Configs em `build/` com chaves `*_teste_local` (lab-apoio local em `http://localhost:8039`) e sem chaves reais.
- Versão no log de abertura e no health check.
