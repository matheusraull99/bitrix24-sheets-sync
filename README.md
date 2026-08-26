# bitrix24-sheets-sync

Sincroniza o CRM do Bitrix24 com uma planilha do Google **nos dois sentidos**,
sem perder alteração — porque compara campo a campo contra uma linha de base,
não "quem salvou por último".

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Testes](https://img.shields.io/badge/testes-23%20passando-brightgreen)
![Licença](https://img.shields.io/badge/licença-MIT-lightgrey)

---

## O problema

Sempre existe alguém que prefere a planilha. Não adianta lutar: a operação
roda no Sheets, o oficial é o CRM, e os dois divergem em uma semana.

A tentação é sincronizar com *last-write-wins* — comparar timestamps e
escolher o lado mais recente. Isso **perde dado em silêncio**:

```
10:00  vendedor atualiza o telefone no CRM
10:05  analista corrige o valor na planilha
11:00  sincronização escolhe um lado inteiro
       → uma das duas alterações desaparece, e ninguém é avisado
```

---

## Como resolve: merge de três vias

Guardando o estado da última sincronização bem-sucedida (a **linha de base**),
dá para saber *o que mudou de qual lado* em vez de só *qual está mais novo*:

| | telefone | valor |
|---|---|---|
| **base** (última sync) | `1111` | `100` |
| **CRM** agora | `2222` ← mudou | `100` |
| **planilha** agora | `1111` | `250` ← mudou |
| **resultado** | `2222` | `250` |

Nenhum conflito: cada lado mexeu num campo diferente, e as duas alterações
entram. Conflito de verdade é só quando **os dois lados mudaram o mesmo
campo** — e aí a política é sua:

```bash
--vencedor crm        # CRM ganha (padrão)
--vencedor planilha   # planilha ganha
--vencedor manual     # ninguém ganha: reporta e sai com código 1
```

No modo `manual` o registro **não entra na nova linha de base**. Se entrasse,
o conflito sumiria na próxima execução sem ninguém ter resolvido nada.

---

## Uso

```bash
pip install -e ".[dev]"
cp .env.example .env
```

`mapa.json` define o contrato entre os dois lados:

```json
{
  "entidade": "crm.deal",
  "chave_crm": "ID",
  "coluna_chave": "ID",
  "campos": {
    "TITLE": "Negocio",
    "OPPORTUNITY": "Valor",
    "STAGE_ID": "Etapa"
  }
}
```

```bash
sync-sheets --mapa mapa.json                        # simula
sync-sheets --mapa mapa.json --vencedor manual --executar
```

Saída:

```
SIMULACAO (use --executar)
4 mudancas | 1 conflitos
  1042: atualizar_planilha (TITLE)
  1105: atualizar_crm (OPPORTUNITY)
  1200: criar_no_crm (TITLE, OPPORTUNITY, STAGE_ID)
  1301: CONFLITO — OPPORTUNITY: CRM='18000' vs planilha='21000'
```

A planilha precisa ser compartilhada com o e-mail da **conta de serviço**.

---

## Decisões técnicas

**Conta de serviço, não OAuth de usuário.** O robô roda de madrugada, sem
ninguém para clicar em "permitir" quando o refresh token expira.

**Vazio é vazio.** `None`, `""` e `"  "` normalizam para a mesma coisa. Sem
isso, uma célula em branco e um campo nulo apareceriam como diferença em toda
execução, gerando escrita infinita entre os dois lados.

**Registro que sumiu de um lado não é apagado do outro.** Apagar em cascata é
destrutivo demais para um robô decidir sozinho — e a causa mais comum é
alguém ter filtrado a planilha, não ter excluído o registro.

**A aba é regravada inteira.** Trocar N chamadas de célula por uma só evita o
descompasso de índice quando alguém insere uma linha no meio enquanto o robô
roda.

**Célula vazia no fim da linha é completada na leitura.** A API do Sheets
omite as últimas células vazias. Sem completar, `zip()` truncaria e a última
coluna simplesmente sumiria do registro — falha silenciosa clássica.

**Linha de base corrompida é erro fatal, não recuperável.** Sem ela, tudo
vira "registro novo" e o robô pode duplicar a planilha inteira. A mensagem
diz explicitamente para apagar o arquivo de propósito se a intenção for
recomeçar.

---

## Testes

```bash
pytest -q
```

23 testes sobre o merge puro. O mais importante é o de **idempotência**:
aplicar o plano e recalcular precisa devolver zero ações. Sync que não é
idempotente vira ping-pong de escrita entre os dois lados e só é descoberto
quando a cota da API acaba.

## Licença

MIT.
