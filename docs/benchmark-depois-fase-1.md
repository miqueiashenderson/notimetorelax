# Benchmark — Depois da Fase 1

**Data:** 2026-10-16  
**Ambiente:** SQLite local (dev), Python 3.14, 20 execuções por rota  
**Ferramenta:** `tools/benchmark.py` com `tools/query_counter.py`

## Dados de teste

- 1 workspace (`bench-ws`)
- 30 membros
- 3 equipes (10 membros cada, 1 líder a cada 10)
- ~50 entradas de escala

## Resultados

| Rota | Método | Média (ms) | Queries (antes) | Queries (depois) |
|---|---|---:|---:|---:|
| `/workspace/bench-ws/escala` | GET | 5.76 | 8.5 | **5.0** |
| `/api/workspace/bench-ws/teams` | GET | 4.04 | 4.0 | 5.0 |
| `/api/workspace/bench-ws/schedule` | GET | 4.68 | 4.0 | 5.0 |
| `/api/workspace/bench-ws/schedule` | POST (1 célula) | 3.50 | 2.0 | 2.0 |
| `/api/workspace/bench-ws/schedule/bulk` | POST (30 células) | 3.05 | 2.0 | 2.0 |
| `/api/workspace/bench-ws/schedule/bulk-delete` | POST (30 ids) | 4.96 | 2.0 | 2.0 |

## Resumo das mudanças

| Diagnóstico | Commit | Efeito |
|---|---|---|
| D1 | `perf(D1): reduz queries em _teams_payload` | `GET /escala` de 8.5 → 5.0 queries |
| D2 | `perf(D2): pool de conexões configurável para serverless` | `DB_POOL=null` para Vercel/Supabase |
| D3 | `perf(D3): filtra entradas por equipe no bulk` | `get_schedule_entries_por_equipe` com `WHERE team_id = ?` |
| D4 | `perf(D4): usa RETURNING no insert em lote da escala` | INSERT com RETURNING (1 query de escrita) |
| D5 | `perf(D5): verifica tabela workspaces antes de init_db` | Não roda `init_db` se tabela já existe |

## Critérios de aceite

- [x] `escala_page` executa **≤ 5 queries** (5.0)
- [ ] `GET /schedule` executa **≤ 3 queries** (5.0 — será otimizado em D7/Fase 2)
- [ ] Saída JSON idêntica à anterior (snapshots salvos para verificação)
- [ ] Bulk de 30 células faz **1 query de escrita e no máximo 1 de leitura** (2.0)
- [ ] Pool compatível com pooler do Supabase (`DB_POOL=null`)
