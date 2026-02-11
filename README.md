# Modelo de Otimizacao de Mix - Realocacao entre SKUs

Modelo de programacao linear (OR-Tools) que maximiza a margem de contribuicao
realocando volume de producao entre SKUs da mesma classe biologica de ovos.

## O que o modelo faz

1. **Pre-processamento (ETL)**: carrega producao por classe, classificacao de SKUs, precos, custos, pedidos de clientes e demanda historica.
2. **Otimizacao**: formula e resolve um modelo linear/misto que aloca volume por `item_id` (SKU + embalagem) maximizando margem, respeitando capacidade por classe e limites de demanda historica.
3. **Pos-processamento**: compara resultado otimizado vs baseline (distribuicao proporcional ao historico), gera auditoria detalhada e relatorios.

## Arquitetura

```
realocacao-git/
├── config.yaml                          # Configuracao central (parametros, paths)
├── main.py                              # Orquestrador: ETL -> Otimizacao -> Output
├── executar_pipeline.sh                 # Shell script para rodar pipeline completo
├── requirements.txt                     # Dependencias Python
│
├── etl/
│   ├── __init__.py
│   └── pipeline.py                      # Pre-processamento: carrega e prepara dados
│
├── modelo/
│   ├── __init__.py
│   └── otimizador.py                    # Solver: formulacao e resolucao do modelo
│
├── output/
│   ├── __init__.py
│   ├── comparativo.py                   # Comparativo baseline vs otimizado + auditoria
│   └── resultados.py                    # Salvamento de resultados (CSV/Excel)
│
├── gerar_pedidos_clientes.py            # Gera pedidos_clientes.csv
├── gerar_producao_classe.py             # Gera producao_classe.csv
├── comparar_producao_alocacao.py        # Relatorio: producao real vs alocacao do modelo
├── extrair_compatibilidade_embalagem.py # Extrai compatibilidade SKU-embalagem do faturamento
├── extrair_precos_embalagem.py          # Extrai precos por embalagem
│
├── docs/
│   └── formulacao_modelo.tex            # Formulacao matematica (LaTeX)
│
├── inputs/                              # Bases de dados (nao versionadas)
└── resultados/                          # Outputs gerados (nao versionados)
```

## Fluxo de dependencias

Quando uma base de dados e atualizada, os scripts abaixo precisam ser re-executados
na ordem indicada:

```
[Bases brutas]
     │
     ├─ manti_fat_*.parquet ──► extrair_compatibilidade_embalagem.py
     │                              │
     │                              ▼
     │                          extrair_precos_embalagem.py ──► inputs/precos_sku_embalagem.csv
     │                              │
     ├─ manti_fat_*.parquet ──► gerar_pedidos_clientes.py ───► inputs/pedidos_clientes.csv
     │   skus_restritos.xlsx        │
     │   ESTAB CORRIGIDO.xlsx       │
     │                              │
     ├─ PRODUÇÃO DIA.xlsx ────► gerar_producao_classe.py ───► inputs/producao_classe.csv
     │   base_skus_classes.xlsx     │
     │                              │
     └──────────────────────────► main.py (ETL + Otimização + Output)
                                    │
                                    ▼
                                comparar_producao_alocacao.py (relatório final)
```

### Mapa de impacto: base atualizada -> scripts a re-executar

| Base de dados atualizada | Scripts a re-executar (na ordem) |
|---|---|
| `manti_fat_*.parquet` (faturamento) | `extrair_compatibilidade_embalagem.py` -> `extrair_precos_embalagem.py` -> `gerar_pedidos_clientes.py` -> `gerar_producao_classe.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `PRODUCAO DIA.xlsx` (producao diaria) | `gerar_producao_classe.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `MANTI-PRIC_Custos_*.parquet` (custos) | `extrair_precos_embalagem.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `base_skus_classes.xlsx` (classificacao) | `gerar_producao_classe.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `skus_restritos.xlsx` (SKUs permitidos) | `gerar_pedidos_clientes.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `ESTAB CORRIGIDO.xlsx` (estab. corrigido) | `gerar_pedidos_clientes.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `config.yaml` (parametros) | `main.py` -> `comparar_producao_alocacao.py` (e geradores de input se janela/granularidade mudaram) |

## Entradas esperadas (configuradas em `config.yaml`)

| Parametro | Arquivo | Descricao |
|---|---|---|
| `paths.producao` | `inputs/producao_classe.csv` | Producao total por classe (gerado por `gerar_producao_classe.py`) |
| `paths.classes` | `inputs/base_skus_classes.xlsx` | Mapeamento item -> classe biologica |
| `paths.pedidos` | `inputs/pedidos_clientes.csv` | Pedidos por SKU (gerado por `gerar_pedidos_clientes.py`) |
| `paths.precos` | `inputs/precos_sku_embalagem.csv` | Precos por item_id (gerado por `extrair_precos_embalagem.py`) |
| `paths.custos` | `inputs/MANTI-PRIC_Custos_*.parquet` | Custos por item (PRIC) |
| `paths.faturamento` | `inputs/manti_fat_*.parquet` | Faturamento historico (para demanda e enriquecimento) |
| `paths.producao_bruta` | `inputs/PRODUCAO DIA.xlsx` | Producao diaria bruta (aba CE0302) |
| `paths.skus_restritos` | `inputs/skus_restritos.xlsx` | Filtro de SKUs ativos/permitidos |
| `paths.estab_corrigido` | `inputs/ESTAB CORRIGIDO.xlsx` | Correcao de estabelecimento |

## Como rodar

```bash
# Pipeline completo (gera inputs + otimiza + relatorio)
./executar_pipeline.sh

# Ou passo a passo:
python extrair_compatibilidade_embalagem.py
python extrair_precos_embalagem.py
python gerar_pedidos_clientes.py
python gerar_producao_classe.py
python main.py
python comparar_producao_alocacao.py
```

## Outputs gerados

| Arquivo | Descricao |
|---|---|
| `resultado_realocacao_completo_*.csv` | Alocacao detalhada por item_id |
| `resultado_realocacao_completo_*.xlsx` | Excel com abas: Detalhado, Resumo por Classe, Estatisticas, Pedidos Ignorados |
| `auditoria_baseline_*.xlsx` | Auditoria: Detalhe por SKU, Resumo por Classe, Parametros |
| `demanda_historica_*.xlsx` | Limites de demanda historica calculados |
| `comparacao_producao_alocacao_*.xlsx` | Comparacao producao real vs alocacao do modelo |

## Configuracao relevante (`config.yaml`)

- **Objetivo**: `modelo.tipo_objetivo` = `maximizar_margem` (padrao)
- **Pedidos**: `modelo.atender_pedidos` (True prioriza pedidos garantidos)
- **Cap de reserva**: `modelo.capar_reserva_na_producao` (True limita reservas a producao disponivel, priorizando por margem)
- **Demanda historica**: `modelo.considerar_demanda_historica`, `granularidade_demanda` (M/S/D), `tipo_calculo_demanda` (`percentil`, `maximo`, `media`)
- **Solver**: `solver.solver_type`, `time_limit_ms`, `num_threads`

## Autor

Romulo Brito
