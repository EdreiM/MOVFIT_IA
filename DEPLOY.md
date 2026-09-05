# Deploy em produção — checklist

Passo a passo pra colocar a Mônica no ar em `200.6.142.5`, usando as imagens
do Docker Hub (`edreimp/movfit-ia-backend`, `edreimp/movfit-ia-frontend`).

## 1. Preparar o banco (antes de subir o backend pela primeira vez)

A ordem importa — se o backend rodar primeiro, o seed automático cria uma
empresa/admin novos, e restaurar o dump depois disso duplica dado.

```bash
# No seu PC — exporta o banco local
docker exec movfitia-db-1 pg_dump -U monica -d monica --no-owner --clean --if-exists > movfit_dump.sql

# Copia pro servidor
scp movfit_dump.sql usuario@200.6.142.5:/caminho/no/servidor/

# No servidor — sobe só o banco primeiro
docker compose -f docker-compose.prod.yml up -d db

# Espera ficar "healthy", depois restaura
docker exec -i <nome-do-container-db> psql -U monica -d monica < movfit_dump.sql

# Só agora sobe o resto
docker compose -f docker-compose.prod.yml up -d
```

## 2. Trocar a senha do admin

```bash
TOKEN=$(curl -s -X POST http://200.6.142.5:8001/auth/login -H "Content-Type: application/json" -d '{"email":"admin@movfit.com","password":"admin123"}' | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>console.log(JSON.parse(d).access_token))")
USER_ID=$(curl -s http://200.6.142.5:8001/auth/me -H "Authorization: Bearer $TOKEN" | node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>console.log(JSON.parse(d).id))")
curl -X PATCH http://200.6.142.5:8001/users/$USER_ID -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"password":"SUA-SENHA-NOVA-FORTE-AQUI"}'
```

Ou, mais simples: acesse o painel em `http://200.6.142.5:5174/account` e troque pela tela "Minha conta".

## 3. Reenviar as imagens de planos

Qualquer imagem de plano enviada em dev ficou com URL `localhost:8000` —
quebrada em produção. No Catálogo, apague a imagem de cada plano afetado e
suba de novo — o novo upload já gera a URL certa (`200.6.142.5:8001`)
automaticamente.

## 4. Configurar backup automático

```bash
chmod +x scripts/backup-db.sh
```

No crontab do servidor (`crontab -e`):
```
0 3 * * * /caminho/completo/para/scripts/backup-db.sh >> /var/log/movfit-backup.log 2>&1
```

## 5. Atualizar as integrações já configuradas

Toda integração criada em dev tem uma URL de inbound com um segredo
(`/webhooks/inbound/{id}/{segredo}`) que só existe nesse ambiente. Depois do
deploy, recrie a integração em produção (ou copie os dados manualmente) e
atualize a URL no sistema externo (ex: MOVFIT) com o novo segredo — a URL de
dev não funciona lá.

## 6. Se um deploy quebrar algo, como voltar atrás

Toda imagem buildada pela Action fica com 2 tags: `:latest` e o hash do
commit (`:SHA`). Pra voltar pra uma versão anterior:

1. Descubra o SHA do commit bom (`git log`).
2. No `docker-compose.prod.yml`, troque `edreimp/movfit-ia-backend:latest`
   (e/ou o frontend) por `edreimp/movfit-ia-backend:SHA-DO-COMMIT`.
3. `docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d`

## 7. Pendências que não são "código"

- **Webhook `enviar_imagens_planos`** (n8n): o workflow `seletor_imagens_planos`
  ainda responde `{"imagem_enviada": "true"}` em vez de
  `{"sucesso": true, "mensagem": "...", "dados": {}}` — ajustar o nó de
  resposta final desse workflow (ver `n8n/TOOLS-CONTRATO.md`).
