# Flow API

Backend do Flow — **FastAPI** + **Postgres do Supabase**. Autentica pelo access
token (JWT) do Supabase enviado pelo front (`Authorization: Bearer <token>`).

## Stack
- FastAPI + Uvicorn
- SQLAlchemy 2.0 (async) + asyncpg
- PyJWT (validação do token do Supabase)
- Pydantic v2 / pydantic-settings

## Estrutura
```
flow-api/
├── app/
│   ├── main.py            # app FastAPI + CORS + rotas
│   ├── core/
│   │   ├── config.py      # settings via .env
│   │   ├── db.py          # engine/sessão async + Base
│   │   └── security.py    # valida JWT do Supabase → CurrentUser
│   ├── deps.py            # dependência: carteira do usuário
│   ├── models.py          # carteiras, transacoes, proventos
│   ├── schemas.py         # entrada/saída (Pydantic)
│   └── routers/           # health, carteiras, transacoes, proventos, posicoes
├── alembic/versions/      # migrations — fonte oficial do schema
├── tests/                 # unitários (motores) + integração (Postgres real)
├── requirements.txt
└── .env.example
```

## Rodando localmente
```bash
cd flow-api
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # preencha DATABASE_URL e SUPABASE_JWT_SECRET

uvicorn app.main:app --reload --port 8000
```

- Docs interativas: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Onde pegar as credenciais (Supabase)
- `DATABASE_URL`: Settings → Database → Connection string (use a direta, porta5432) e troque `postgresql://` por `postgresql+asyncpg://`.
- `SUPABASE_JWT_SECRET`: Settings → API → JWT Secret.

## Banco de dados

**O Alembic é a fonte oficial do schema.** As migrations em `alembic/versions/`
descrevem a estrutura; `app/models.py` deve refleti-las.

```bash
alembic upgrade head          # aplica as migrations pendentes
alembic upgrade head --sql    # só mostra o SQL, sem conectar
alembic current               # em que revisão o banco está
alembic revision --autogenerate -m "descrição"   # gera a partir dos modelos
```

A URL vem de `DATABASE_URL` (a mesma da aplicação), não do `alembic.ini` — então
o Alembic age sobre o banco que a variável apontar. Confira antes de rodar contra
produção.

### Dependências do Supabase

**FK `carteiras.user_id → auth.users(id) ON DELETE CASCADE`** — criada pela
migration `0004`, que é **condicional**: só age onde a tabela `auth.users`
existe. Num Postgres limpo (o CI) ela não faz nada, porque o `create_all` do
harness não tem como criar o schema `auth`.

Duas consequências que valem saber:

- **O CI não prova essa FK.** Lá a migration apenas se abstém. A verificação é
  por introspecção depois de aplicar em produção.
- **A FK não está em `app/models.py`**, pelo mesmo motivo — o `create_all` a
  criaria e falharia. `alembic/env.py` protege essa divergência com o filtro
  `include_object`: sem ele, o próximo `--autogenerate` proporia removê-la.
  Ao adicionar outro objeto que exista só no Supabase, inclua-o naquele filtro.

### Histórico

Havia um `sql/schema.sql` que o README mandava rodar em produção. A introspecção
do banco real mostrou que **ele nunca foi executado**: as tabelas nasceram do
`create_all` (via `DEV_CREATE_TABLES`), e os índices têm os nomes gerados pelo
SQLAlchemy (`ix_carteiras_user_id`), não os do arquivo (`carteiras_user_id_idx`).

O arquivo foi removido: descrevia constraints que não existem (FK para
`auth.users`, CHECK em `operacao`, defaults de servidor), duplicava o que a
migration `0001` já faz, e rodá-lo agora criaria índices duplicados. As garantias
que ele prometia continuam ausentes do banco e estão registradas acima.

## Endpoints (v0.1)
| Método | Rota                                        | Descrição                     |
|--------|---------------------------------------------|-------------------------------|
| GET    | `/health`                                   | status + ping no banco        |
| GET    | `/carteiras`                                | lista carteiras do usuário    |
| POST   | `/carteiras`                                | cria carteira                 |
| GET    | `/carteiras/{id}`                           | detalhe                       |
| DELETE | `/carteiras/{id}`                           | remove                        |
| GET    | `/carteiras/{id}/transacoes`                | lista compras/vendas          |
| POST   | `/carteiras/{id}/transacoes`                | registra compra/venda         |
| GET    | `/carteiras/{id}/proventos`                 | lista proventos               |
| POST   | `/carteiras/{id}/proventos`                 | registra provento             |
| GET    | `/carteiras/{id}/posicoes`                  | posições consolidadas (PM)    |

Todas as rotas (exceto `/health`) exigem o header
`Authorization: Bearer <access_token do Supabase>`.

## Próximos passos
- CHECK em `transacoes.operacao`: hoje o banco aceita qualquer texto, e o motor
  de posição trata o que não for `compra` como venda. Quem barra é só o Pydantic.
- FK para `auth.users` (ver acima).
- `/carteiras/ativa` cria a carteira padrão sem lock nem constraint: duas
  chamadas concorrentes podem criar duas.
