# Benchmark — Depois da Fase 4

**Data:** 2026-10-16  
**Ambiente:** SQLite local (dev), Python 3.14  
**Foco:** Extração e Processamento de PDF (`extractor.py`, `main.py`)

## Resumo das melhorias de PDF

| Diagnóstico | Commit | Otimização |
|---|---|---|
| D15 | `perf(D15): limita extracao de texto cru a primeira pagina` | Extração de texto cru (`visitor_text`) restrita à primeira página (onde reside o cabeçalho de discente/curso), evitando duplo parse completo em documentos multipáginas |
| D16 | `perf(D16): simplifica regexes de extracao e limita tamanho de linha` | Substituição de padrões com risco de backtracking catastrófico (`[ -]*\s*`) por `[\s-]*`, truncamento de linha a 2.000 caracteres e teste adversarial incluído na suíte |
| D17 | `perf(D17): valida tamanho de upload antecipadamente e loga excecoes` | Verificação prévia de `file.size` antes de alocar memória do buffer e inclusão de `logger.exception` com stacktrace |

## Critérios de aceite

- [x] Extração de dados comprovada em arquivos reais (`rdm_asas/*.pdf`)
- [x] Testes adversariais executam em menos de 10ms (critério < 1s)
- [x] Falhas de upload logadas com traceback estruturado
