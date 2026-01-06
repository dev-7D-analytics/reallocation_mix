# Fluxo do Modelo Canônico (Realocação por Classe)

## Visão rápida
- **Config** (`config.yaml`) → caminhos e flags.
- **Inputs** → produção por classe, classes, preços por `item_id`, custos por `item_id`; opcionais: pedidos por SKU, demanda histórica.
- **Preparação** → monta base de otimização por `item_id` com margem e produção disponível por classe.
- **Modelo OR-Tools** → variáveis `y_pedido_item` (se atender pedidos) e `x_item_id` (alocação). Restrições de capacidade por classe e, opcionalmente, demanda histórica.
- **Objetivo** → maximizar margem ou minimizar custo.
- **Outputs** → CSV/Excel detalhado + resumos/estatísticas.

## Diagrama simplificado
```mermaid
flowchart TD
    A[Config YAML] --> B[Carregar Dados]
    B --> B1[Produção por Classe]
    B --> B2[Classes SKU->Classe]
    B --> B3[Pedidos por SKU (opcional)]
    B --> B4[Preços por item_id]
    B --> B5[Custos por item_id]
    B --> B6[Demanda Histórica (opcional)]
    B1 & B2 & B3 & B4 & B5 & B6 --> C[Preparar Base por item_id]
    C --> D[Criar Modelo OR-Tools]
    D --> E[Objetivo (Margem ou Custo)]
    D --> F[Restrições: pedidos (se houver), capacidade por classe, demanda hist (opcional)]
    E --> G[Resolver]
    F --> G
    G --> H[Extrair Resultados]
    H --> I[CSV/Excel + Estatísticas]
```

## Entradas detalhadas
- `producao_classe.csv`: `Classe_Produto, quantidade`.
- `base_skus_classes.xlsx`: `item, Classe_Produto`.
- `precos_sku_embalagem.csv`: `item, embalagem, preco` ou `item_id, preco`.
- `CUSTO ITEM.csv`: descrição com código + embalagem e coluna de custo (`custo_ytd`); o modelo gera `item_id`.
- Pedidos (opcional): `item, quantidade_pedida`.
- Faturamento (opcional): Parquet para demanda histórica.

## Saídas
- `resultados/resultados_realocacao_<modo>_<timestamp>.csv/.xlsx` (detalhado + abas Resumo/Estatísticas).
- Resumos auxiliares: `analise_potencial_classe.csv`, `analise_potencial_item_id.csv` (via `analisar_potencial_ganho.py`).

## Notas operacionais
- Capacidade é sempre por **classe**; é aqui que a realocação acontece.
- `atender_pedidos=true` força uso do excedente (produção – pedidos) por classe.
- Demanda histórica limita `x_item_id` por SKU; se não usada, o limite é só a produção da classe.
