# Benchmark — Depois da Fase 2

**Data:** 2026-10-16  
**Ambiente:** SQLite local (dev), Python 3.14, 20 execuções por rota  
**Ferramenta:** `tools/benchmark.py` com `tools/query_counter.py`

## Dados de teste

- 1 workspace (`bench-ws`)
- 30 membros
- 3 equipes (10 membros cada, 1 líder a cada 10)
- ~50 entradas de escala

## Resultados

| Rota | Método | Média (ms) | Queries (Fase 0) | Queries (Fase 1) | Queries (Fase 2) |
|---|---|---:|---:|---:|---:|
| `/workspace/bench-ws/escala` | GET | 6.29 | 8.5 | 5.0 | **5.0** |
| `/api/workspace/bench-ws/teams` | GET | 4.72 | 4.0 | 5.0 | **5.0** |
| `/api/workspace/bench-ws/schedule` | GET | 5.88 | 4.0 | 5.0 | **5.0** |
| `/api/workspace/bench-ws/schedule` | POST (1 célula) | 3.44 | 2.0 | 2.0 | **2.0** |
| `/api/workspace/bench-ws/schedule/bulk` | POST (30 células) | 3.44 | 2.0 | 2.0 | **2.0** |
| `/api/workspace/bench-ws/schedule/bulk-delete` | POST (30 ids) | 4.00 | 2.0 | 2.0 | **2.0** |

## Resumo das mudanças (Fase 2)

| Diagnóstico | Commit | Efeito |
|---|---|---|
| D6 | `perf(D6): calcula busy por membro uma vez por requisição` | Elimina recalculo redundante de `busy` e parse repetido de JSON |
| D7 | `perf(D7): filtra membros por equipe em _conflicts_for_cells` | Evita carregar todos os membros do workspace para checar conflitos de uma equipe |
| D8 | `perf(D8): atualiza equipes no frontend sem refresh total` | Operações de equipe atualizam `TEAMS` e `SCHEDULE` localmente sem disparar `refresh()` completo |
| D9 | `perf(D9): documenta decisão de rate limit no serverless` | Registrado em `docs/achados.md` |

## Critérios de aceite

- [x] Contratos de API preservados (JSONs idênticos)
- [x] Conflitos por célula calculados apenas para integrantes da equipe envolvida
- [x] Ações de equipe no frontend sem disparar requisições GET adicionais
- [x] Decisão do rate limit documentada em `docs/achados.md`
