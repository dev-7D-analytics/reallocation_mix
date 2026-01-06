# Diferença entre o modelo canônico de realocação e o mix diário legado

## Resumo
- **Canônico (realocação por classe)**: `modelo_otimizacao_com_realocacao.py`. Produção é informada por classe biológica; SKUs da mesma classe compartilham essa capacidade. O solver decide a alocação por `item_id` (SKU + embalagem) para maximizar margem ou minimizar custo.
- **Mix diário legado**: `modelo_otimizacao_mix_diario.py`. Usa estoque diário por SKU e compatibilidade SKU x embalagem; não há realocação entre SKUs, apenas escolha de embalagem para cada SKU.

## Por que o canônico gera ganho
1. **Capacidade por classe**: a restrição é na soma dos `item_id` da classe; o modelo pode concentrar volume nos SKUs de maior margem.
2. **Diferença de margem entre SKUs**: margens variam (R$1–R$2/un), permitindo ganhos relevantes ao realocar.
3. **Objetivo flexível**: maximiza margem ou, em modo de desova, minimiza custo ainda respeitando classe e pedidos (se habilitados).

## Por que o mix diário entrega pouco ganho
1. **Sem realocação entre SKUs**: cada SKU fica limitado ao próprio estoque; apenas escolhe embalagem.
2. **Margens quase idênticas entre embalagens**: diferenças centesimais tornam o ganho irrelevante.
3. **Dependência de compatibilidade**: exige histórico de vendas/compatibilidade e estoque diário granular.

## Inputs comparados
- **Canônico**: `producao_classe.csv`, `base_skus_classes.xlsx`, preços e custos por `item_id`; pedidos e demanda histórica são opcionais.
- **Mix diário legado**: `manti_estoque.parquet`, compatibilidade SKU x embalagem, preços por embalagem; produção não é agregada por classe.

## Quando usar cada um
- Use **realocação por classe** para otimizar margem/custo de forma estruturante (padrão).
- Use **mix diário** apenas para análises legadas ou quando não houver produção por classe disponível e você precisar testar escolha de embalagem sem realocação.
