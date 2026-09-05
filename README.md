# NoTimeToRelax

Grade de disponibilidade semanal da sua equipe, gerada automaticamente a partir de comprovantes de matrícula do SIGAA (UFCG).

Cada membro envia o PDF do seu comprovante, o sistema extrai os horários de aula, e monta um heatmap interativo mostrando quem está livre em cada horário da semana.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | Python + FastAPI |
| Banco | SQLite (via SQLAlchemy) |
| PDF | pdftotext + parser próprio |
| Frontend | HTML/CSS/JS (Jinja2) |
| Deploy | Render.com |

## Uso

1. Acesse a landing page e crie um workspace com o nome da sua equipe
2. Compartilhe o link do workspace com os membros
3. Cada membro envia o PDF do comprovante de matrícula (SIGAA)
4. O heatmap é atualizado automaticamente

## Estrutura

```
notimetorelax/
├── main.py              # FastAPI app (rotas frontend + API)
├── database.py          # SQLAlchemy: Workspace + Member
├── extractor.py         # Parsing de PDF comprovante SIGAA
├── templates/
│   ├── landing.html     # Página inicial: criar/acessar workspace
│   ├── dashboard.html   # Heatmap interativo
│   └── upload.html      # Upload de PDF + preview
├── requirements.txt
└── render.yaml          # Config deploy Render
```

## Login Google

Quando as variáveis abaixo estão configuradas, o site exige login com conta
Google antes de criar/acessar qualquer workspace. Os horários de aula vêm do
PDF do SIGAA e são vinculados à conta do usuário; cada pessoa pode então marcar
horários extras de ocupação (projeto, pesquisa, trabalho) no dashboard.

| Variável | Descrição |
|----------|-----------|
| `GOOGLE_CLIENT_ID` | Client ID do projeto OAuth no Google Cloud |
| `GOOGLE_CLIENT_SECRET` | Client Secret do projeto OAuth |
| `GOOGLE_REDIRECT_URI` | OPCIONAL. Usado se estiver atrás de proxy; default: `https://SEU_DOMINIO/auth/google/callback` |

Redirect URI configurado no Google Cloud: `https://SEU_DOMINIO/auth/google/callback`.

Se `GOOGLE_CLIENT_ID` não estiver definida, o site funciona no modo antigo
(acesso apenas com a senha do workspace).

## API

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/workspace` | Criar workspace |
| GET | `/api/workspace/{slug}/members` | Listar membros |
| POST | `/api/workspace/{slug}/upload?preview=true` | Extrair dados do PDF (sem salvar) |
| POST | `/api/workspace/{slug}/members` | Salvar membro (JSON: nome, curso, busy) |
| PATCH | `/api/workspace/{slug}/members/{id}/extra-busy` | Atualizar horários extras do membro |
| DELETE | `/api/workspace/{slug}/members/{id}` | Remover membro |

## Desenvolvimento

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --loop asyncio
```

## Deploy

1. Crie um repositório no GitHub e faça push do código
2. No Render.com, New Web Service → conecte ao repositório
3. O `render.yaml` já configura tudo automaticamente
