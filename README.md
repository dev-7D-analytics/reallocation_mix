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
├── main.py                              # Orquestrador: ETL -> Otimização -> Output
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
│   └── resultados.py                    # Salvamento de resultados (CSV/Excel)
│
├── gerar_pedidos_clientes.py            # Gera pedidos_clientes.csv (editável)
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
└── resultados/                          # Outputs gerados (não versionados)
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
     ├─ manti_fat_*.parquet ──► gerar_pedidos_clientes.py ─► inputs/pedidos_clientes.csv (editável)
     │   skus_restritos.xlsx
     │   ESTAB CORRIGIDO.xlsx
     │
     ├─ PRODUÇÃO DIA.xlsx ────► gerar_producao_classe.py ──► inputs/producao_classe.csv (editável)
     │   base_skus_classes.xlsx
     │
     └──────────────────────────► main.py (ETL + Otimização + Output)
                                    │
                                    ▼
                                comparar_producao_alocacao.py (relatório final)
```

### Inputs editáveis pelo usuário

Após a geração automática, os seguintes CSVs podem ser editados manualmente antes de rodar `main.py`:

| Arquivo | Colunas mínimas | O que o usuário pode fazer |
|---|---|---|
| `inputs/precos_sku_embalagem.csv` | `item`, `preco` | Alterar preço de um SKU, adicionar SKU novo |
| `inputs/custos_sku.csv` | `item`, `custo_ytd` | Alterar custo de um SKU, adicionar SKU novo |
| `inputs/demanda_historica.csv` | `item`, `demanda_max` | Alterar limite de demanda, adicionar/remover SKU |

Os CSVs já saem com os cálculos aplicados (ex: fator multiplicativo na demanda, média ponderada no custo). O modelo lê diretamente desses arquivos.

### Mapa de impacto: base atualizada -> scripts a re-executar

| Base de dados atualizada | Scripts a re-executar (na ordem) |
|---|---|
| `manti_fat_*.parquet` (faturamento) | `extrair_compatibilidade_embalagem.py` -> `extrair_precos_embalagem.py` -> `gerar_demanda_historica.py` -> `gerar_pedidos_clientes.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `PRODUÇÃO DIA.xlsx` (produção diária) | `gerar_producao_classe.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `MANTI-PRIC_Custos_*.parquet` (custos) | `gerar_custos_sku.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `base_skus_classes.xlsx` (classificação) | `gerar_producao_classe.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `skus_restritos.xlsx` (SKUs permitidos) | `gerar_pedidos_clientes.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `ESTAB CORRIGIDO.xlsx` (estab. corrigido) | `gerar_demanda_historica.py` -> `gerar_pedidos_clientes.py` -> `main.py` -> `comparar_producao_alocacao.py` |
| `config.yaml` (parâmetros) | `main.py` -> `comparar_producao_alocacao.py` (e geradores de input se janela/granularidade mudaram) |
| Edição manual de CSV (preço/custo/demanda) | `main.py` -> `comparar_producao_alocacao.py` |

## Entradas esperadas (configuradas em `config.yaml`)

| Parâmetro | Arquivo | Descrição |
|---|---|---|
| `paths.producao` | `inputs/producao_classe.csv` | Produção total por classe (gerado por `gerar_producao_classe.py`) |
| `paths.classes` | `inputs/base_skus_classes.xlsx` | Mapeamento item -> classe biológica |
| `paths.pedidos` | `inputs/pedidos_clientes.csv` | Pedidos por SKU (gerado por `gerar_pedidos_clientes.py`) |
| `paths.precos` | `inputs/precos_sku_embalagem.csv` | Preços por SKU (gerado por `extrair_precos_embalagem.py`, editável) |
| `paths.custos` | `inputs/MANTI-PRIC_Custos_*.parquet` | Custos brutos (Parquet original, usado por `gerar_custos_sku.py`) |
| — | `inputs/custos_sku.csv` | Custos por SKU (gerado por `gerar_custos_sku.py`, editável) |
| — | `inputs/demanda_historica.csv` | Limites de demanda (gerado por `gerar_demanda_historica.py`, editável) |
| `paths.faturamento` | `inputs/manti_fat_*.parquet` | Faturamento histórico (para demanda, preços e enriquecimento) |
| `paths.producao_bruta` | `inputs/PRODUÇÃO DIA.xlsx` | Produção diária bruta (aba CE0302) |
| `paths.skus_restritos` | `inputs/skus_restritos.xlsx` | Filtro de SKUs ativos/permitidos |
| `paths.estab_corrigido` | `inputs/ESTAB CORRIGIDO.xlsx` | Correção de estabelecimento |

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
| `base_skus_classes.xlsx` | Classificação SKU -> classe | Sim |
| `skus_restritos.xlsx` | Filtro de SKUs ativos | Sim |
| `ESTAB CORRIGIDO.xlsx` | Correção de estabelecimento | Sim |

Se os nomes dos arquivos forem diferentes, ajustar a seção `paths:` do `config.yaml`.

### 4. Executar

#### Linux / Mac

```bash
# Ativar ambiente virtual (se não estiver ativo)
source venv/bin/activate

# Pipeline completo (8 passos: preparação + otimização + relatório)
./executar_pipeline.sh

# Opções:
./executar_pipeline.sh --sem-preparacao   # Pula passos 1-6 (inputs já prontos)
./executar_pipeline.sh --sem-relatorio    # Pula passo 8 (sem relatório comparativo)
```

#### Windows (CMD ou PowerShell)

```cmd
REM Ativar ambiente virtual
venv\Scripts\activate

REM Pipeline completo (8 passos)
executar_pipeline.bat

REM Opções:
executar_pipeline.bat --sem-preparacao   &REM Pula passos 1-6 (inputs já prontos)
executar_pipeline.bat --sem-relatorio    &REM Pula passo 8 (sem relatório comparativo)
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
python main.py                                 # 7. ETL + Otimização + Output
python comparar_producao_alocacao.py           # 8. Relatório comparativo
```

> **Nota**: No Windows use `python` em vez de `python3`. No Linux/Mac use `python3` ou `python` (depende da instalação). Os CSVs gerados nos passos 2-6 podem ser editados manualmente antes de rodar o passo 7.

### 5. Resultados

Arquivos gerados na pasta `resultados/`.

## Outputs gerados

| Arquivo | Descrição |
|---|---|
| `resultado_realocacao_completo_*.csv` | Alocação detalhada por item_id |
| `resultado_realocacao_completo_*.xlsx` | Excel com abas: Detalhado, Resumo por Classe, Estatísticas, Pedidos Ignorados |
| `auditoria_baseline_*.xlsx` | Auditoria: Detalhe por SKU, Resumo por Classe, Parâmetros |
| `demanda_historica_*.xlsx` | Limites de demanda histórica calculados |
| `comparacao_producao_alocacao_*.xlsx` | Comparação produção real vs alocação do modelo |

## Configuração relevante (`config.yaml`)

- **Objetivo**: `modelo.tipo_objetivo` = `maximizar_margem` (padrão)
- **Pedidos**: `modelo.atender_pedidos` (True prioriza pedidos garantidos)
- **Cap de reserva**: `modelo.capar_reserva_na_producao` (True limita reservas à produção disponível, priorizando por margem)
- **Demanda histórica**: `modelo.considerar_demanda_historica`, `granularidade_demanda` (M/S/D), `tipo_calculo_demanda` (`percentil`, `maximo`, `media`)
- **Solver**: `solver.solver_type`, `time_limit_ms`, `num_threads`

## Autor

Romulo Brito
