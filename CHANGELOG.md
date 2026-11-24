# Changelog

Versão única para todos os leitores, definida em `shared/versao.py`. Ela aparece na abertura do
`analisador_<maquina>.log` e no `GET /health`.

## 7.0.0 - 2026-11-24

Primeira versão numerada.

- CA-150 (URIT, bioquímica): leitor novo por TCP na porta 5150, com consulta por etiqueta
  (`GET /api/interfacing/tag`) e envio para `POST /api/integration/ca-150`.
- Log: cada etapa com sucesso ou falha (consulta, resposta à máquina, confirmação da máquina, o que o lab-apoio
  gravou e o que ficou de fora). Erros em threads vão para o log.
- Queda da conexão (serial e TCP): popup e encerramento só depois de enviar `gerados/`.
- Configs em `build/` com chaves `*_teste_local` (lab-apoio local em `http://localhost:8039`) e sem chaves reais.
- Versão no log de abertura e no health check.
