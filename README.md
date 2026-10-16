# NoTimeToRelax

Grade de disponibilidade semanal da sua equipe, gerada automaticamente a partir de comprovantes de matrícula do SIGAA (UFCG).

Cada membro envia o PDF do seu comprovante, o sistema extrai os horários de aula, e monta um heatmap interativo mostrando quem está livre em cada horário da semana. Além disso, conta com módulo completo de escala de trabalho por equipes com edição estilo planilha (arrastar/soltar e operações em lote).

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12+ / FastAPI / Uvicorn |
| ORM / Banco | SQLAlchemy (PostgreSQL / Supabase em produção; SQLite em dev/testes) |
| PDF | pypdf (extração de fluxos de texto com suporte a letter-spacing SIGAA) |
| Frontend | Jinja2 + HTML5 / CSS3 / Vanilla JavaScript |
| Deploy / Infra | Vercel Serverless (`vercel.json`) + Supabase (Connection Pooler modo transaction) |

## Uso

1. Acesse a landing page e crie um workspace com o nome da sua equipe.
2. Compartilhe o link do workspace com os membros.
3. Cada membro envia o PDF do comprovante de matrícula (SIGAA).
4. O heatmap e a escala de trabalho são atualizados imediatamente.

## Estrutura do Projeto

```
notimetorelax/
├── main.py              # Aplicação FastAPI, middlewares, endpoints e rotas de API
├── database.py          # Modelos SQLAlchemy (Workspace, Member, Team, ScheduleEntry)
├── extractor.py         # Parsing robusto de PDF do comprovante SIGAA
├── templates/
│   ├── landing.html     # Página inicial (criar / acessar workspace)
│   ├── dashboard.html   # Heatmap de disponibilidade
│   ├── upload.html      # Upload de PDF e pré-visualização
│   └── escala.html      # Escala de trabalho por equipes (grade interativa)
├── static/              # Ativos estáticos
├── tools/
│   ├── benchmark.py     # Benchmark automatizado de latência e contagem de queries
│   └── query_counter.py # Contador de queries SQL via eventos do SQLAlchemy
├── tests/               # Suíte de testes com pytest (>210 testes)
├── vercel.json          # Configuração de build, região e runtime no Vercel
└── requirements.txt     # Dependências Python do projeto
```

## Variáveis de Ambiente

Configure um arquivo `.env` na raiz do projeto para desenvolvimento local ou defina no painel do Vercel/Supabase:

| Variável | Obrigatória | Padrão / Exemplo | Observação |
|---|---|---|---|
| `DATABASE_URL` | Sim em produção | `postgresql://postgres.[ref]:[pass]@aws-0-[region].pooler.supabase.com:6543/postgres` | Utilize o pooler do Supabase (porta 6543, modo transaction). Se ausente em dev, utiliza SQLite local em `data/horariolivre.db`. |
| `DB_POOL` | Não | `queue` (dev) / `null` (serverless) | Em serverless (Vercel), utilize `null` para desabilitar pool local de conexões e delegar ao pooler do Supabase. |
| `SESSION_SECRET` | Sim em produção | String secreta (32+ bytes) | Chave HMAC para assinatura de cookies de sessão. Em modo `DEBUG=1`, gera um segredo efêmero automático; em produção sem segredo, recusa inicialização por segurança. |
| `DEBUG` | Não | `0` (produção) / `1` (dev) | Ativa recarregamento e mensagens detalhadas. Mantenha desativado em produção. |
| `GOOGLE_CLIENT_ID` | Sim para auth | `xxx.apps.googleusercontent.com` | OAuth 2.0 Client ID do Google Cloud Console. |
| `GOOGLE_CLIENT_SECRET` | Sim para auth | `GOCSPX-xxx` | OAuth 2.0 Client Secret do Google Cloud Console. |
| `GOOGLE_REDIRECT_URI` | Opcional | `https://seu-dominio.vercel.app/auth/google/callback` | Necessário se a aplicação estiver atrás de proxy reverso/CDN com terminação SSL externa. |

## Login Google

Quando as credenciais Google estão configuradas, o sistema exige login com conta Google para criar ou gerenciar workspaces e vincular integrantes. Os horários de aula extraídos do SIGAA são vinculados ao perfil do usuário, permitindo registrar compromissos extras e sincronizar disponibilidade entre diferentes equipes.

## Escala de Trabalho por Equipes

Na rota `/workspace/{slug}/escala`, o criador e os líderes do workspace organizam as atividades:
- **Equipes e Líderes:** Grupos criados com integrantes do workspace; o proprietário pode delegar liderança de equipes.
- **Edição em Grade (Arrastar / Bloco):** Arraste células vazias para alocar blocos de horários ou arraste o marcador `⠿` de uma entrada existente para estendê-la.
- **Apagar em Lote:** Modo de apagar (`🗑 Apagar blocos`) permite seleção por arrasto para remoção em massa.
- **Detecção de Conflitos:** Ao escalar um horário, o sistema sinaliza (⚠) se algum membro da equipe possui aula ou atividade conflitante, permitindo confirmação expressa (`force`).

## Desenvolvimento Local

```bash
# 1. Clonar e preparar ambiente virtual
git clone https://github.com/miqueiashenderson/notimetorelax.git
cd notimetorelax
python3 -m venv venv
source venv/bin/activate

# 2. Instalar dependências
pip install -r requirements.txt pytest

# 3. Rodar os testes
pytest -q

# 4. Iniciar servidor de desenvolvimento
DEBUG=1 uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

## Deploy (Vercel + Supabase)

1. Crie um projeto no **Supabase** (preferencialmente região `sa-east-1` São Paulo).
2. Obtenha a URL de conexão do **Connection Pooler** (modo Transaction, porta `6543`).
3. Importe o repositório no **Vercel**.
4. Configure as variáveis de ambiente no Vercel:
   - `DATABASE_URL`: URL do connection pooler (porta 6543).
   - `DB_POOL`: `null`.
   - `SESSION_SECRET`: Chave aleatória gerada com `openssl rand -hex 32`.
   - `GOOGLE_CLIENT_ID` e `GOOGLE_CLIENT_SECRET`.
5. O arquivo `vercel.json` já direciona as rotas para o runtime Python na região `gru1` (São Paulo).
