"""Precedência de commit/branch: Render > GitHub Actions > "local".

Testa as funções de resolução diretamente, não o endpoint: commit/branch são
lidos do ambiente uma única vez, no import de app.core.version — testar via
HTTP dependeria de qual ambiente o pytest está rodando (ex.: GITHUB_SHA já
vem setado em todo job do Actions), tornando o teste instável entre "rodando
local" e "rodando na CI".
"""

from app.core.version import resolve_branch, resolve_commit


def test_commit_prioriza_render_sobre_github(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdefabcdef")
    monkeypatch.setenv("GITHUB_SHA", "1111111111111")

    assert resolve_commit() == "abcdefa"


def test_commit_cai_para_github_sha_sem_render(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "2222222222222")

    assert resolve_commit() == "2222222"


def test_commit_e_local_sem_nenhuma_variavel(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    assert resolve_commit() == "local"


def test_commit_trata_variavel_vazia_como_ausente(monkeypatch):
    # RENDER_GIT_COMMIT="" (variável existe, mas vazia) não pode virar "" no
    # payload — precisa cair para o próximo candidato, igual a não existir.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "")
    monkeypatch.setenv("GITHUB_SHA", "3333333333333")

    assert resolve_commit() == "3333333"


def test_commit_e_local_com_ambas_variaveis_vazias(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "")
    monkeypatch.setenv("GITHUB_SHA", "")

    assert resolve_commit() == "local"


def test_branch_prioriza_render_sobre_github(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_BRANCH", "master")
    monkeypatch.setenv("GITHUB_REF_NAME", "develop")

    assert resolve_branch() == "master"


def test_branch_cai_para_github_ref_name_sem_render(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_BRANCH", raising=False)
    monkeypatch.setenv("GITHUB_REF_NAME", "develop")

    assert resolve_branch() == "develop"


def test_branch_e_local_sem_nenhuma_variavel(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_BRANCH", raising=False)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)

    assert resolve_branch() == "local"


def test_branch_trata_variavel_vazia_como_ausente(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_BRANCH", "")
    monkeypatch.setenv("GITHUB_REF_NAME", "develop")

    assert resolve_branch() == "develop"


def test_branch_e_local_com_ambas_variaveis_vazias(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_BRANCH", "")
    monkeypatch.setenv("GITHUB_REF_NAME", "")

    assert resolve_branch() == "local"
