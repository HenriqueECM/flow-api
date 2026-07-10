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
├── sql/schema.sql         # schema p/ rodar no Supabase (produção)
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
- Relatórios: rentabilidade mensal, YoC, comparativo de índices.
- Cotações atuais (integração externa) para valor de mercado/variação.
- Migrations com Alembic (hoje o dev usa `DEV_CREATE_TABLES`).
- Importação por planilha (parse do Excel).
