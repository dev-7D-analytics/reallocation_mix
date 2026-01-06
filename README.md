# Modelo de Otimização com Realocação entre SKUs (canônico)

Este diretório usa o modelo `modelo_otimizacao_com_realocacao.py` (OR-Tools) como fonte única de verdade. O estoque é informado por **produção agregada por classe**, e SKUs da mesma classe compartilham esse volume, permitindo realocar para quem tem maior margem.

## O que o modelo faz
- Carrega produção por classe (`producao_classe.csv`), mapeia `item -> classe`, preços por `item_id` (SKU + embalagem) e custos por `item_id` (já inclui embalagem).
- Opcional: pedidos por SKU (atende antes de otimizar excedente) e demanda histórica para limitar alocação.
- Cria um modelo linear/misto que impõe capacidade por classe e escolhe alocação por `item_id` maximizando margem ou minimizando custo.
- Exporta CSV/Excel com alocações, resumos e estatísticas.

## Entradas esperadas (veja `config.yaml`)
- `paths.producao`: CSV com `Classe_Produto, quantidade` (produção total por classe).
- `paths.classes`: Excel com `item, Classe_Produto` (classe biológica).
- `paths.pedidos` (opcional): CSV com `item, quantidade_pedida` (agrega por SKU).
- `paths.precos`: CSV com `item, embalagem, preco` **ou** `item_id, preco`. `item_id` = `item` + `embalagem`.
- `paths.custos`: CSV `CUSTO ITEM.csv` com código e embalagem na descrição; o modelo extrai `item`, `embalagem`, `custo_ytd` e monta `item_id`.
- `paths.faturamento` (opcional): Parquet para demanda histórica.
- Saídas em `paths.output_dir` (padrão `resultados/`).

## Como rodar (rápido)
```bash
cd reallocation_mix
python modelo_otimizacao_com_realocacao.py
```
Saídas: `resultados/resultados_realocacao_<modo>_<timestamp>.csv/.xlsx` e resumos por classe.

## Configuração relevante (`config.yaml`)
- Objetivo: `modelo.tipo_objetivo` = `maximizar_margem` (padrão) ou `minimizar_custos`.
- Pedidos e excedente: `modelo.atender_pedidos` (True prioriza pedidos) e `modelo.usar_apenas_excedente` (ajustado automaticamente quando atende pedidos).
- Demanda histórica: `modelo.considerar_demanda_historica`, `granularidade_demanda` (M/S/D), `tipo_calculo_demanda` (`percentil` ou `maximo`) e fatores.
- Solver: `solver.solver_type`, `time_limit_ms`, `num_threads`.
- Caminhos: por padrão apontam para OneDrive (ajuste para seu ambiente).

## Modos suportados
- **Atender pedidos**: cria variáveis `y_pedido_item`, atende até min(pedido, produção da classe); otimiza excedente com `x_item_id`.
- **Ignorar pedidos**: otimiza todo o volume da classe.
- **Maximizar margem**: objetivo = margem pedidos + margem excedente.
- **Minimizar custos**: objetivo = custo pedidos + custo excedente com “recompensa” pequena para evitar solução zero.
- **Demanda histórica (opcional)**: limita `x_item_id` pelo histórico do SKU.

## Estrutura principal
```
modelo_otimizacao_com_realocacao.py   # Modelo canônico
config.yaml                           # Caminhos e parâmetros
testar_modos_operacao.py              # Compara atender vs ignorar pedidos
testar_modo2.py                       # Rodar modo ignorar pedidos
testar_maximo_historico.py            # Teste demanda histórica (máximo)
testar_granularidade_mensal.py        # Teste demanda histórica (mensal)
analisar_potencial_ganho.py           # Resumo rápido do resultado canônico
gerar_producao_classe.py              # Agrega estoque histórico em produção por classe
extrair_precos_embalagem.py           # Gera preços por (item, embalagem) do faturamento
extrair_compatibilidade_embalagem.py  # LEGADO (compatibilidade histórica; não usado no canônico)
```

## Outputs
- CSV detalhado: alocação por `item_id`, receitas/custos/margem, tipo (`PEDIDO`, `EXCEDENTE` ou `ESTOQUE_TOTAL`).
- Excel: abas Detalhado, Resumo por Classe, Estatísticas.
- Resumos adicionais: `analise_potencial_classe.csv`, `analise_potencial_item_id.csv` (via `analisar_potencial_ganho.py`).

## Troubleshooting rápido
- Sem solução: cheque se `producao_classe.csv` tem classes que aparecem em `base_skus_classes.xlsx` e se há preços/custos > 0 para os `item_id`.
- Margem zero/baixa: confirme se há variação de margem dentro da classe; ajuste `tipo_objetivo` ou verifique dados de preço/custo.
- Demanda histórica bloqueando: desative `modelo.considerar_demanda_historica` ou revise percentil/fatores.

## Diferenciação vs modelos legados
- Canônico: `modelo_otimizacao_com_realocacao.py` (produção por classe, realocação entre SKUs da classe, item_id inclui embalagem).
- Legado mix diário: `modelo_otimizacao_mix_diario.py` (estoque diário por SKU e compatibilidade/embalagem) – manter apenas para histórico; use o canônico.
