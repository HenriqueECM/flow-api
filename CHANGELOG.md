# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
e este projeto adere a [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

## [0.1.0] - 2026-07-18

Primeira versão oficial do backend Flow API.

### Adicionado

- Autenticação via JWT do Supabase: chaves assimétricas (ES256) através de JWKS,
  com fallback HS256 para sessões legadas.
- Gestão de carteiras: criação, listagem, detalhe e remoção.
- Registro de transações (compras e vendas) por carteira.
- Registro de proventos por carteira.
- Posições consolidadas por carteira, com preço médio (PM) calculado a partir
  do histórico de transações.
- Relatório de Yield on Cost (YoC) por ativo e consolidado da carteira, com
  filtros por ticker e por período.
- Importação de transações a partir de arquivo, com fluxo de pré-visualização,
  revalidação e confirmação antes de gravar no banco.
- Cotações de ativos da B3 via integração com a brapi.dev.
- Schema de banco de dados gerenciado por Alembic, incluindo a FK condicional
  entre carteiras e `auth.users` do Supabase.
- Health checks de liveness (`/health`) e readiness (`/health/ready`).
- Logging estruturado em JSON, com `request_id` de correlação propagado a
  todos os logs de uma mesma requisição.
- Handler global de exceções: respostas de erro padronizadas ao cliente, com
  detalhes preservados apenas nos logs.
- Pipeline de CI/CD via GitHub Actions: lint e testes, build e smoke da imagem
  Docker, validação do ciclo de migrations, aplicação de migrations em
  produção, deploy no Render e verificação de saúde pós-deploy.

### Alterado

### Descontinuado

### Removido

### Corrigido

### Segurança
