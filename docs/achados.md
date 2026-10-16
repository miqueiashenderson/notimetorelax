# Achados e Decisões Técnicas — NoTimeToRelax

Este documento registra problemas identificados fora do roteiro ou decisões de produto/infraestrutura que exigem alinhamento.

## D9 — Rate limit em memória no serverless (Fase 2)
- **Problema:** A função `_rate_limit` em `main.py` armazena tentativas em um dicionário em memória do processo (`_RL`). No ambiente serverless (Vercel), cada invocação/instância tem seu próprio estado efêmero e volátil, tornando o rate limit ineficaz contra ataques distribuídos ou instâncias concorrentes.
- **Opções de solução:**
  1. Tabela dedicada no PostgreSQL com contadores por IP/chave e expiração (`ON CONFLICT DO UPDATE`).
  2. Serviço gerenciado de cache/chave-valor serverless (Upstash Redis via REST API / SDK).
  3. Proteção no edge (Cloudflare WAF / Vercel Edge Middleware / Rate Limiting rules).
- **Decisão:** Manter limitação documentada para deploy serverless; implementar camada persistente (Redis ou tabela Postgres) conforme demanda de produção do projeto.
