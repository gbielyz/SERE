# Deploy no Render

Este projeto ja inclui `render.yaml`, `Procfile` e `runtime.txt`.

## Passo a passo

1. Suba o repositorio para o GitHub.
2. Entre em [Render](https://render.com).
3. Clique em `New +` e escolha `Blueprint`.
4. Selecione o repositorio do SERE.
5. Confirme o servico `sere-demo`.
6. Configure as variaveis secretas:
   - `SERE_ADMIN_PASSWORD`
   - `SERE_PROFESSOR_PASSWORD`
   - `SERE_ALUNO_PASSWORD`
7. Faça o deploy.

## URL esperada

Algo como:

```text
https://sere-demo.onrender.com
```

## Observacao importante

No plano gratuito, o Render pode hibernar o servico apos um tempo sem uso. A primeira abertura depois disso pode demorar alguns segundos.

## Banco

O `render.yaml` configura um disco persistente em `/var/data` e usa:

```text
SERE_DB_PATH=/var/data/sere.db
```

Para uma demonstracao, isso basta. Para producao real, o proximo passo e migrar para PostgreSQL.
