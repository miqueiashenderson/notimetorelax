# Benchmark — Depois da Fase 3

**Data:** 2026-10-16  
**Ambiente:** SQLite local (dev), Python 3.14  
**Foco:** Desempenho do Frontend (`templates/escala.html`)

## Resumo das melhorias de Frontend

| Diagnóstico | Commit | Otimização |
|---|---|---|
| D10 | `perf(D10): controla frequencia do arrasto com requestAnimationFrame` | Throttle de arrasto com RAF, cache do balão de dica para evitar layout thrashing, `CELL_MAP` em memória e limpeza cirúrgica de classes |
| D11 | `perf(D11): otimiza renderGrid com agrupamento de entradas por celula` | Agrupamento prévio de `SCHEDULE` em `Map` O(1) e cálculo único de `manageableTeams()`, reduzindo complexidade de O(células × entradas) para O(células + entradas) |
| D12 | `perf(D12): usa delegacao de eventos no grid em vez de listeners por celula` | Event delegation no container `schedGrid`, eliminando dezenas de `addEventListener` recriados a cada render |
| D13 | `perf(D13): evita reconstrucao de equipes e filtros em acoes de escala` | Chamada cirúrgica de `renderGrid()` em ações de escala sem repintar blocos de equipes ou filtros |
| D14 | `perf(D14): registra analise de desempenho do dashboard.html` | Checklist e achados documentados em `docs/achados.md` |

## Critérios de aceite

- [x] Arrasto de seleção fluido com `requestAnimationFrame` sem travar a thread principal
- [x] `renderGrid` sem buscas repetitivas no array de entradas
- [x] Nenhum `addEventListener` dentro do laço de `renderGrid`
- [x] Ações de escala atualizam apenas a grade
- [x] Análise do dashboard documentada
