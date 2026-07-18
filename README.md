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
funciona sem elas, e sem banco algum, porque é liveness puro — não toca no
Postgres. É por isso que ele serve de smoke test de liveness no CI; a
conectividade com o banco é validada à parte, por `/health/ready`.) O
`docker exec` herda as variáveis do `docker run`, então não é preciso repetir a
`DATABASE_URL`:

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

## Deploy contínuo (CI/CD)

O deploy é orquestrado pelo **GitHub Actions**, não pelo Render. O `render.yaml`
mantém `autoDeploy: false` de propósito: quem decide a ordem é o pipeline. Um
merge em `master` dispara a cadeia (todos os jobs de produção são *gated* a
`master` + `push` — em PR e em `develop` eles não rodam):

```
PR/push  →  build  →  docker  →  migrations           (qualidade; rodam sempre)
merge em master  →  migrate-prod  →  deploy  →  health-check   (só master + push)
```

- **build** — ruff, black, pytest com cobertura.
- **docker** — constrói a imagem e faz smoke de `/health` e `/health/ready`.
- **migrations** — aplica o ciclo `upgrade/downgrade/upgrade` num Postgres limpo;
  prova que as migrations criam, desfazem e recriam o schema.
- **migrate-prod** — roda `alembic upgrade head` contra o Supabase. **Forward-only:
  nunca faz downgrade.** `needs: [build, docker, migrations]`.
- **deploy** — dispara o Deploy Hook do Render. `needs: [migrate-prod]`, então é
  estruturalmente impossível deployar sem as migrations terem passado.
- **health-check** — faz poll de `/health` e `/health/ready` públicos, com
  orçamento generoso para o build da imagem e o cold start do plano free.

### Secrets e variáveis

No **GitHub** (Settings → Secrets and variables → Actions):

| Nome | Tipo | Para que serve |
|---|---|---|
| `PROD_DATABASE_URL` | secret | `alembic upgrade head` do `migrate-prod` contra o Supabase (pooler session mode, `postgresql+asyncpg://…`) |
| `RENDER_DEPLOY_HOOK_URL` | secret | URL que o `deploy` chama por `POST` para disparar o Render |
| `PROD_BASE_URL` | variável | URL pública do serviço, usada pelo `health-check`. Não é segredo |

No **Render** (painel do serviço, `sync: false` no Blueprint): `DATABASE_URL`,
`SUPABASE_JWKS_URL`, `SUPABASE_JWT_SECRET`, `BRAPI_TOKEN`, `CORS_ORIGINS`.
`DEV_CREATE_TABLES` já vem fixado em `false`.

Enquanto esses valores não existem, os jobs de produção ficam **inertes**: falham
por valor vazio, sem tocar em nada. Não marque `migrate-prod`/`deploy`/
`health-check` como *required checks* — eles são pulados em PR, e um check
obrigatório que nunca roda em PR trava o merge.

### Migrations retrocompatíveis (expand/contract)

Como as migrations rodam **antes** do deploy, existe uma janela em que o **código
antigo serve contra o schema já migrado**. Por isso toda migration precisa ser
retrocompatível: adicionar coluna *nullable*, tabela ou constraint `NOT VALID` é
seguro; remover/renomear coluna ou adicionar `NOT NULL` sem default quebra o
código antigo nessa janela. Mudança destrutiva tem que ser fatiada em duas
releases (primeiro o código para de usar, depois a migration remove). **O CI não
detecta isso — é responsabilidade da revisão de PR.**

### Rollback

Não há reversão automática: uma falha de deploy após a migration **não** desfaz
nada. A recuperação é manual e em duas frentes independentes:

- **Código** — no painel do Render, *Manual Deploy* → selecionar o deploy
  anterior (ou fazer *Rollback*). Como `autoDeploy` é `false`, isso é sempre uma
  ação deliberada.
- **Schema** — `alembic downgrade <revision>` a partir do container ou de uma
  máquina autorizada, apontando `DATABASE_URL` para o Supabase. Graças ao
  expand/contract, na maioria dos casos basta voltar o código e **deixar o schema
  como está** — o código antigo tolera o schema novo. Só faça downgrade quando a
  própria migration precisar ser revertida.

## Observabilidade

Logs estruturados em **JSON**, um por linha, para stdout — é o formato que o
Render e um log drain (Better Stack/Logtail) indexam e tornam pesquisável.
Texto solto não dá para filtrar por campo.

Campos principais de cada linha:

| Campo | Descrição |
|---|---|
| `level` | severidade (`INFO`, `ERROR`, ...) |
| `logger` | nome do logger (`flow.access`, `flow.error`, ...) |
| `message` | mensagem legível |
| `request_id` | identificador da requisição, para correlacionar todas as linhas de um mesmo request |
| `method` | verbo HTTP (nas linhas de access log e de erro) |
| `path` | caminho da rota (idem) |
| `status` | status code da resposta (access log) |
| `duration_ms` | duração da requisição em milissegundos (access log) |

Cada requisição recebe um `request_id`, propagado para todo log emitido durante
o seu processamento — inclusive logs de módulos internos (`flow.brapi`,
`flow.posicoes`). É esse campo que liga o log de erro à linha de access log da
mesma requisição, mesmo com múltiplas requisições concorrentes intercaladas no
stdout.

Fluxo típico de investigação:

```
Usuário reporta erro
        |
        v
Identificar request_id
   (no X-Request-ID retornado ao front, ou no relato do usuário)
        |
        v
Buscar logs no Render/log drain
   filtrando por request_id
        |
        v
Correlacionar endpoint e erro
   (method + path do access log, exc do log de erro)
```

### `request_id`

- **Header de entrada:** `X-Request-ID`. Se o chamador enviar um, ele é reaproveitado —
  útil para correlacionar com um id gerado a montante (front, gateway).
- **Header de saída:** toda resposta devolve `X-Request-ID`, com o valor usado
  nesta requisição.
- **Caso não exista:** a API gera um UUID automaticamente.

Os health checks (`/health`, `/health/ready`) recebem `X-Request-ID` normalmente,
mas não geram linha de access log — evita inundar os logs com o poll constante
do Render.

### Tratamento de erros

Exceções não tratadas por qualquer rota caem no handler global e viram uma
resposta padronizada:

```json
{
  "detail": "Erro interno."
}
```

O detalhe da exceção (mensagem, traceback) **não** é exposto ao cliente — fica
só no log de erro (`flow.error`), junto com `request_id`, `method` e `path` da
requisição que falhou.

Já existe um ponto preparado para integração futura com o Sentry
(`capture_exception` em `app/core/observability.py`): hoje é *no-op*, chamado a
cada exceção não tratada; quando o SDK entrar como dependência, a captura liga
ali, sem mexer no restante do fluxo.

### Próximos passos de observabilidade

- **Sentry** para alertas de exceção em tempo real (o ponto de integração já
  existe, ver acima).
- **Better Stack/log drain** para busca e análise dos logs em produção.
- **Métricas de negócio** (ex.: carteiras criadas, transações registradas por
  dia) além dos logs técnicos atuais.

## Endpoints (v0.1)
| Método | Rota                                        | Descrição                     |
|--------|---------------------------------------------|-------------------------------|
| GET    | `/health`                                   | liveness (não toca no banco)  |
| GET    | `/health/ready`                             | readiness: 200 se o banco responde, 503 se não |
| GET    | `/carteiras`                                | lista carteiras do usuário    |
| POST   | `/carteiras`                                | cria carteira                 |
| GET    | `/carteiras/{id}`                           | detalhe                       |
| DELETE | `/carteiras/{id}`                           | remove                        |
| GET    | `/carteiras/{id}/transacoes`                | lista compras/vendas          |
| POST   | `/carteiras/{id}/transacoes`                | registra compra/venda         |
| GET    | `/carteiras/{id}/proventos`                 | lista proventos               |
| POST   | `/carteiras/{id}/proventos`                 | registra provento             |
| GET    | `/carteiras/{id}/posicoes`                  | posições consolidadas (PM)    |

Todas as rotas (exceto `/health` e `/health/ready`) exigem o header
`Authorization: Bearer <access_token do Supabase>`.

## Próximos passos
- CHECK em `transacoes.operacao`: hoje o banco aceita qualquer texto, e o motor
  de posição trata o que não for `compra` como venda. Quem barra é só o Pydantic.
- FK para `auth.users` (ver acima).
- `/carteiras/ativa` cria a carteira padrão sem lock nem constraint: duas
  chamadas concorrentes podem criar duas.
