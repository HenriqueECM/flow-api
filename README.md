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
├── Dockerfile             # imagem da API
├── .dockerignore          # allowlist do contexto de build
└── requirements.txt
```

## Variáveis de ambiente

`Settings` é instanciado no **import** de `app.core.config`. Faltando uma
obrigatória, o processo morre antes do Uvicorn subir — num container isso aparece
como "deploy failed" com um `ValidationError` no meio do log, sem nenhuma
requisição atendida.

| Variável | Obrigatória | Default | Para que serve |
|---|---|---|---|
| `DATABASE_URL` | **sim** | — | Postgres. Precisa do driver: `postgresql+asyncpg://…` |
| `SUPABASE_JWKS_URL` | **sim** | — | chaves públicas que validam o JWT (ES256) |
| `SUPABASE_JWT_SECRET` | não | vazio | fallback HS256, só para sessões legadas |
| `JWT_AUDIENCE` | não | `authenticated` | `aud` esperado no token |
| `CORS_ORIGINS` | não | `http://localhost:3000` | origens liberadas, separadas por vírgula |
| `BRAPI_TOKEN` | não | vazio | cotações da B3. Sem ele a app sobe; as cotações é que ficam nulas |
| `DEV_CREATE_TABLES` | não | `false` | **deixe `false`.** Ver abaixo |
| `PORT` | não | `8000` | porta do Uvicorn. Render/Railway/Fly injetam |

**Sobre `DEV_CREATE_TABLES`:** com `true`, o `lifespan` roda `Base.metadata.create_all`
a cada boot. Isso **contorna o Alembic** e desconhece objetos que só existem no
banco (como a FK para `auth.users`, criada pela migration `0004`). Nunca ligue em
ambiente com dados.

### Onde pegar as credenciais (Supabase)
- `DATABASE_URL`: Settings → Database → Connection string (use a direta, porta
  5432) e troque `postgresql://` por `postgresql+asyncpg://`. Se a senha tiver
  caracteres percent-encoded (`%40` para `@`), mantenha como está — o Alembic já
  trata isso.
- `SUPABASE_JWKS_URL`: `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`.
- `SUPABASE_JWT_SECRET`: Settings → API → JWT Secret.

## Rodando localmente

### Sem Docker
```bash
python -m venv .venv
source .venv/Scripts/activate    # Windows (Git Bash)
# source .venv/bin/activate      # Linux/macOS

pip install -r requirements.txt

export DATABASE_URL="postgresql+asyncpg://usuario:senha@host:5432/postgres"
export SUPABASE_JWKS_URL="https://<ref>.supabase.co/auth/v1/.well-known/jwks.json"

uvicorn app.main:app --reload --port 8000
```

- Docs interativas: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Com Docker

O job `docker` do CI faz este mesmo build e sobe o container com esta mesma
topologia de rede a cada push — então o que está aqui é caminho verificado, não
receita improvisada. As flags diferem um pouco: o CI roda em segundo plano para
poder inspecionar; abaixo o container fica em primeiro plano, que é mais útil
para desenvolver.

```bash
docker build -t flow-api .
```

Para rodar, a API precisa de um Postgres. Uma rede própria resolve o detalhe que
mais confunde: **`localhost` dentro do container é o próprio container**, então
apontar `DATABASE_URL` para `localhost` não alcança um Postgres que esteja no
host.

```bash
docker network create flow-local

docker run -d --name pg --network flow-local \
  -e POSTGRES_USER=flow -e POSTGRES_PASSWORD=flow -e POSTGRES_DB=flow_dev \
  postgres:16-alpine

docker run --rm --name api --network flow-local -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://flow:flow@pg:5432/flow_dev" \
  -e SUPABASE_JWKS_URL="https://<ref>.supabase.co/auth/v1/.well-known/jwks.json" \
  flow-api
```

O banco sobe vazio: aplique as migrations antes de usar os endpoints. (`/health`
funciona sem elas, porque só faz `SELECT 1` — é por isso que ele serve de smoke
test no CI.) O `docker exec` herda as variáveis do `docker run`, então não é
preciso repetir a `DATABASE_URL`:

```bash
docker exec api alembic upgrade head
```

Para apontar o container ao **Supabase** em vez do Postgres local, troque a
`DATABASE_URL` e dispense a rede:

```bash
docker run --rm -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://postgres:senha@db.<ref>.supabase.co:5432/postgres" \
  -e SUPABASE_JWKS_URL="https://<ref>.supabase.co/auth/v1/.well-known/jwks.json" \
  flow-api
```

Limpeza:
```bash
docker rm -f pg api && docker network rm flow-local
```

**Notas da imagem** — o processo roda como `flow` (não-root, UID 1000); a porta
vem de `$PORT` e cai para 8000; um worker por container, porque escalar é
responsabilidade da plataforma e cada processo abriria seu próprio pool contra o
Postgres.

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
