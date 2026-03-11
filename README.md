# Modelo de Otimização de Mix - Realocação entre SKUs

Modelo de programação linear (OR-Tools) que maximiza a margem de contribuição
realocando volume de produção entre SKUs da mesma classe biológica de ovos.

## O que o modelo faz

1. **Pré-processamento (ETL)**: carrega produção por classe, classificação de SKUs, preços, custos, pedidos de clientes e demanda histórica.
2. **Otimização**: formula e resolve um modelo linear/misto que aloca volume por `item_id` (SKU + embalagem) maximizando margem, respeitando capacidade por classe e limites de demanda histórica.
3. **Pós-processamento**: compara resultado otimizado vs baseline (distribuição proporcional ao histórico), gera auditoria detalhada e relatórios.

## Arquitetura

```
realocacao-git/
├── config.yaml                          # Configuração central (parâmetros, paths)
├── main.py                              # Orquestrador: ETL -> Otimização -> Output -> DOW -> Comparação -> PBI
├── executar_pipeline.sh                 # Shell script para rodar pipeline completo (Linux/Mac)
├── executar_pipeline.bat                # Batch script para rodar pipeline completo (Windows)
├── requirements.txt                     # Dependências Python
│
├── etl/
│   ├── __init__.py
│   └── pipeline.py                      # Pré-processamento: carrega e prepara dados
│
├── modelo/
│   ├── __init__.py
│   └── otimizador.py                    # Solver: formulação e resolução do modelo
│
├── output/
│   ├── __init__.py
│   ├── comparativo.py                   # Comparativo baseline vs otimizado + auditoria
│   ├── distribuicao_historica_dow.py    # Distribuição diária DOW (semanal)
│   ├── gerar_consolidado_pbi.py         # Consolidação final PBI (CSV + XLSX)
│   └── resultados.py                    # Salvamento de resultados (CSV/Excel)
│
├── gerar_pedidos_clientes.py            # Gera pedidos_clientes.csv a partir da CARTEIRA_VENDIDA.xlsx
├── gerar_producao_classe.py             # Gera producao_classe.csv (editável)
├── gerar_custos_sku.py                  # Gera custos_sku.csv (editável)
├── gerar_demanda_historica.py           # Gera demanda_historica.csv (editável)
├── comparar_producao_alocacao.py        # Relatório: produção real vs alocação do modelo
├── extrair_compatibilidade_embalagem.py # Extrai compatibilidade SKU-embalagem do faturamento
├── extrair_precos_embalagem.py          # Extrai preços por embalagem (editável)
│
├── docs/
│   └── formulacao_modelo.tex            # Formulação matemática (LaTeX)
│
├── inputs/                              # Bases de dados (não versionadas)
└── resultados/                          # Outputs gerados (não versionados, por rodada em subpastas timestamp)
```

## Fluxo de dependências

Quando uma base de dados é atualizada, os scripts abaixo precisam ser re-executados
na ordem indicada:

```
[Bases brutas]
     │
     ├─ manti_fat_*.parquet ──► extrair_compatibilidade_embalagem.py
     │                              │
     │                              ▼
     │                          extrair_precos_embalagem.py ──► inputs/precos_sku_embalagem.csv (editável)
     │
     ├─ MANTI-PRIC_Custos_* ──► gerar_custos_sku.py ──────► inputs/custos_sku.csv   (editável)
     │
     ├─ manti_fat_*.parquet ──► gerar_demanda_historica.py ► inputs/demanda_historica.csv (editável)
     │   ESTAB CORRIGIDO.xlsx
     │
     ├─ CARTEIRA_VENDIDA.xlsx ► gerar_pedidos_clientes.py ─► inputs/pedidos_clientes.csv (editável)
     │   skus_restritos.xlsx
     │
     ├─ PRODUÇÃO DIA.xlsx ────► gerar_producao_classe.py ──► inputs/producao_classe.csv (editável)
     │   base_skus_classes.xlsx
     │
     └──────────────────────────► main.py (ETL + Otimização + Output + Comparação + PBI)
```

### Inputs editáveis pelo usuário

Após a geração automática, os seguintes CSVs podem ser editados manualmente antes de rodar `main.py`:

| Arquivo | Colunas mínimas | O que o usuário pode fazer |
|---|---|---|
| `inputs/precos_sku_embalagem.csv` | `item`, `preco` | Alterar preço de um SKU, adicionar SKU novo |
| `inputs/custos_sku.csv` | `item`, `custo_ytd` | Alterar custo de um SKU, adicionar SKU novo |
| `inputs/demanda_historica.csv` | `item`, `demanda_max` | Alterar limite de demanda, adicionar/remover SKU |
| `inputs/pedidos_clientes.csv` | `item`, `quantidade`, `preco_pedido`, `data_entrega_min/max` | Alterar volume ou preço de pedido, ajustar datas |

Os CSVs já saem com os cálculos aplicados (ex: fator multiplicativo na demanda, média ponderada no custo). O modelo lê diretamente desses arquivos.

### Atenção: o que pode ser sobrescrito

Os scripts de preparação (passos 1-6) **reescrevem** arquivos em `inputs/`. Em especial:

- `extrair_precos_embalagem.py` reescreve `inputs/precos_sku_embalagem.csv`
- `gerar_custos_sku.py` reescreve `inputs/custos_sku.csv`
- `gerar_demanda_historica.py` reescreve `inputs/demanda_historica.csv`
- `gerar_pedidos_clientes.py` reescreve `inputs/pedidos_clientes.csv`
- `gerar_producao_classe.py` reescreve `inputs/producao_classe.csv`

Se você editou manualmente algum desses CSVs e quer preservar as mudanças, **não rode os passos 1-6**.

### Override manual de embalagem (fallback)

Além da extração automática por regex, o pipeline suporta fallback manual via:

- `inputs/embalagens_override.xlsx` (aba `override`, editável)

Uso esperado:

1. O script tenta extrair embalagem automaticamente da descrição.
2. Se não encontrar, consulta o `embalagens_override.xlsx`.
3. Se houver cadastro válido para o SKU, usa a embalagem do override.
4. Se não houver, permanece como não capturado (aparece na auditoria).

Campos mínimos para cadastro manual seguro (aba `override`):

- **Obrigatórios para o usuário**: `item`, `embalagem`
- **Opcionais**: `ativo`, `observacao`
- **Preenchidos/derivados pelo sistema**: `descricao`, `descricao_normalizada`, `qtd_embalagem`, `origem`, `updated_at`

Schema da aba `override`:

- `item` (int)
- `descricao` (str)
- `descricao_normalizada` (str)
- `embalagem` (str, formato canônico ex.: `CX 12 BJ 30 UN`)
- `qtd_embalagem` (int)
- `ativo` (bool)
- `origem` (str: `manual` ou `auto_seed`)
- `observacao` (str, opcional)
- `updated_at` (datetime)

Observações:

- O arquivo é atualizado sem apagar cadastros existentes.
- Novos padrões capturados automaticamente podem ser adicionados como `auto_seed`.
- O arquivo contém abas auxiliares de auditoria e inconsistências para suporte operacional.

### Mapa de impacto: base atualizada -> scripts a re-executar

| Base de dados atualizada | Scripts a re-executar (na ordem) |
|---|---|
| `manti_fat_*.parquet` (faturamento) | `extrair_compatibilidade_embalagem.py` -> `extrair_precos_embalagem.py` -> `gerar_demanda_historica.py` -> `main.py` |
| `CARTEIRA_VENDIDA.xlsx` (carteira vendida) | `gerar_pedidos_clientes.py` -> `main.py` |
| `PRODUÇÃO DIA.xlsx` (produção diária) | `gerar_producao_classe.py` -> `main.py` |
| `MANTI-PRIC_Custos_*.parquet` (custos) | `gerar_custos_sku.py` -> `main.py` |
| `base_skus_classes.xlsx` (classificação) | `gerar_producao_classe.py` -> `main.py` |
| `skus_restritos.xlsx` (SKUs permitidos) | `gerar_pedidos_clientes.py` -> `gerar_producao_classe.py` -> `main.py` |
| `ESTAB CORRIGIDO.xlsx` (estab. corrigido) | `gerar_demanda_historica.py` -> `main.py` |
| `config.yaml` (parâmetros) | `main.py` (e geradores de input se janela/granularidade mudaram) |
| Edição manual de CSV (preço/custo/demanda) | `main.py` |

### Fluxos recomendados de execução (didático)

#### Fluxo A — Rodada completa com recálculo de tudo (padrão)

Use quando houve troca de bases brutas (`parquet/xlsx`) e você quer regenerar todos os inputs.

```bash
source venv/bin/activate
./executar_pipeline.sh
```

Efeito: recalcula e sobrescreve inputs intermediários (passos 1-6), depois roda `main.py`.

#### Fluxo B — Preservar CSVs editáveis e só otimizar

Use quando você alterou manualmente `precos/custos/demanda/pedidos/producao` em `inputs/` e **não quer sobrescrever**.

```bash
source venv/bin/activate
./executar_pipeline.sh --sem-preparacao
```

Efeito: pula passos 1-6 e executa apenas a etapa de otimização/report (`main.py`) usando os CSVs já existentes.

#### Fluxo C — Reprocessamento parcial por tipo de mudança

Use quando só uma fonte foi alterada.

- Mudou faturamento: rode `extrair_compatibilidade_embalagem.py`, `extrair_precos_embalagem.py`, `gerar_demanda_historica.py`, depois `main.py`.
- Mudou carteira: rode `gerar_pedidos_clientes.py`, depois `main.py`.
- Mudou produção diária: rode `gerar_producao_classe.py`, depois `main.py`.
- Mudou somente parâmetro de otimização no `config.yaml` (sem mudar bases): rode `main.py` (ou `./executar_pipeline.sh --sem-preparacao`).

### Checklist rápido antes de rodar

1. Ativou o ambiente virtual (`source venv/bin/activate`)?
2. Vai preservar CSV manual? Se sim, use `--sem-preparacao`.
3. Confirmou `config.yaml` (estabelecimento, semana/data de referência, granularidade)?
4. Conferiu se os arquivos de `inputs/` existem para o período escolhido?

### Fallbacks de custo (configuráveis)

Para evitar valores hardcoded no código, o ETL lê fallback de custo em `config.yaml`:

```yaml
fallbacks:
  custo_medio_geral: 132.82
  qtd_ovos_por_caixa: 360
```

Esses parâmetros só são usados em cenário extremo (base de custo vazia no ETL).

## Entradas esperadas (configuradas em `config.yaml`)

| Parâmetro | Arquivo | Descrição |
|---|---|---|
| `paths.producao` | `inputs/producao_classe.csv` | Produção total por classe (gerado por `gerar_producao_classe.py`) |
| `paths.classes` | `inputs/base_skus_classes.xlsx` | Mapeamento item -> classe biológica |
| `paths.carteira_vendida` | `inputs/CARTEIRA_VENDIDA.xlsx` | Carteira de pedidos vendidos (TOTVS), usada por `gerar_pedidos_clientes.py` |
| `paths.pedidos` | `inputs/pedidos_clientes.csv` | Pedidos por SKU (gerado por `gerar_pedidos_clientes.py` a partir da carteira vendida) |
| `paths.precos` | `inputs/precos_sku_embalagem.csv` | Preços por SKU (gerado por `extrair_precos_embalagem.py`, editável) |
| `paths.custos` | `inputs/MANTI-PRIC_Custos_*.parquet` | Custos brutos (Parquet original, usado por `gerar_custos_sku.py`) |
| — | `inputs/custos_sku.csv` | Custos por SKU (gerado por `gerar_custos_sku.py`, editável) |
| — | `inputs/demanda_historica.csv` | Limites de demanda (gerado por `gerar_demanda_historica.py`, editável) |
| `paths.faturamento` | `inputs/manti_fat_*.parquet` | Faturamento histórico (para demanda, preços e enriquecimento) |
| `paths.producao_bruta` | `inputs/PRODUÇÃO DIA.xlsx` | Produção diária bruta (aba CE0302) |
| `paths.skus_restritos` | `inputs/skus_restritos.xlsx` | Filtro de SKUs ativos/permitidos |
| `paths.embalagens_override` (opcional) | `inputs/embalagens_override.xlsx` | Fallback manual de embalagem quando regex não captura |
| `paths.estab_corrigido` | `inputs/ESTAB CORRIGIDO.xlsx` | Correção de estabelecimento |

## Base física da otimização (auditoria)

A partir do ETL, o pipeline salva a base consolidada usada pelo solver em:

- `resultados/<timestamp_rodada>/base_otimizacao_<timestamp>.parquet`
- `resultados/<timestamp_rodada>/base_otimizacao_<timestamp>.csv`
- `resultados/<timestamp_rodada>/base_otimizacao_<timestamp>.xlsx`

Esse dataset contém os campos de rastreabilidade já usados internamente, por exemplo:

- `origem_preco`, `origem_custo`, `origem_embalagem`
- `usa_custo_medio_classe`, `tem_demanda_historica`
- `limite_demanda_historica`, `producao_disponivel_otimizacao_classe`

O `main.py` persiste essa base e o `Otimizador` passa a consumi-la diretamente
(com retrocompatibilidade para DataFrame em memória).

## Instalação e execução

### 1. Instalar Python 3.9+

Verificar se já está instalado:

```bash
python3 --version
```

Se não estiver instalado:

**Linux (Ubuntu/Debian):**
```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

**Mac (com Homebrew):**
```bash
brew install python@3.11
```

**Windows:**
- Baixar o instalador em https://www.python.org/downloads/
- Na instalação, marcar a opção **"Add Python to PATH"**
- Reiniciar o terminal após instalar

### 2. Download e setup

```bash
# Descompactar o zip e entrar no diretório
unzip reallocation_mix.zip
cd reallocation_mix

# Criar ambiente virtual
python3 -m venv venv

# Ativar ambiente virtual
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows (CMD)
# venv\Scripts\Activate.ps1     # Windows (PowerShell)

# Instalar dependências
pip install -r requirements.txt
```

Para verificar que tudo instalou corretamente:
```bash
python3 -c "import ortools; import pandas; print('OK - dependências instaladas')"
```

### 3. Preparar inputs

Criar a pasta `inputs/` e colocar os seguintes arquivos:

```bash
mkdir -p inputs
```

| Arquivo | Descrição | Obrigatório |
|---|---|---|
| `manti_fat_2025_full.parquet` | Faturamento histórico | Sim |
| `PRODUÇÃO DIA.xlsx` | Produção diária (aba CE0302) | Sim |
| `MANTI-PRIC_Custos_*.parquet` | Custos PRIC | Sim |
| `CARTEIRA_VENDIDA.xlsx` | Carteira de pedidos vendidos (TOTVS) | Sim |
| `base_skus_classes.xlsx` | Classificação SKU -> classe | Sim |
| `skus_restritos.xlsx` | Filtro de SKUs ativos | Sim |
| `ESTAB CORRIGIDO.xlsx` | Correção de estabelecimento | Sim |

Se os nomes dos arquivos forem diferentes, ajustar a seção `paths:` do `config.yaml`.

### 4. Executar

#### Linux / Mac

```bash
# Ativar ambiente virtual (se não estiver ativo)
source venv/bin/activate

# Pipeline completo (7 passos: preparação + main com outputs)
./executar_pipeline.sh

# Opções:
./executar_pipeline.sh --sem-preparacao   # Pula passos 1-6 (inputs já prontos)
```

#### Windows (CMD ou PowerShell)

```cmd
REM Ativar ambiente virtual
venv\Scripts\activate

REM Pipeline completo (7 passos: preparação + main com outputs)
executar_pipeline.bat

REM Opções:
executar_pipeline.bat --sem-preparacao   &REM Pula passos 1-6 (inputs já prontos)
```

#### Passo a passo manual (qualquer SO)

Se preferir rodar cada etapa separadamente:

```bash
python extrair_compatibilidade_embalagem.py    # 1. Compatibilidade embalagem
python extrair_precos_embalagem.py             # 2. Preços por SKU/embalagem
python gerar_custos_sku.py                     # 3. Custos por SKU
python gerar_demanda_historica.py              # 4. Demanda histórica por SKU
python gerar_pedidos_clientes.py               # 5. Pedidos de clientes
python gerar_producao_classe.py                # 6. Produção por classe
python main.py                                 # 7. ETL + Otimização + Output + DOW + Comparação + PBI
# python comparar_producao_alocacao.py         # (opcional) gerar relatório comparativo avulso
# python output/gerar_consolidado_pbi.py       # (opcional) gerar consolidado PBI avulso
```

> **Nota**: No Windows use `python` em vez de `python3`. No Linux/Mac use `python3` ou `python` (depende da instalação). Os CSVs gerados nos passos 2-6 podem ser editados manualmente antes de rodar o passo 7.

### 5. Resultados

Arquivos gerados em `resultados/<YYYYMMDD_HHMMSS>/` (uma subpasta por rodada).

## Outputs gerados

| Arquivo | Descrição |
|---|---|
| `<rodada_ts>/resultado_realocacao_completo_*.csv` | Alocação detalhada por item_id |
| `<rodada_ts>/resultado_realocacao_completo_*.xlsx` | Excel com abas: Detalhado, Resumo por Classe, Estatísticas, Pedidos Ignorados |
| `<rodada_ts>/auditoria_baseline_*.xlsx` | Auditoria: Detalhe por SKU, Resumo por Classe, Parâmetros |
| `<rodada_ts>/demanda_historica_*.xlsx` | Limites de demanda histórica calculados |
| `<rodada_ts>/comparacao_producao_alocacao_*.xlsx` | Comparação produção real vs alocação do modelo (inclui `quantidade_nao_atendida_pedido`) |
| `<rodada_ts>/skus_fora_otimizacao_*.csv` | SKUs fora da otimização com motivo principal |
| `<rodada_ts>/distribuicao_historica_dow_*.xlsx` | Distribuição histórica diária por SKU (DOW) |
| `<rodada_ts>/pbi_consolidado_sku_*.csv` | Consolidação SKU para BI (flat, inclui `quantidade_nao_atendida_pedido`) |
| `<rodada_ts>/pbi_skus_fora_otimizacao_*.csv` | SKUs fora da otimização para consumo no BI |
| `<rodada_ts>/pbi_consolidado_*.xlsx` | Consolidação BI com abas: consolidado_sku, distribuicao_diaria, parametros, skus_fora_otimizacao (inclui `quantidade_nao_atendida_pedido` na aba consolidado_sku) |

## Configuração relevante (`config.yaml`)

- **Objetivo**: `modelo.tipo_objetivo` = `maximizar_margem` (padrão)
- **Pedidos**: `modelo.atender_pedidos` (True prioriza pedidos garantidos)
- **Referência temporal de pedidos (independente)**:
  - `dados.pedidos_semana_ref` (usado quando `modelo.granularidade_demanda = S`)
  - `dados.pedidos_data_ref` (usado quando `modelo.granularidade_demanda = D` ou `M`)
  - a granularidade dos pedidos segue `modelo.granularidade_demanda`; apenas a referência temporal é independente
- **Cap de reserva**: `modelo.capar_reserva_na_producao` (True limita reservas à produção disponível, priorizando por margem)
- **Demanda histórica**: `modelo.considerar_demanda_historica`, `granularidade_demanda` (M/S/D), `tipo_calculo_demanda` (`percentil`, `maximo`, `media`)
- **Solver**: `solver.solver_type`, `time_limit_ms`, `num_threads`

### Regras de filtro de pedidos por período

O script `gerar_pedidos_clientes.py` aplica o filtro de período pela data de entrega (`Dt.Entrega`) assim:

- `granularidade_demanda = S`: usa `dados.pedidos_semana_ref` (fallback: `dados.semana_ref`)
- `granularidade_demanda = D`: usa `dados.pedidos_data_ref` (fallback: `dados.data_ref`) no dia exato
- `granularidade_demanda = M`: usa `dados.pedidos_data_ref` (fallback: `dados.data_ref`) no mês da data

Quando o usuário informa uma referência incompatível com a granularidade ativa (ex.: `pedidos_semana_ref` com granularidade `D`/`M`), o script emite aviso durante o filtro e repete no resumo final.

## Autor

Romulo Brito
