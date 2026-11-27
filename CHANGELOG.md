# Changelog

Versão única para todos os leitores, definida em `shared/versao.py`. Ela aparece na abertura do
`analisador_<maquina>.log` e no `GET /health`.

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
