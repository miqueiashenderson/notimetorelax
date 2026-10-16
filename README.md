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
│   ├── upload.html      # Upload de PDF + preview
│   └── escala.html      # Escala de trabalho por equipes
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

## Escala de trabalho por equipes

Na página `/workspace/{slug}/escala` (link "Escala de trabalho" no dashboard),
o **criador** do workspace monta a escala semanal de trabalho da construção:

- **Equipes** — grupos formados a partir dos integrantes já cadastrados no
  workspace (um integrante pode estar em mais de uma equipe).
- **Líderes** — o criador marca um integrante (com conta Google vinculada) como
  líder; o líder administra a própria equipe e a escala dela.
- **Escala** — cada célula da grade 14 horários × 7 dias recebe uma equipe, uma
  tarefa/local (texto livre) e um status (`Planejado`, `Em andamento`,
  `Concluído`). Várias atribuições podem ocupar o mesmo horário.
- **Arrastar (estilo Excel)** — arraste o ⠿ de um bloco para **estendê-lo** por
  vários horários de uma vez, ou arraste sobre células vazias para selecionar um
  retângulo e criar um **bloco grande** já aplicado a todos os horários
  selecionados (usa o endpoint `schedule/bulk`).
- **Apagar em lote** — o botão **🗑 Apagar blocos** ativa um modo em que arrastar
  sobre a grade apaga todas as entradas visíveis que o usuário pode gerenciar
  (usa o endpoint `schedule/bulk-delete`). Só são apagadas entradas de equipes
  que o usuário administra e que estejam visíveis no filtro atual.
- **Conflitos** — ao escalar uma equipe, o sistema avisa (⚠) quando algum
  integrante tem aula ou compromisso extra naquele horário, com opção de
  confirmar mesmo assim (não bloqueia).
- **Acesso** — o criador e os líderes editam; os demais integrantes apenas
  visualizam. A escala pode ser exportada em CSV (`schedule.csv`).

## API

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/workspace` | Criar workspace |
| GET | `/api/workspace/{slug}/members` | Listar membros |
| POST | `/api/workspace/{slug}/upload?preview=true` | Extrair dados do PDF (sem salvar) |
| POST | `/api/workspace/{slug}/members` | Salvar membro (JSON: nome, curso, busy) |
| PATCH | `/api/workspace/{slug}/members/{id}/extra-busy` | Atualizar horários extras do membro |
| DELETE | `/api/workspace/{slug}/members/{id}` | Remover membro |
| GET | `/api/workspace/{slug}/teams` | Listar equipes (com integrantes e líderes) |
| POST | `/api/workspace/{slug}/teams` | Criar equipe (criador) |
| PATCH/DELETE | `/api/workspace/{slug}/teams/{id}` | Renomear/apagar equipe (criador) |
| POST/DELETE | `/api/workspace/{slug}/teams/{id}/members[/{member_id}]` | Adicionar/remover integrante (criador ou líder da equipe) |
| PATCH | `/api/workspace/{slug}/teams/{id}/leaders/{member_id}` | Definir/remover líder (criador) |
| GET | `/api/workspace/{slug}/schedule` | Listar escala (com conflitos por entrada) |
| POST | `/api/workspace/{slug}/schedule` | Criar entrada na escala (criador ou líder) |
| POST | `/api/workspace/{slug}/schedule/bulk` | Criar várias entradas de uma vez (arrastar/copiar bloco; `force` para conflitos) |
| POST | `/api/workspace/{slug}/schedule/bulk-delete` | Apagar várias entradas de uma vez (`ids`; só equipes gerenciáveis) |
| PATCH/DELETE | `/api/workspace/{slug}/schedule/{id}` | Editar/apagar entrada (criador ou líder) |
| GET | `/api/workspace/{slug}/schedule.csv` | Exportar a escala em CSV |

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
