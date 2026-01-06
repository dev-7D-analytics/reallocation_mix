# Campos Necessários (Modelo de Realocação por Classe)

## Produção por Classe (`producao_classe.csv`)
- `Classe_Produto`: nome da classe biológica.
- `quantidade`: produção disponível da classe (> 0).

## Classificação de SKUs (`base_skus_classes.xlsx`)
- `item`: código numérico do SKU (int).
- `Classe_Produto` (ou coluna contendo “classe” e “produto”): classe biológica do SKU.
- SKUs sem classe são tratados como `OUTROS`, mas só entram se a classe existir na produção.

## Pedidos (opcional) (`inputs/pedidos_clientes.csv`)
- `item`: código do SKU (int).
- `quantidade_pedida`: quantidade solicitada (> 0).
- Opcional `cod_cliente` para rastreio.
- O modelo agrega por SKU para limitar `y_pedido_item`.

## Preços por `item_id` (`inputs/precos_sku_embalagem.csv`)
- Formato 1: `item`, `embalagem`, `preco` → cria `item_id = item_embalagem`.
- Formato 2: `item_id`, `preco` (usado diretamente).
- Deve haver `preco > 0`; duplicatas por `item_id` são deduplicadas.

## Custos por `item_id` (`CUSTO ITEM.csv`)
- Coluna de descrição com código no início e embalagem (“CX … BJ … UN”).
- Coluna de custo (`custo_ytd`); aceita formato brasileiro com “R$”.
- Extraídos pelo modelo: `item` (int), `embalagem`, `custo_ytd` > 0, `item_id = item_embalagem`.
- Duplicatas por `item_id` são deduplicadas.

## Demanda Histórica (opcional) (`manti_fat_2024.parquet`)
- Coluna de item (contendo “item” no nome) → SKU.
- Coluna de quantidade (contendo “quantidade” no nome) → vendas.
- Coluna de data/emissão (contendo “emiss” ou “data” no nome) → data da venda.
- Usado somente se `modelo.considerar_demanda_historica=true` para limitar alocação por SKU.

## Saídas
- Geradas automaticamente em `resultados/` (CSV/Excel com detalhamento, resumo por classe e estatísticas).

## Itens legados (não usados pelo modelo canônico)
- Estoque diário (`manti_estoque.parquet`) e compatibilidade SKU x embalagem eram usados no mix diário antigo; não são necessários na versão de realocação por classe.
