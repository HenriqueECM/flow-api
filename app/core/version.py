"""Versão do backend: fonte única, usada por `app.main` e pelo endpoint `/version`.

Um lugar só evita que a versão declarada na app FastAPI e a exposta em runtime
divirjam por alguém atualizar uma string e esquecer da outra.
"""

VERSION = "0.1.0"
