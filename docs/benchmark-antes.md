# Benchmark — Antes das otimizações

**Data:** 2026-10-16  
**Ambiente:** SQLite local (dev), Python 3.14, 20 execuções por rota  
**Ferramenta:** `tools/benchmark.py` com `tools/query_counter.py`

## Dados de teste

- 1 workspace (`bench-ws`)
- 30 membros
- 3 equipes (10 membros cada, 1 líder a cada 10)
- ~50 entradas de escala

## Resultados

| Rota | Método | Média (ms) | Queries |
|---|---|---:|---:|
| `/workspace/bench-ws/escala` | GET | 9.18 | 8.5 |
| `/api/workspace/bench-ws/teams` | GET | 3.52 | 4.0 |
| `/api/workspace/bench-ws/schedule` | GET | 4.09 | 4.0 |
| `/api/workspace/bench-ws/schedule` | POST (1 célula) | 2.49 | 2.0 |
| `/api/workspace/bench-ws/schedule/bulk` | POST (30 células) | 2.34 | 2.0 |
| `/api/workspace/bench-ws/schedule/bulk-delete` | POST (30 ids) | 2.99 | 2.0 |

## Observações

- Os tempos são de SQLite local. Em produção (Supabase/Vercel), a latência de rede domina.
- Os snapshots das rotas `/teams` e `/schedule` foram salvos em `tests/fixtures/` para verificação de contrato nas próximas fases.

## Diagnósticos confirmados/refutados pelo benchmark

### Confirmados
- **D1 (N+1 em `escala_page`):** Confirmado. A rota `GET /escala` executa **8.5 queries** em média, muito acima do limite de 5. O `_teams_payload` e `_schedule_payload` repetem `get_members` e `get_workspace_team_members`.
- **D3 (Carregamento da escala inteira no bulk):** Parcialmente confirmado. O bulk-delete carrega todas as entradas, mas o impacto é pequeno em SQLite local. Em produção, seria mais significativo.
- **D4 (OR gigante no bulk):** Parcialmente confirmado. O bulk insert usa `executemany` (1 query), mas a releitura usa OR. O total de 2 queries está dentro do limite, mas o OR pode ser um problema em seleções maiores.

### Refutados
- **Nenhum** dos diagnósticos foi totalmente refutado pelo benchmark local. Os problemas de latência de rede (D2) e Vercel não podem ser medidos localmente.

### Não verificáveis localmente
- **D2 (Pool de conexões serverless):** Requer ambiente Vercel/Supabase.
- **D15 (Duplo parse PDF):** Requer medição com PDF real em produção.
- **D20 (SESSION_SECRET padrão):** Já verificado no código — `main.py` tem um fallback `secrets.token_hex(32)` quando `DEBUG=True`, mas levanta `RuntimeError` quando `DEBUG=False` e `SESSION_SECRET` ausente. Isso é seguro.
