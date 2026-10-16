# Achados e Decisões Técnicas — NoTimeToRelax

Este documento registra problemas identificados fora do roteiro ou decisões de produto/infraestrutura que exigem alinhamento.

## D9 — Rate limit em memória no serverless (Fase 2)
- **Problema:** A função `_rate_limit` em `main.py` armazena tentativas em um dicionário em memória do processo (`_RL`). No ambiente serverless (Vercel), cada invocação/instância tem seu próprio estado efêmero e volátil, tornando o rate limit ineficaz contra ataques distribuídos ou instâncias concorrentes.
- **Opções de solução:**
  1. Tabela dedicada no PostgreSQL com contadores por IP/chave e expiração (`ON CONFLICT DO UPDATE`).
  2. Serviço gerenciado de cache/chave-valor serverless (Upstash Redis via REST API / SDK).
  3. Proteção no edge (Cloudflare WAF / Vercel Edge Middleware / Rate Limiting rules).
- **Decisão:** Manter limitação documentada para deploy serverless; implementar camada persistente (Redis ou tabela Postgres) conforme demanda de produção do projeto.

## D14 — Verificação e Checklist do `templates/dashboard.html` (Fase 3)
- **Achado 1 — Layout thrashing em hover de célula:** `positionTooltip(e)` lê `tooltip.getBoundingClientRect()` em cada evento `mousemove`, forçando reflows síncronos repetitivos.
- **Achado 2 — O(células × membros) no renderGrid:** Para cada uma das 98 células (7 dias × 14 slots), `pool.filter(m => !isBusy(m,d,s))` e `pool.filter(m => isBusy(m,d,s))` realizam varreduras completas no array de membros ativos (~3000 chamadas a `isBusy` por renderização).
- **Achado 3 — Listeners diretos por célula sem delegação:** `td.onmouseenter`, `td.onmousemove`, `td.onmouseleave` e `td.onclick` são recriados e atrelados individualmente a cada `<td>` em vez de utilizar delegação de eventos no elemento `<table>`.
- **Achado 4 — Reconstrução total da grade no modo de edição:** Ao alternar um horário livre/ocupado no modo de edição individual (`draftExtra`/`draftRemoved`), `renderGrid()` reconstrói a tabela HTML inteira em vez de atualizar apenas as classes/estilos da célula clicada.
- **Recomendação para versão futura:** Aplicar agrupamento prévio de disponibilidade em `Map` e delegação de eventos em `#grid`, replicando as otimizações implementadas na escala.

## D21 — CSP e 'unsafe-inline' em scripts (Fase 5)
- **Problema:** O middleware de segurança em `main.py` define `script-src 'self' 'unsafe-inline'`. Isso ocorre porque os templates Jinja2 (`dashboard.html`, `escala.html`, `upload.html`) contêm scripts embutidos com variáveis injetadas via interpolação de template.
- **Plano de migração:**
  1. Extrair a lógica JavaScript para arquivos modulares em `static/js/` (`escala.js`, `dashboard.js`).
  2. Passar dados iniciais do servidor via tags `<script type="application/json" id="server-data">` (seguras e inertes para execução direta).
  3. Substituir `'unsafe-inline'` por CSP estrito (`script-src 'self'`) ou por nonces gerados por requisição (`script-src 'nonce-{token}'`).
- **Decisão:** Planejado para refatoração de frontend dedicada, preservando compatibilidade imediata e evitando quebras de script em navegadores sem teste ponta-a-ponta.

## D19 (correção) — Falha de deploy: conflito `builds` × `functions` no `vercel.json`
- **Sintoma:** O deploy no Vercel falhava na validação da configuração, antes do build.
- **Causa raiz:** O D19 adicionou as chaves `regions` e `functions` ao `vercel.json` que ainda continha a chave legada `builds`. O Vercel **não permite** usar `functions` e `builds` ao mesmo tempo ("Conflicting functions and builds configuration"). A configuração original (commit `a6ae19e`) já usava `builds` + `routes` e funcionava; a introdução de `functions` quebrou o deploy.
- **Correção:** Removidas as chaves legadas `builds` e `routes`. O runtime Python do Vercel detecta automaticamente aplicações FastAPI (arquivo `main.py` com variável `app` + `fastapi` em `requirements.txt`) e roteia todas as requisições para a aplicação. Mantidas `regions: ["gru1"]` e `functions.main.py.maxDuration: 15`.
- **Referência:** https://vercel.com/docs/errors/error-list#conflicting-functions-and-builds-configuration
- **Alternativa conservadora:** caso a detecção automática do framework não ocorra, restaurar `builds` + `routes` e remover apenas `functions`, configurando região/duração pela interface do projeto no dashboard.

## D5 (correção) — `_ensure_db` pulava migrações e tabelas novas
- **Sintoma:** Ao abrir `/workspace/{slug}/escala` em produção (Supabase) aparecia a tela "Erro interno".
- **Causa raiz:** A otimização do D5 (`_ensure_db` só rodava `init_db()` se a tabela `workspaces` não existisse) impedia a criação de tabelas introduzidas depois. Com o recurso de co-donos, a tabela `workspace_admins` passou a ser consultada por `_admins_payload` na rota da escala; em bancos já existentes ela nunca era criada → `relation "workspace_admins" does not exist` → 500. Os testes não detectavam porque o `conftest.py` cria todas as tabelas (`Base.metadata.create_all`) antes da suíte.
- **Correção:** `_ensure_db()` volta a chamar `init_db()` uma vez por processo. A operação é idempotente (`create_all` só cria tabelas ausentes; `_migrate` só adiciona colunas/índices faltantes) e em bancos atualizados executa apenas introspecção.
- **Teste de regressão:** `tests/test_ensure_db.py` simula um banco legado sem `workspace_admins` e garante que a tabela seja criada.
- **Correção manual imediata (Supabase SQL Editor):**
```sql
CREATE TABLE workspace_admins (
  id SERIAL NOT NULL,
  workspace_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  created_at VARCHAR NOT NULL,
  PRIMARY KEY (id),
  CONSTRAINT uq_workspace_admin UNIQUE (workspace_id, user_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
  FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_workspace_admins_user_id ON workspace_admins (user_id);
CREATE INDEX ix_workspace_admins_workspace_id ON workspace_admins (workspace_id);
```

## Co-donos — painel não renderizava (dropdown de candidatos vazio)
- **Sintoma:** Na escala, a seção "Administradores do workspace" não mostrava os candidatos a co-dono (dropdown vazio / sem opções), impossibilitando promover alguém.
- **Causa raiz:** `renderAll()` em `templates/escala.html` chamava apenas `renderTeams()`, `renderFilters()` e `renderGrid()`. A função `renderAdmins()` (que monta os chips e popula o `<select id="adminPick">`) só era invocada dentro de `promoteAdmin()`/`demoteAdmin()` — nunca na carga da página. Assim o painel nunca era preenchido.
- **Correção:** `renderAll()` passa a chamar `renderAdmins()`.
- **Teste de regressão:** `tests/test_escala_template.py` verifica que `renderAll()` chama `renderAdmins()` e que os elementos do painel existem no template.
- **Observação:** o dropdown lista apenas integrantes **com conta vinculada** (`user_id` não nulo), ou seja, que já entraram com Google/upload no workspace. Integrantes adicionados só por nome não aparecem — comportamento intencional.
