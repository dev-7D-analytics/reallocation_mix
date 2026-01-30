# Fluxo do Modelo de Realocação por Classe

## Visão Geral

O modelo otimiza a alocação de produção entre SKUs da mesma classe de produtos, maximizando a margem total. A unidade de otimização é o **ovo** (não a caixa), priorizando SKUs com maior margem por ovo produzido.

## Arquivos de Entrada

| Arquivo | Descrição |
|---------|-----------|
| `PRODUÇÃO DIA.xlsx` | Produção semanal bruta por SKU (aba CE0302) |
| `producao_classe.csv` | Produção agregada por classe de produtos |
| `base_skus_classes.xlsx` | Mapeamento SKU para classe |
| `precos_sku_embalagem.csv` | Preços por item_id (SKU + embalagem) |
| `CUSTO ITEM.csv` ou `.parquet` | Custos por SKU |
| `skus_restritos.xlsx` | SKUs permitidos para produção no estabelecimento (filtro A interseção B) |
| `manti_fat_*.parquet` | Faturamento histórico para demanda (opcional) |

## Fluxo de Execução

```
1. Carregar Dados
   - Produção por classe
   - Mapeamento SKU -> Classe
   - Preços e custos por item_id
   - SKUs permitidos (estabelecimento)
   - Demanda histórica (se habilitada)

2. Preparar Base de Otimização
   - Filtrar SKUs: apenas os que aparecem na produção E estão na lista de permitidos (A interseção B)
   - Calcular margem unitária (preço - custo) por caixa
   - Calcular margem por ovo (margem_unitaria / ovos_por_caixa)

3. Criar Modelo OR-Tools
   - Variáveis: x[item_id] = quantidade em ovos a alocar
   - Restrições:
     - Soma por classe <= produção disponível da classe
     - Por SKU <= limite de demanda histórica (se habilitado)

4. Função Objetivo
   - Maximizar: soma(margem_unitaria * quantidade_caixas)
   - Equivale a: soma(margem_por_ovo * quantidade_ovos)

5. Resolver e Extrair Resultados
```

## Configurações Principais (config.yaml)

| Parâmetro | Descrição |
|-----------|-----------|
| `considerar_demanda_historica` | Habilita restrição de demanda por SKU |
| `fator_percentual_maximo` | Fator aplicado ao máximo histórico (ex: 1.2 = 120%) |
| `filtrar_granjas` | Estabelecimentos a manter no cálculo de demanda |
| `tipo_objetivo` | `maximizar_margem` ou `minimizar_custos` |

## Arquivos de Saída

### Modelo Principal
- `resultado_realocacao_completo_<timestamp>.csv`
- `resultado_realocacao_completo_<timestamp>.xlsx` (abas: Detalhado, Resumo, Estatísticas)
- `resumo_por_classe_completo_<timestamp>.csv`

### Comparativo (pós-processamento)
- `comparacao_producao_alocacao_<semana>.xlsx`
- `pedidos_ignorados_<semana>.xlsx`

## Colunas do Output

| Coluna | Descrição |
|--------|-----------|
| `item` | Código do SKU |
| `embalagem` | Tipo de embalagem (ex: CX 12 BJ 20 UN) |
| `item_id` | Identificador único (item_embalagem) |
| `classe` | Classe do produto |
| `quantidade` | Quantidade alocada em ovos |
| `quantidade_caixas` | Quantidade alocada em caixas |
| `margem_unitaria` | Margem por caixa (R$/caixa) |
| `margem_por_ovo` | Margem por ovo (R$/ovo) - métrica otimizada |
| `margem_total` | Margem total da alocação (R$) |
| `limite_demanda_historica` | Limite calculado de demanda (se habilitado) |
| `tem_demanda_historica` | Flag indicando se SKU tem histórico |
| `sku_restrito` | Flag indicando se SKU não está na lista de permitidos |

## Lógica de Otimização

O modelo prioriza SKUs com maior **margem por ovo**, não maior margem por caixa. Isso ocorre porque:

1. O recurso escasso é a produção de ovos (limitada)
2. Embalagens menores (ex: 60 ovos) geram mais caixas por ovo
3. Mesmo com margem/caixa menor, podem gerar mais margem total

Exemplo:
- SKU A: R\$ 62/caixa, 240 ovos/caixa = R\$ 0,26/ovo
- SKU B: R\$ 58/caixa, 60 ovos/caixa = R\$ 0,97/ovo
- O modelo prioriza SKU B (maior margem por ovo)
