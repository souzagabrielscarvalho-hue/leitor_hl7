# Changelog

Versão única para todos os leitores, definida em `shared/versao.py`. Ela aparece na abertura do
`analisador_<maquina>.log` e no `GET /health`.

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
