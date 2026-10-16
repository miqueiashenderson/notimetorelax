# Benchmark — Depois da Fase 5

**Data:** 2026-10-16  
**Ambiente:** Vercel / Supabase Config & Segurança  
**Foco:** Infraestrutura, Configurações e Segurança

## Resumo das melhorias de Configuração e Infraestrutura

| Diagnóstico | Commit | Otimização |
|---|---|---|
| D18 | `docs(D18): atualiza README com stack Vercel, Supabase e variaveis de ambiente` | README reescrito com instruções completas de deploy, tabela de variáveis e guia de desenvolvimento local |
| D19 | `perf(D19): define regiao e maxDuration da funcao no vercel.json` | Configurada região `gru1` (São Paulo, alinhada ao Supabase `sa-east-1`) e `maxDuration` de 15s |
| D20 | *Verificado* | Startup seguro: lança `RuntimeError` em produção caso `SESSION_SECRET` não esteja configurada |
| D21 | `docs(D21): documenta analise de CSP e plano de migracao para nonces` | Análise de CSP e plano de transição para scripts sem unsafe-inline registrado em `docs/achados.md` |

## Critérios de aceite

- [x] README permite setup local e deploy completo
- [x] Região do Vercel alinhada com banco de dados
- [x] Aplicação não sobe com segredo previsível em produção
- [x] Plano de migração de CSP registrado
