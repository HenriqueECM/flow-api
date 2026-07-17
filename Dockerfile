# Imagem da API. Sem nada específico de plataforma: a porta vem de $PORT (Render,
# Railway, Fly) e cai para 8000 quando ninguém a define (Compose, docker run).

FROM python:3.11-slim

# PYTHONUNBUFFERED é o que mais importa aqui: sem ele, o stdout do Python fica
# com buffer de bloco quando não há TTY — os logs aparecem atrasados e, se o
# processo morrer, a última mensagem (justamente a do erro) se perde. É a causa
# de "o container caiu e o log não diz nada".
#
# PYTHONDONTWRITEBYTECODE evita .pyc no container: o código não muda em runtime,
# então o cache só ocuparia espaço em camada.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# /code, e não /app: o pacote da aplicação já se chama `app`, e usar o mesmo nome
# nos dois lugares torna `/app/app/main.py` confuso de ler.
WORKDIR /code

# Criado antes do COPY para o --chown funcionar. UID fixo porque o padrão do
# `useradd` varia entre distros, e volumes montados dependem dele para casar as
# permissões.
RUN useradd --create-home --uid 1000 flow

# Dependências antes do código: esta camada só é reconstruída quando o
# requirements.txt muda. Sem esta ordem, cada alteração em app/ reinstalaria
# pandas e numpy — a diferença entre um build de segundos e um de minutos.
#
# Sem apt-get: nenhuma dependência precisa de biblioteca de sistema. O asyncpg
# implementa o protocolo do Postgres em vez de usar libpq, e cryptography,
# pandas e numpy trazem o que precisam nos próprios wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# O Alembic vai junto para permitir `alembic current`/`upgrade` manuais contra o
# banco a partir do container, mesmo com o upgrade rodando fora dele.
COPY --chown=flow:flow alembic.ini ./
COPY --chown=flow:flow alembic/ ./alembic/
COPY --chown=flow:flow app/ ./app/

USER flow

# Documental: o Render e afins ignoram e roteiam pela $PORT que injetam. Serve
# para `docker run -P` e para quem lê o arquivo.
EXPOSE 8000

# Shell form (sem colchetes) porque $PORT precisa ser expandida em runtime — a
# exec form passaria a string literal "$PORT" e o bind falharia.
#
# O `exec` não é enfeite: sem ele, o /bin/sh continua como PID 1 e o uvicorn vira
# filho. O SIGTERM do "parando o container" iria para o sh, que não o repassa —
# o uvicorn nunca encerraria as conexões, e a plataforma o mataria no timeout. Com
# o `exec`, o uvicorn substitui o sh e recebe o sinal.
#
# Um worker: escalar é responsabilidade da plataforma. Vários processos aqui
# brigariam pela CPU do container e cada um abriria seu próprio pool contra o
# Postgres, multiplicando conexões contra um limite que não é nosso.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
