"""Versão do backend: fonte única, usada por `app.main` e pelo endpoint `/version`.

Um lugar só evita que a versão declarada na app FastAPI e a exposta em runtime
divirjam por alguém atualizar uma string e esquecer da outra.

`COMMIT`/`BRANCH` também são resolvidos aqui, uma vez no import — variáveis de
ambiente de deploy não mudam durante a vida do processo:

- `RENDER_GIT_COMMIT`/`RENDER_GIT_BRANCH` — o Render injeta as duas em todo
  deploy, sem nada a configurar em render.yaml. Fonte de verdade em produção.
- `GITHUB_SHA`/`GITHUB_REF_NAME` — presentes em todo job do Actions; cobre o
  smoke do job `docker` da CI, que nunca passa pelo Render.
- `"local"` — fora dos dois casos acima (dev na máquina, testes).

Não existe injeção via `--build-arg`/Dockerfile aqui de propósito: a imagem
que a CI builda nunca é a que o Render deploya (dois builds independentes do
mesmo Dockerfile, sem registry entre eles) — um SHA "gravado" no build da CI
não provaria nada sobre o que está de fato rodando em produção.
"""

import os

VERSION = "0.1.2"


def _resolve(*env_vars: str) -> str:
    for nome in env_vars:
        valor = os.environ.get(nome)
        if valor:
            return valor
    return "local"


def resolve_commit() -> str:
    """Commit em execução, como SHA curto — igual à convenção do próprio git."""
    valor = _resolve("RENDER_GIT_COMMIT", "GITHUB_SHA")
    return valor[:7] if valor != "local" else valor


def resolve_branch() -> str:
    """Branch em execução."""
    return _resolve("RENDER_GIT_BRANCH", "GITHUB_REF_NAME")


COMMIT = resolve_commit()
BRANCH = resolve_branch()
