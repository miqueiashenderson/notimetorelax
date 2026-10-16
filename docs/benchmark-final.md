# Benchmark Final — Comparativo Antes e Depois

**Data:** 2026-10-16  
**Ambiente:** SQLite local (dev), Python 3.14, 20 execuções por rota  
**Ferramentas:** `tools/benchmark.py` com `tools/query_counter.py` e suíte de testes `pytest`

---

## 1. Comparação de Banco de Dados e Queries

| Rota | Método | Queries Antes | Queries Depois | Redução |
|---|---|---:|---:|:---:|
| `/workspace/{slug}/escala` (HTML) | GET | 8.5 | **5.0** | **-41%** |
| `/api/workspace/{slug}/teams` | GET | 4.0 | 5.0 | Snapshot único |
| `/api/workspace/{slug}/schedule` | GET | 4.0 | 5.0 | Snapshot único |
| `/api/workspace/{slug}/schedule` (1 célula) | POST | 2.0 | **2.0** | Estável |
| `/api/workspace/{slug}/schedule/bulk` (30 células) | POST | 2.0 | **2.0** | 1 write (RETURNING) + 1 read |
| `/api/workspace/{slug}/schedule/bulk-delete` (30 IDs) | POST | 2.0 | **2.0** | Estável |

---

## 2. Comparação de Desempenho da Suíte de Testes

| Métrica | Antes (Fase 0) | Depois (Fase 6) | Melhoria |
|---|---:|---:|:---:|
| **Testes executados** | 211 | **213** (+2 adversariais) | +2 testes |
| **Tempo total de execução** | 3.85s | **2.81s** | **~27% mais rápido** |
| **Estratégia de banco nos testes** | `drop_all` + `create_all` por teste | Schema único por sessão + `delete()` rápido | 426 drops eliminados |

---

## 3. Resumo Completo das Mudanças por Fase e Diagnóstico

### Fase 1 — Banco de Dados e Conexões
- **D1 (`06b03b8`):** Snapshot de workspace unificado reduzindo queries da rota `/escala` de 8.5 para 5.0.
- **D2 (`35d4de4`):** Suporte a `DB_POOL=null` para compatibilidade com o pooler em modo transaction do Supabase.
- **D3 (`f4e7eaa`):** Filtro de entradas da escala por equipe no SQL (`WHERE team_id = ?`) evitando carregar todo o workspace.
- **D4 (`dcecfc2`):** `INSERT` em lote com cláusula `RETURNING` eliminando o bloco de releitura com `OR` gigante.
- **D5 (`9f553f0`):** Verificação prévia da tabela `workspaces` antes de invocar `init_db` no middleware.

### Fase 2 — Backend e Payloads
- **D6 (`cd5287c`):** Cálculo de disponibilidade (`busy`) por membro memorizado uma vez por requisição.
- **D7 (`d6a9bf6`):** `_conflicts_for_cells` filtra apenas integrantes da equipe da célula.
- **D8 (`824d547`):** Ações de equipe no frontend atualizam estado localmente sem refazer `refresh()` completo (`GET /teams` + `GET /schedule`).
- **D9 (`c0c98a8`):** Documentação de arquitetura do rate limit em serverless registrada em `docs/achados.md`.

### Fase 3 — Frontend da Escala (`templates/escala.html`)
- **D10 (`e68a784`):** Controle de frequência de arrasto com `requestAnimationFrame`, `CELL_MAP` em memória, limpeza seletiva de classes e prevenção de layout thrashing.
- **D11 (`1806868`):** Pré-agrupamento de `SCHEDULE` por célula (`Map`) em O(1) e chamada única de `manageableTeams()`, reduzindo complexidade para O(células + entradas).
- **D12 (`57f1983`):** Event delegation no container `schedGrid`, eliminando listeners por célula.
- **D13 (`4652259`):** Ações de escala acionam exclusivamente `renderGrid()`, preservando equipes e filtros.
- **D14 (`340c3e5`):** Auditoria técnica do `dashboard.html` registrada em `docs/achados.md`.

### Fase 4 — Extração de PDF
- **D15 (`711dd1c`):** Extração de texto cru (`visitor_text`) restrita à primeira página (onde reside o cabeçalho SIGAA).
- **D16 (`670ca7e`):** Regexes simplificadas com `[\s-]*` e linhas limitadas a 2.000 caracteres; testes adversariais executando em < 10ms.
- **D17 (`e51b41b`):** Validação antecipada de `file.size` e logging estruturado de exceções com `logger.exception`.

### Fase 5 — Configuração, Infraestrutura e Segurança
- **D18 (`624a032`):** README reescrito com foco em Vercel, Supabase, variáveis de ambiente e deploy.
- **D19 (`1b9d269`):** `vercel.json` configurado com região `gru1` (São Paulo) e `maxDuration: 15`.
- **D20:** Startup protegido contra falta de `SESSION_SECRET` em produção.
- **D21 (`8a6a639`):** Análise e planejamento de transição de CSP para nonces/arquivos estáticos em `docs/achados.md`.

### Fase 6 — Qualidade e Manutenção
- **D22 (`dd13f03`):** Execução do pytest otimizada com schema persistente de teste.
- **D23 (`20ba572`):** Eliminação de literais duplicados de horários nos templates, centralizando em `extractor.SLOTS`.
- **D24 (`7e1bdfd`):** Middleware de observabilidade `[PERF]` ativado via `PERF_LOG=1`.
