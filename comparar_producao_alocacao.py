import argparse
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd

from extrair_compatibilidade_embalagem import (
    calcular_qtd_embalagem,
    extrair_embalagem_descricao,
)
from metadados_output import aplicar_colunas_estabelecimento


INPUT_PATH = Path("inputs")
SHEET_NAME = "CE0302"
RESULTS_DIR = Path("resultados")
DEFAULT_YEAR_WEEK = None
CONFIG_PATH = Path("config.yaml")
DEFAULT_PRECOS_PATH = INPUT_PATH / "precos_sku_embalagem.csv"
DEFAULT_CUSTOS_PATH = INPUT_PATH / "CUSTO ITEM.csv"


def extract_week(df: pd.DataFrame, date_column: str) -> pd.Series:
    """Extract ISO week from a date column."""
    return pd.to_datetime(df[date_column]).dt.isocalendar().week


def extract_year(df: pd.DataFrame, date_column: str) -> pd.Series:
    """Extract ISO year from a date column."""
    return pd.to_datetime(df[date_column]).dt.isocalendar().year


def get_week_start_date(year_week: str) -> pd.Timestamp:
    """Return the Monday for a given ISO year-week (YYYY-WW)."""
    year, week = year_week.split("-")
    return pd.to_datetime(f"{year}-W{week}-1", format="%G-W%V-%u")


def _resolver_coluna_classe(df_classes: pd.DataFrame) -> Optional[str]:
    """Detect the class column name in the SKU/class mapping."""
    for col in df_classes.columns:
        if "classe" in col.lower() and "produto" in col.lower():
            return col
    return None


def _carregar_config(config_path: Path = CONFIG_PATH) -> Dict:
    """Load YAML config if available; fallback to empty dict when pyyaml is absent."""
    try:
        import yaml
    except ImportError:
        return {}
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolver_caminho(config: Dict, chave: str, default: Path) -> Path:
    """Read path from config or fallback to default."""
    return Path(config.get("paths", {}).get(chave, default))


def _carregar_precos(config: Dict) -> pd.DataFrame:
    """Carga de precos conforme modelo canônico."""
    path = _resolver_caminho(config, "precos", DEFAULT_PRECOS_PATH)
    if not path.exists():
        return pd.DataFrame(columns=["item_id", "preco"])

    # Tentar ler com separador de virgula primeiro, depois ponto e virgula
    try:
        df_precos = pd.read_csv(path, sep=",", decimal=".")
    except:
        df_precos = pd.read_csv(path, sep=";", decimal=",")

    if "preco" not in df_precos.columns:
        if "preco_ponderado" in df_precos.columns:
            df_precos["preco"] = df_precos["preco_ponderado"]
        elif "preco_medio" in df_precos.columns:
            df_precos["preco"] = df_precos["preco_medio"]

    if "item" in df_precos.columns and "embalagem" in df_precos.columns:
        df_precos["item"] = pd.to_numeric(df_precos["item"], errors="coerce")
        df_precos = df_precos[df_precos["item"].notna()].copy()
        df_precos["item"] = df_precos["item"].astype(int)
        df_precos["item_id"] = df_precos["item"].astype(str) + "_" + df_precos["embalagem"]
    elif "item_id" not in df_precos.columns:
        raise ValueError("Arquivo de precos deve conter 'item_id' ou ('item' e 'embalagem')")

    df_precos = df_precos[df_precos["preco"] > 0].copy()
    # Garantir coluna item para fallback por SKU (qualquer embalagem) no comparador
    if "item" not in df_precos.columns and "item_id" in df_precos.columns:
        df_precos["item"] = pd.to_numeric(df_precos["item_id"].astype(str).str.split("_").str[0], errors="coerce")
    colunas_out = ["item_id", "preco"]
    if "item" in df_precos.columns:
        colunas_out = ["item_id", "item", "preco"]
    return df_precos[colunas_out].drop_duplicates(["item_id"])


def _carregar_pedidos(config: Dict) -> pd.DataFrame:
    """Carrega pedidos por SKU com campos enriquecidos (preco_pedido, data_entrega)."""
    path = _resolver_caminho(config, "pedidos", INPUT_PATH / "pedidos_clientes.csv")
    if not path.exists():
        return pd.DataFrame(columns=["item", "quantidade_total_pedida"])
    
    try:
        df_pedidos = pd.read_csv(path)
        
        # Detectar coluna de quantidade
        col_qtd = None
        for col in df_pedidos.columns:
            col_lower = col.lower()
            if col_lower == 'quantidade':
                col_qtd = col
                break
            if 'quantidade' in col_lower or 'qtd' in col_lower:
                col_qtd = col
        
        if 'item' not in df_pedidos.columns or col_qtd is None:
            return pd.DataFrame(columns=["item", "quantidade_total_pedida"])
        
        df_pedidos['item'] = pd.to_numeric(df_pedidos['item'], errors='coerce')
        df_pedidos = df_pedidos[df_pedidos['item'].notna()].copy()
        df_pedidos['item'] = df_pedidos['item'].astype(int)
        df_pedidos = df_pedidos[df_pedidos[col_qtd] > 0].copy()
        
        # Construir resultado com campos extras se disponíveis
        result = df_pedidos[['item', col_qtd]].copy()
        result.columns = ['item', 'quantidade_total_pedida']
        
        for col_extra in ['preco_pedido', 'data_entrega_min', 'data_entrega_max',
                          'n_pedidos', 'n_clientes', 'qt_pedida_caixas']:
            if col_extra in df_pedidos.columns:
                result[col_extra] = df_pedidos[col_extra].values
        
        return result
    except Exception:
        return pd.DataFrame(columns=["item", "quantidade_total_pedida"])


def _carregar_skus_restritos(config: Dict) -> pd.DataFrame:
    """Carrega SKUs permitidos para produção no estabelecimento (ESTAB, STATUS=ATIVO).
    
    NOTA: O nome da função foi mantido para compatibilidade, mas a semântica mudou:
    - Antes: lista de SKUs restritos (não podem receber alocação)
    - Agora: lista de SKUs permitidos (podem receber alocação no estabelecimento)
    """
    path = _resolver_caminho(config, "skus_restritos", INPUT_PATH / "skus_restritos.xlsx")
    if not path.exists():
        return pd.DataFrame(columns=["item"])
    
    try:
        df_restritos = pd.read_excel(path)
        
        # Ler lista de estabelecimentos do config
        estabelecimentos_config = config.get('dados', {}).get('estabelecimentos', [100])
        if not isinstance(estabelecimentos_config, list):
            estabelecimentos_config = [estabelecimentos_config]
        estabelecimentos = [int(estab) for estab in estabelecimentos_config]
        
        # Filtrar por STATUS='ATIVO' e ESTAB na lista de estabelecimentos
        if 'STATUS' in df_restritos.columns and 'ESTAB' in df_restritos.columns:
            df_restritos_filtrado = df_restritos[
                (df_restritos['STATUS'] == 'ATIVO') & 
                (df_restritos['ESTAB'].isin(estabelecimentos))
            ].copy()
        else:
            df_restritos_filtrado = df_restritos.copy()
        
        # Extrair lista de SKUs permitidos para produção no estabelecimento
        if 'item' in df_restritos_filtrado.columns:
            skus_restritos = df_restritos_filtrado['item'].tolist()
            skus_restritos = [int(item) for item in skus_restritos if pd.notna(item)]
            return pd.DataFrame({'item': skus_restritos})
        else:
            return pd.DataFrame(columns=["item"])
    except Exception:
        return pd.DataFrame(columns=["item"])


def _carregar_skus_ativos_completo(config: Dict) -> pd.DataFrame:
    """Carrega SKUs ATIVOS do estabelecimento com todas as informações disponíveis.
    
    Filtra por STATUS='ATIVO' e ESTAB na lista de estabelecimentos do config.
    
    Returns:
        DataFrame com colunas: item, descricao (do cadastro), tipo_cadastro, status_cadastro
    """
    path = _resolver_caminho(config, "skus_restritos", INPUT_PATH / "skus_restritos.xlsx")
    if not path.exists():
        return pd.DataFrame(columns=["item", "descricao_cadastro", "tipo_cadastro", "status_cadastro"])
    
    try:
        df_restritos = pd.read_excel(path)
        
        # Ler lista de estabelecimentos do config
        estabelecimentos_config = config.get('dados', {}).get('estabelecimentos', [100])
        if not isinstance(estabelecimentos_config, list):
            estabelecimentos_config = [estabelecimentos_config]
        estabelecimentos = [int(estab) for estab in estabelecimentos_config]
        
        # Filtrar por STATUS='ATIVO' e ESTAB na lista de estabelecimentos
        if 'STATUS' in df_restritos.columns and 'ESTAB' in df_restritos.columns:
            df_filtrado = df_restritos[
                (df_restritos['STATUS'] == 'ATIVO') & 
                (df_restritos['ESTAB'].isin(estabelecimentos))
            ].copy()
        elif 'ESTAB' in df_restritos.columns:
            df_filtrado = df_restritos[df_restritos['ESTAB'].isin(estabelecimentos)].copy()
        else:
            df_filtrado = df_restritos.copy()
        
        if 'item' not in df_filtrado.columns:
            return pd.DataFrame(columns=["item", "descricao_cadastro", "tipo_cadastro", "status_cadastro"])
        
        # Preparar DataFrame de saída com informações úteis
        df_filtrado['item'] = pd.to_numeric(df_filtrado['item'], errors='coerce')
        df_filtrado = df_filtrado[df_filtrado['item'].notna()].copy()
        df_filtrado['item'] = df_filtrado['item'].astype(int)
        
        result = pd.DataFrame({'item': df_filtrado['item'].values})
        
        # Mapear colunas disponíveis
        if 'DESCRIÇÃO' in df_filtrado.columns:
            result['descricao_cadastro'] = df_filtrado['DESCRIÇÃO'].values
        else:
            result['descricao_cadastro'] = None
            
        if 'TIPO' in df_filtrado.columns:
            result['tipo_cadastro'] = df_filtrado['TIPO'].values
        else:
            result['tipo_cadastro'] = None
            
        if 'STATUS' in df_filtrado.columns:
            result['status_cadastro'] = df_filtrado['STATUS'].values
        else:
            result['status_cadastro'] = None
        
        return result.drop_duplicates('item', keep='first')
    except Exception:
        return pd.DataFrame(columns=["item", "descricao_cadastro", "tipo_cadastro", "status_cadastro"])


def _carregar_demanda_historica_completa(caminho: Optional[Path] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Carrega demanda histórica completa a partir de demanda_historica_*.xlsx em resultados/.
    
    Returns:
        (df_demanda, df_param): df_demanda com colunas item, descricao, classe, demanda_max;
        df_param com colunas parametro, valor (aba Parametros do Excel).
    """
    if caminho is None:
        candidatos = sorted(RESULTS_DIR.glob("demanda_historica_*.xlsx"))
        if not candidatos:
            return pd.DataFrame(columns=["item", "descricao", "classe", "demanda_max"]), pd.DataFrame(columns=["parametro", "valor"])
        caminho = candidatos[-1]
    if not caminho.exists():
        return pd.DataFrame(columns=["item", "descricao", "classe", "demanda_max"]), pd.DataFrame(columns=["parametro", "valor"])
    try:
        df_demanda = pd.read_excel(caminho, sheet_name="Demanda")
        df_demanda["item"] = pd.to_numeric(df_demanda["item"], errors="coerce")
        df_demanda = df_demanda[df_demanda["item"].notna()].copy()
        df_demanda["item"] = df_demanda["item"].astype(int)
        if "demanda_max" not in df_demanda.columns:
            return pd.DataFrame(columns=["item", "descricao", "classe", "demanda_max"]), pd.DataFrame(columns=["parametro", "valor"])
        df_param = pd.read_excel(caminho, sheet_name="Parametros")
        if "parametro" not in df_param.columns or "valor" not in df_param.columns:
            df_param = pd.DataFrame(columns=["parametro", "valor"])
        return df_demanda, df_param
    except Exception:
        return pd.DataFrame(columns=["item", "descricao", "classe", "demanda_max"]), pd.DataFrame(columns=["parametro", "valor"])


def _carregar_custos(config: Dict) -> pd.DataFrame:
    """Carga de custos conforme modelo canônico - suporta CSV ou Parquet."""
    path = _resolver_caminho(config, "custos", DEFAULT_CUSTOS_PATH)
    if not path.exists():
        return pd.DataFrame(columns=["item_id", "item", "embalagem", "custo_ytd"])

    # Detectar se é parquet ou CSV
    is_parquet = path.suffix.lower() == '.parquet'
    
    if is_parquet:
        # Ler apenas colunas necessárias
        colunas_necessarias = ['Estab', 'item', 'MÊS', 'ano', 'Custo Médio', 'Quantidade', 'UF', 'Descrição do item']
        df_custo = pd.read_parquet(path, columns=colunas_necessarias, engine='pyarrow')
        
        # Aplicar filtros do config (mesmo do modelo principal)
        dados_config = config.get('dados', {})
        mes_custo = dados_config.get('mes_custo', 11)  # Mês de referência
        ano_custo = dados_config.get('ano_custo', 2025)  # Ano de referência
        estab_custo = dados_config.get('estab_custo', 100)  # Default: 100
        meses_janela = dados_config.get('meses_janela_custo', 6)  # Janela de meses (últimos N meses)
        
        # Calcular range de meses (filtro vetorizado para evitar apply em 150k+ linhas)
        if meses_janela > 1:
            periodos = []
            for i in range(meses_janela):
                mes = mes_custo - i
                ano = ano_custo
                while mes <= 0:
                    mes += 12
                    ano -= 1
                periodos.append((ano, mes))
            periodos_set = set(periodos)
            mask_estab = df_custo['Estab'] == estab_custo
            df_custo['_periodo'] = list(zip(df_custo['ano'], df_custo['MÊS']))
            mask_periodo = df_custo['_periodo'].isin(periodos_set)
            df_custo = df_custo[mask_estab & mask_periodo].drop(columns=['_periodo']).copy()
        else:
            df_custo = df_custo[
                (df_custo['Estab'] == estab_custo) & 
                (df_custo['MÊS'] == mes_custo) & 
                (df_custo['ano'] == ano_custo)
            ].copy()
        
        # Filtrar exportacao (UF != 'EX')
        df_custo = df_custo.loc[df_custo['UF'] != 'EX'].copy()
        
        # Converter tipos para numérico
        df_custo['Custo Médio'] = pd.to_numeric(df_custo['Custo Médio'], errors='coerce')
        df_custo['Quantidade'] = pd.to_numeric(df_custo['Quantidade'], errors='coerce')
        df_custo = df_custo[df_custo['Quantidade'] > 0].copy()
        df_custo = df_custo[df_custo['Custo Médio'].notna()].copy()
        
        # Calcular custo por caixa: custos_cx360 = Custo Médio / Quantidade
        df_custo['custos_cx360'] = df_custo['Custo Médio'] / df_custo['Quantidade']
        
        # Custo Médio já é o custo total da transação (para agregação ponderada)
        df_custo['custo_total'] = df_custo['Custo Médio']
        
        # Criar coluna ano_mes
        df_custo['ano_mes'] = df_custo['ano'].astype(str) + '-' + df_custo['MÊS'].astype(str).str.zfill(2)
        
        # Extrair embalagem e criar item_id (aplicar só em descrições únicas para ganho de velocidade)
        col_item_desc = 'Descrição do item'
        unicos_desc = df_custo[col_item_desc].dropna().unique()
        mapa_embalagem = {d: extrair_embalagem_descricao(d) for d in unicos_desc}
        df_custo['embalagem'] = df_custo[col_item_desc].map(mapa_embalagem)
        df_custo['item'] = pd.to_numeric(df_custo['item'], errors='coerce')
        df_custo = df_custo[df_custo['item'].notna() & df_custo['embalagem'].notna()].copy()
        df_custo['item_id'] = df_custo['item'].astype(str) + '_' + df_custo['embalagem']
        
        # Primeira agregação: por (item, embalagem, ano_mes) - somar custo total e quantidade
        # (mesmo racional de preços - EXATAMENTE como no modelo_otimizacao_com_realocacao.py)
        custos_por_ano_mes = df_custo.groupby(['item', 'embalagem', 'ano_mes']).agg({
            'Quantidade': 'sum',
            'custo_total': 'sum',
            col_item_desc: 'first'
        }).reset_index()
        
        # Calcular custo ponderado por ano_mes (ANTES de renomear)
        custos_por_ano_mes['custo_ponderado_ano_mes'] = custos_por_ano_mes['custo_total'] / custos_por_ano_mes['Quantidade']
        
        # Renomear colunas (EXATAMENTE como no modelo_otimizacao_com_realocacao.py)
        custos_por_ano_mes.columns = ['item', 'embalagem', 'ano_mes', 'volume_ano_mes', 'custo_total_ano_mes', 'descricao_item', 'custo_ponderado_ano_mes']
        
        # Segunda agregação: por (item, embalagem) - somar custos e volumes totais
        # (EXATAMENTE como no modelo_otimizacao_com_realocacao.py)
        df_custo_agg = custos_por_ano_mes.groupby(['item', 'embalagem']).agg({
            'volume_ano_mes': 'sum',
            'custo_total_ano_mes': 'sum',
            'descricao_item': 'first',
            'ano_mes': 'count'  # Número de meses com dados
        }).reset_index()
        
        # Renomear colunas (EXATAMENTE como no modelo_otimizacao_com_realocacao.py)
        df_custo_agg.columns = ['item', 'embalagem', 'volume_total', 'custo_total', 'descricao_item', 'num_meses']
        
        # Calcular custo final ponderado por volume total
        df_custo_agg['custo_ytd'] = df_custo_agg['custo_total'] / df_custo_agg['volume_total']
        df_custo_agg['item_id'] = df_custo_agg['item'].astype(str) + '_' + df_custo_agg['embalagem']
        
        df_custo = df_custo_agg[['item_id', 'item', 'embalagem', 'custo_ytd']].copy()
        
        # Filtrar valores negativos
        df_custo = df_custo[df_custo['custo_ytd'] > 0].copy()
        
        # Retornar diretamente após processar parquet (não executar código de CSV)
        return df_custo[["item_id", "item", "embalagem", "custo_ytd"]]
        
    else:
        # Leitura de CSV (comportamento original)
        try:
            df_custo = pd.read_csv(path, encoding='utf-8')
        except UnicodeDecodeError:
            df_custo = pd.read_csv(path, encoding='latin-1')

        col_item_desc = None
        for col in df_custo.columns:
            if "item" in col.lower() and "descri" in col.lower():
                col_item_desc = col
                break
        if col_item_desc is None:
            col_item_desc = df_custo.columns[0]

        df_custo["item"] = pd.to_numeric(
            df_custo[col_item_desc].astype(str).str.extract(r"^(\d+)")[0],
            errors="coerce",
        )
        df_custo["embalagem"] = df_custo[col_item_desc].apply(extrair_embalagem_descricao)

        def parse_currency(valor):
            if pd.isna(valor):
                return np.nan
            limpo = str(valor).replace("R$", "").replace(".", "").replace(",", ".").strip()
            try:
                return float(limpo) if limpo else np.nan
            except Exception:
                return np.nan

        col_custo = None
        for col in df_custo.columns:
            if "custo" in col.lower() and "ytd" in col.lower():
                col_custo = col
                break
        if col_custo is None:
            col_custo = df_custo.columns[-1]

        df_custo["custo_ytd"] = df_custo[col_custo].apply(parse_currency)

        # Extrair embalagem (mesma lógica para ambos)
        if 'embalagem' not in df_custo.columns:
            df_custo["embalagem"] = df_custo[col_item_desc].apply(extrair_embalagem_descricao)

        # Filtrar registros válidos
        df_custo = df_custo[
            df_custo["item"].notna()
            & df_custo["custo_ytd"].notna()
            & df_custo["embalagem"].notna()
        ].copy()
        df_custo["item"] = df_custo["item"].astype(int)
        df_custo["item_id"] = df_custo["item"].astype(str) + "_" + df_custo["embalagem"]

        # Agregar duplicatas por item_id usando média
        df_custo = df_custo.groupby('item_id').agg({
            'item': 'first',
            'embalagem': 'first',
            'custo_ytd': 'mean'
        }).reset_index()

        return df_custo[["item_id", "item", "embalagem", "custo_ytd"]]


def carregar_producao(year_week: Optional[str], config: Optional[Dict] = None) -> Tuple[pd.DataFrame, str]:
    """Read production data and return aggregation by item_id.
    
    Respeita granularidade_demanda do config:
    - S (semanal): filtra por semana ISO (year_week)
    - D (diário): filtra por data_ref (um dia)
    - M (mensal): filtra por mês de data_ref
    """
    if config:
        producao_bruta_path = _resolver_caminho(config, "producao_bruta", INPUT_PATH / "PRODUÇÃO DIA.xlsx")
    else:
        producao_bruta_path = INPUT_PATH / "PRODUÇÃO DIA.xlsx"
    excel_path = Path(producao_bruta_path)
    df_prod = pd.read_excel(excel_path, sheet_name=SHEET_NAME, skiprows=1)
    df_prod["Data Trans"] = pd.to_datetime(df_prod["Data Trans"], errors="coerce")

    # Filtrar por estabelecimentos configurados
    if config and "Est" in df_prod.columns:
        estabelecimentos = config.get("dados", {}).get("estabelecimentos", [100])
        if estabelecimentos:
            total_antes = len(df_prod)
            df_prod = df_prod[df_prod["Est"].isin(estabelecimentos)].copy()
            print(f"  Filtro estabelecimentos {estabelecimentos}: {total_antes} -> {len(df_prod)} registros")

    # Determinar granularidade
    granularidade = config.get("modelo", {}).get("granularidade_demanda", "S").upper() if config else "S"

    if granularidade == "D":
        # Diário: filtrar por data_ref
        data_ref = config.get("dados", {}).get("data_ref") if config else None
        if not data_ref:
            raise ValueError("Para granularidade diária (D), é necessário definir 'data_ref' em dados no config.yaml")
        data_ref = pd.to_datetime(data_ref)
        df_prod = df_prod[df_prod["Data Trans"].dt.date == data_ref.date()].copy()
        periodo_label = data_ref.strftime("%Y-%m-%d")
        data_producao = data_ref
        if df_prod.empty:
            raise ValueError(f"Nao encontrei registros para o dia {periodo_label} em {excel_path}")

    elif granularidade == "M":
        # Mensal: filtrar por mês de data_ref
        data_ref = config.get("dados", {}).get("data_ref") if config else None
        if not data_ref:
            raise ValueError("Para granularidade mensal (M), é necessário definir 'data_ref' em dados no config.yaml")
        data_ref = pd.to_datetime(data_ref)
        df_prod = df_prod[
            (df_prod["Data Trans"].dt.year == data_ref.year) &
            (df_prod["Data Trans"].dt.month == data_ref.month)
        ].copy()
        periodo_label = data_ref.strftime("%Y-%m")
        data_producao = data_ref.replace(day=1)
        if df_prod.empty:
            raise ValueError(f"Nao encontrei registros para o mês {periodo_label} em {excel_path}")

    else:
        # Semanal (padrão): filtrar por semana ISO
        df_prod["week"] = extract_week(df_prod, "Data Trans")
        df_prod["year"] = extract_year(df_prod, "Data Trans")
        df_prod["year_week"] = df_prod["year"].astype(str) + "-" + df_prod["week"].astype(str).str.zfill(2)
        target_year_week = year_week or sorted(df_prod["year_week"].unique())[-1]
        df_prod = df_prod[df_prod["year_week"] == target_year_week].copy()
        periodo_label = target_year_week
        data_producao = get_week_start_date(target_year_week)
        if df_prod.empty:
            raise ValueError(f"Nao encontrei registros para a semana {periodo_label} em {excel_path}")

    # Ajuste de sinal para estorno de produção:
    # ACA = produção positiva; EAC = estorno (deve ser negativo)
    col_esp = next((c for c in ["Esp", "ESP", "Especie", "Espécie"] if c in df_prod.columns), None)
    df_prod["Quantidade"] = pd.to_numeric(df_prod["Quantidade"], errors="coerce").fillna(0.0)
    if col_esp is not None:
        esp_serie = df_prod[col_esp].astype(str).str.strip().str.upper()
        mask_eac = esp_serie == "EAC"
        mask_aca = esp_serie == "ACA"
        df_prod.loc[mask_eac, "Quantidade"] = -df_prod.loc[mask_eac, "Quantidade"].abs()
        df_prod.loc[mask_aca, "Quantidade"] = df_prod.loc[mask_aca, "Quantidade"].abs()

    df_prod["embalagem"] = df_prod["Desc Item"].apply(extrair_embalagem_descricao)
    df_prod["qtd_embalagem"] = df_prod["embalagem"].apply(calcular_qtd_embalagem)
    df_prod["quantidade"] = df_prod["Quantidade"] * df_prod["qtd_embalagem"]

    df_prod["item"] = pd.to_numeric(df_prod["Cod Item"], errors="coerce")
    df_prod = df_prod[
        (df_prod["item"].notna())
        & (df_prod["embalagem"].notna())
        & (df_prod["quantidade"].notna())
        & (df_prod["quantidade"] > 0)
    ].copy()
    df_prod["item"] = df_prod["item"].astype(int)

    # Filtrar apenas SKUs ativos no estabelecimento
    if config:
        skus_restritos_path = Path(config.get("paths", {}).get("skus_restritos", "inputs/skus_restritos.xlsx"))
        if skus_restritos_path.exists():
            df_skus = pd.read_excel(skus_restritos_path)
            if "STATUS" in df_skus.columns and "ESTAB" in df_skus.columns:
                estabelecimentos = config.get("dados", {}).get("estabelecimentos", [100])
                df_ativos = df_skus[
                    (df_skus["STATUS"] == "ATIVO") &
                    (df_skus["ESTAB"].isin(estabelecimentos))
                ]
                skus_ativos = set(df_ativos["item"].astype(int).tolist())
                total_antes = len(df_prod)
                prod_antes = df_prod["quantidade"].sum()
                df_prod = df_prod[df_prod["item"].isin(skus_ativos)]
                print(f"  Filtro SKUs ativos: {total_antes} -> {len(df_prod)} registros ({prod_antes - df_prod['quantidade'].sum():,.0f} ovos removidos)")

    df_prod["item_id"] = df_prod["item"].astype(str) + "_" + df_prod["embalagem"]
    df_prod["data_producao"] = data_producao

    # Anexar classe do SKU
    if config:
        classes_path = _resolver_caminho(config, "classes", INPUT_PATH / "base_skus_classes.xlsx")
    else:
        classes_path = INPUT_PATH / "base_skus_classes.xlsx"
    df_classes = pd.read_excel(Path(classes_path))
    col_classe = _resolver_coluna_classe(df_classes)
    if col_classe is None:
        raise ValueError("Coluna de classe nao encontrada em base_skus_classes.xlsx")

    df_classes = df_classes[["item", col_classe]].rename(columns={col_classe: "classe"})
    df_prod = df_prod.merge(df_classes, on="item", how="left")
    df_prod["classe"] = df_prod["classe"].fillna("OUTROS")

    prod_agg = (
        df_prod.groupby(["item", "embalagem", "item_id", "classe"], as_index=False)["quantidade"]
        .sum()
        .rename(columns={"quantidade": "quantidade_produzida"})
    )
    prod_agg["periodo_label"] = periodo_label
    prod_agg["data_producao"] = data_producao

    return prod_agg, periodo_label


def carregar_alocacao(arquivo_resultado: Optional[str], sep: str = ",", decimal: str = ".") -> Tuple[pd.DataFrame, Path]:
    """Load model allocation results aggregated by item_id."""
    if arquivo_resultado:
        csv_path = Path(arquivo_resultado)
    else:
        # Tentar primeiro arquivos resultado_realocacao_completo (nome padrão)
        candidatos = sorted(RESULTS_DIR.glob("resultado_realocacao_completo_*.csv"))
        if not candidatos:
            # Fallback para resultado_otimizacao (nome antigo)
            candidatos = sorted(RESULTS_DIR.glob("resultado_otimizacao_*.csv"))
        if not candidatos:
            raise FileNotFoundError(f"Nenhum resultado encontrado em {RESULTS_DIR.resolve()}")
        csv_path = candidatos[-1]

    df_aloc = pd.read_csv(csv_path)

    required_cols = {"item_id", "item", "embalagem", "classe", "quantidade"}
    if not required_cols.issubset(set(df_aloc.columns)):
        raise ValueError(
            f"Arquivo de alocacao incompatível: {csv_path}. "
            f"Colunas necessárias: {sorted(required_cols)}. "
            "Use um arquivo resultado_realocacao_*.csv (detalhado por item_id)."
        )
    df_aloc["item"] = pd.to_numeric(df_aloc["item"], errors="coerce")
    df_aloc = df_aloc[df_aloc["item"].notna()].copy()
    df_aloc["item"] = df_aloc["item"].astype(int)
    df_aloc["item_id"] = df_aloc["item_id"].astype(str)
    df_aloc["embalagem"] = df_aloc["embalagem"].astype(str)
    df_aloc["classe"] = df_aloc["classe"].fillna("OUTROS")

    # tipo=reserva: quantidade no resultado representa o pedido garantido (r_res),
    # não uma decisão do otimizador (x_aloc=0 por definição).
    # Zerar aqui para que quantidade_alocada reflita apenas alocação via otimização;
    # o volume reservado será mapeado em quantidade_reservada (via pedidos_clientes).
    if "tipo" in df_aloc.columns:
        df_aloc.loc[df_aloc["tipo"] == "reserva", "quantidade"] = 0

    agg_dict = {"quantidade": "sum"}
    if "tipo" in df_aloc.columns:
        agg_dict["tipo"] = "first"
    aloc_agg = (
        df_aloc.groupby(["item_id", "item", "embalagem", "classe"], as_index=False)
        .agg(agg_dict)
        .rename(columns={"quantidade": "quantidade_alocada"})
    )
    return aloc_agg, csv_path


def construir_comparacao(producao: pd.DataFrame, alocacao: pd.DataFrame, periodo_label: str, config: Optional[Dict] = None) -> pd.DataFrame:
    """Combine producao and alocacao by item_id to compare volumes."""
    comparacao = producao.merge(
        alocacao,
        on=["item_id", "item", "embalagem", "classe"],
        how="outer",
    )
    comparacao["quantidade_produzida"] = comparacao["quantidade_produzida"].fillna(0)
    comparacao["quantidade_alocada"] = comparacao["quantidade_alocada"].fillna(0)
    comparacao["diferenca_aloc_menos_prod"] = comparacao["quantidade_alocada"] - comparacao["quantidade_produzida"]
    comparacao["diferenca_absoluta"] = comparacao["diferenca_aloc_menos_prod"].abs()   

    comparacao["origem_dado"] = comparacao.apply(
        lambda row: "Somente alocacao"
        if row["quantidade_produzida"] == 0
        else ("Somente producao" if row["quantidade_alocada"] == 0 else "Ambos"),
        axis=1,
    )

    comparacao["periodo_label"] = periodo_label
    if "data_producao" not in comparacao.columns or comparacao["data_producao"].isna().any():
        # Preencher data_producao a partir do periodo_label da produção
        if "data_producao" in producao.columns:
            data_fill = producao["data_producao"].iloc[0] if len(producao) > 0 else pd.NaT
        else:
            data_fill = pd.NaT
        comparacao["data_producao"] = comparacao["data_producao"].fillna(data_fill)
    
    # Adicionar descrição dos itens
    comparacao = _adicionar_descricao(comparacao, config)

    return comparacao.sort_values("diferenca_absoluta", ascending=False).reset_index(drop=True)


def agregar_comparacao_por_item(
    comparacao: pd.DataFrame,
    lookup_item_id_por_item: Optional[Dict[Union[int, float], str]] = None,
) -> pd.DataFrame:
    """
    Agrega a comparação por item (SKU): uma linha por item com quantidades somadas.
    Evita duplicação quando o mesmo SKU aparece em várias linhas (ex.: item_id produzido + _RESERVA).
    Para itens que só têm linha _RESERVA, usa lookup_item_id_por_item (ex.: de preços/custos) para
    exibir item_id e embalagem no mesmo padrão dos demais SKUs; a distinção de reserva fica em quantidade_reservada.
    """
    if "item" not in comparacao.columns:
        return comparacao
    lookup = lookup_item_id_por_item or {}
    # Quantidades: soma produção e alocação; reserva é única por item -> max para não duplicar
    agg_dict = {
        "quantidade_produzida": "sum",
        "quantidade_alocada": "sum",
        "quantidade_reservada": "max",  # mesmo valor em todas as linhas do item
    }
    if "quantidade_nao_atendida_pedido" in comparacao.columns:
        # Déficit é único por SKU no resultado; usar max evita duplicidade após merge
        agg_dict["quantidade_nao_atendida_pedido"] = "max"
    # Colunas que mantemos com o primeiro valor não nulo (ou primeiro)
    for col in comparacao.columns:
        if col in agg_dict or col == "item":
            continue
        if col not in agg_dict:
            agg_dict[col] = "first"
    # Garantir que colunas numéricas de quantidade não viram "first"
    for k in ["quantidade_produzida", "quantidade_alocada", "quantidade_reservada", "quantidade_nao_atendida_pedido"]:
        if k in agg_dict and agg_dict[k] == "first":
            agg_dict[k] = "sum" if k not in ["quantidade_reservada", "quantidade_nao_atendida_pedido"] else "max"
    # Agrupar
    por_item = comparacao.groupby("item", as_index=False).agg(agg_dict)
    # Recalcular diferenças
    por_item["diferenca_aloc_menos_prod"] = por_item["quantidade_alocada"] - por_item["quantidade_produzida"]
    por_item["diferenca_absoluta"] = por_item["diferenca_aloc_menos_prod"].abs()
    # tipo: se misto (reserva + otimização) -> "misto"
    if "tipo" in comparacao.columns:
        tipos_por_item = comparacao.groupby("item")["tipo"].apply(lambda s: "misto" if s.nunique() > 1 else s.iloc[0])
        por_item["tipo"] = por_item["item"].map(tipos_por_item)
    # origem_dado: "Ambos" se há linha com produção e linha com alocação (ou alguma "Ambos")
    if "origem_dado" in comparacao.columns:
        def _origem_agg(s):
            if (s == "Ambos").any():
                return "Ambos"
            if (s == "Somente alocacao").any() and (s == "Somente producao").any():
                return "Ambos"
            if (s == "Somente alocacao").any():
                return "Somente alocacao"
            return "Somente producao"
        origem_por_item = comparacao.groupby("item")["origem_dado"].apply(_origem_agg)
        por_item["origem_dado"] = por_item["item"].map(origem_por_item)
    # item_id e embalagem: principal (não RESERVA); se só _RESERVA, buscar item_id/embalagem em lookup (ex.: preços/custos)
    if "item_id" in por_item.columns:
        def _item_id_principal(g):
            ids = g["item_id"].dropna().astype(str)
            nao_reserva = ids[~ids.str.endswith("_RESERVA")]
            if len(nao_reserva):
                return nao_reserva.iloc[0]
            # Item só tem linha(s) RESERVA: usar lookup para mesmo padrão dos demais SKUs (item_id com embalagem)
            item_val = g.name
            key = int(item_val) if pd.notna(item_val) else None
            return lookup.get(key, str(key) if key is not None else "")
        principal = comparacao.groupby("item", group_keys=False).apply(_item_id_principal, include_groups=False)
        por_item["item_id"] = por_item["item"].map(principal)
    if "embalagem" in por_item.columns:
        def _embalagem_principal(g):
            emb = g["embalagem"].dropna().astype(str)
            nao_reserva = emb[emb != "RESERVA"]
            if len(nao_reserva):
                return nao_reserva.iloc[0]
            # Item só RESERVA: derivar embalagem do lookup (item_id no formato item_embalagem)
            item_val = g.name
            key = int(item_val) if pd.notna(item_val) else None
            item_id_canon = lookup.get(key)
            if item_id_canon and "_" in str(item_id_canon):
                return str(item_id_canon).split("_", 1)[1]
            return "RESERVA"
        por_item["embalagem"] = comparacao.groupby("item", group_keys=False).apply(_embalagem_principal, include_groups=False).values
        por_item["embalagens"] = comparacao.groupby("item")["embalagem"].apply(
            lambda s: " | ".join(s.dropna().astype(str).unique())
        ).values
    por_item = por_item.sort_values("diferenca_absoluta", ascending=False).reset_index(drop=True)
    return por_item


def _adicionar_descricao(df: pd.DataFrame, config: Optional[Dict] = None) -> pd.DataFrame:
    """Adiciona coluna de descrição dos itens a partir da base de faturamento."""
    try:
        # Determinar caminho da base de faturamento
        if config:
            path_fat = Path(config.get('paths', {}).get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
        else:
            path_fat = Path('inputs/manti_fat_2025_full.parquet')
        
        if not path_fat.exists():
            df['descricao'] = None
            return df
        
        # Carregar descrições
        df_desc = pd.read_parquet(path_fat, columns=['item', 'Descrição do item'])
        df_desc = df_desc.drop_duplicates(subset=['item'])
        df_desc.columns = ['item', 'descricao']
        df_desc['item'] = df_desc['item'].astype(int)
        
        # Garantir que item é int
        df['item'] = df['item'].astype(int)
        
        # Merge
        df = df.merge(df_desc, on='item', how='left')
        
    except Exception as e:
        print(f"[AVISO] Erro ao carregar descrições: {e}")
        df['descricao'] = None
    
    return df


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compara producao por item (planilha CE0302) com a quantidade alocada pelo modelo "
            "de otimizacao, usando o mesmo tratamento de embalagem de exploration_tests.py."
        )
    )
    parser.add_argument(
        "--year-week",
        default=DEFAULT_YEAR_WEEK,
        help="Ano-semana ISO (ex.: 2026-07). Se vazio, usa semana_ref do config.yaml ou a última semana disponível.",
    )
    parser.add_argument(
        "--resultado",
        help="Caminho para um CSV de resultado_realocacao_completo_*.csv; padrao e o arquivo mais recente em resultados/.",
    )
    parser.add_argument(
        "--sep",
        default=",",
    )
    parser.add_argument(
        "--decimal",
        default=".",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    year_week = args.year_week or None

    config = _carregar_config()
    if year_week is None and config:
        year_week = config.get("dados", {}).get("semana_ref")
    producao, periodo_label = carregar_producao(year_week, config)
    alocacao, caminho_resultado = carregar_alocacao(args.resultado, sep=args.sep, decimal=args.decimal)
    precos = _carregar_precos(config)
    custos = _carregar_custos(config)
    
    # Mapeamentos por item (SKU) a partir dos arquivos externos - fallback quando item_id não bate
    precos_por_item_externo = {}
    custos_por_item_externo = {}
    if len(precos) > 0 and "item" in precos.columns and "preco" in precos.columns:
        precos_por_item_externo = precos.groupby("item")["preco"].first().to_dict()
    if len(custos) > 0 and "item" in custos.columns and "custo_ytd" in custos.columns:
        custos_por_item_externo = custos.groupby("item")["custo_ytd"].first().to_dict()
    
    # Carregar informações para mapeamento
    pedidos = _carregar_pedidos(config)
    skus_restritos = _carregar_skus_restritos(config)
    
    # Tentar carregar demanda histórica, limite_demanda_historica, margem_por_ovo e usa_custo_medio_classe do resultado do modelo
    # Usar o caminho já carregado em carregar_alocacao
    skus_com_demanda = set()
    usa_custo_medio_classe_por_item_id = {}  # item_id -> True/False
    limite_demanda_por_item = {}  # item -> limite_demanda_historica
    margem_por_ovo_por_item = {}  # item -> margem_por_ovo (R$/ovo - métrica otimizada)
    df_demanda_historica = pd.DataFrame(columns=["item", "descricao", "classe", "demanda_max"])
    df_param_demanda = pd.DataFrame(columns=["parametro", "valor"])
    deficit_pedido_por_item = {}
    try:
        df_aloc_completo = pd.read_csv(caminho_resultado)
        if 'tem_demanda_historica' in df_aloc_completo.columns and 'item' in df_aloc_completo.columns:
            # Extrair SKUs com demanda histórica do resultado
            df_aloc_completo['item'] = pd.to_numeric(df_aloc_completo['item'], errors='coerce')
            df_aloc_completo = df_aloc_completo[df_aloc_completo['item'].notna()].copy()
            df_aloc_completo['item'] = df_aloc_completo['item'].astype(int)
            skus_com_demanda = set(df_aloc_completo[df_aloc_completo['tem_demanda_historica'] == True]['item'].unique())
        
        # Extrair limite_demanda_historica por item (SKU)
        if 'limite_demanda_historica' in df_aloc_completo.columns and 'item' in df_aloc_completo.columns:
            # Pegar o primeiro valor de limite_demanda_historica para cada SKU (deve ser o mesmo para todas as embalagens)
            limite_demanda_por_item = df_aloc_completo.groupby('item')['limite_demanda_historica'].first().to_dict()
        
        # Extrair margem_por_ovo por item (SKU) - métrica otimizada pelo modelo
        if 'margem_por_ovo' in df_aloc_completo.columns and 'item' in df_aloc_completo.columns:
            # Pegar o primeiro valor de margem_por_ovo para cada SKU
            margem_por_ovo_por_item = df_aloc_completo.groupby('item')['margem_por_ovo'].first().to_dict()

        # Extrair déficit de pedido por item (pedido não atendido por falta de produção da classe)
        if 'deficit_pedido' in df_aloc_completo.columns and 'item' in df_aloc_completo.columns:
            df_aloc_completo['deficit_pedido'] = pd.to_numeric(df_aloc_completo['deficit_pedido'], errors='coerce').fillna(0.0)
            deficit_pedido_por_item = df_aloc_completo.groupby('item')['deficit_pedido'].max().to_dict()
        
        # Extrair usa_custo_medio_classe por item_id
        if 'usa_custo_medio_classe' in df_aloc_completo.columns and 'item_id' in df_aloc_completo.columns:
            df_aloc_completo['item_id'] = df_aloc_completo['item_id'].astype(str)
            usa_custo_medio_classe_por_item_id = df_aloc_completo.set_index('item_id')['usa_custo_medio_classe'].to_dict()
    except Exception:
        pass

    # Carregar demanda histórica completa (todos os SKUs com limite) do Excel demanda_historica_*.xlsx
    df_demanda_historica, df_param_demanda = _carregar_demanda_historica_completa(None)

    comparacao = construir_comparacao(producao, alocacao, periodo_label, config)
    
    # Garantir tipos numéricos nas colunas-chave (compatibilidade pandas 2.x)
    for col_num in ['preco', 'custo_ytd', 'margem_unitaria', 'margem_unitaria_cx360', 'margem_por_ovo', 'limite_demanda_historica']:
        if col_num in comparacao.columns:
            comparacao[col_num] = pd.to_numeric(comparacao[col_num], errors='coerce')
    
    # PRIMEIRO: Tentar usar preços e custos do arquivo de resultado do modelo (mais completo)
    # Isso garante que item_ids com alocação tenham preços/custos mesmo que não estejam nos arquivos externos
    precos_resultado = None
    custos_resultado = None
    precos_por_item = None  # Para fallback quando há mismatch de embalagem
    custos_por_item = None  # Para fallback quando há mismatch de embalagem
    df_aloc_completo = None  # DataFrame completo para verificação de consistência
    mapeamento_sku_item_id_alocado = {}  # item (SKU) -> item_id alocado (para mismatch)
    
    try:
        df_aloc_completo = pd.read_csv(caminho_resultado)
        df_aloc_completo['item'] = pd.to_numeric(df_aloc_completo['item'], errors='coerce')
        df_aloc_completo = df_aloc_completo[df_aloc_completo['item'].notna()].copy()
        df_aloc_completo['item'] = df_aloc_completo['item'].astype(int)
        
        # Criar mapeamento: para cada SKU, identificar qual item_id foi alocado
        # Isso ajuda quando há mismatch: produção em item_id A, alocação em item_id B
        # ESTRATÉGIA: Criar mapeamento SKU -> item_id_alocado
        # Quando buscar preço/custo para um item_id produzido que não está no resultado,
        # usar o item_id alocado do mesmo SKU
        mapeamento_sku_item_id_alocado = {}  # item (SKU) -> item_id alocado
        if 'item_id' in df_aloc_completo.columns and 'item' in df_aloc_completo.columns:
            df_aloc_completo['item_id'] = df_aloc_completo['item_id'].astype(str)
            # Para cada SKU, pegar o item_id que foi alocado (se houver)
            for item in df_aloc_completo['item'].unique():
                item_ids_alocados = df_aloc_completo[
                    (df_aloc_completo['item'] == item) & 
                    (df_aloc_completo['quantidade'] > 0)
                ]['item_id'].unique()
                if len(item_ids_alocados) > 0:
                    # Pegar o primeiro item_id alocado para este SKU
                    mapeamento_sku_item_id_alocado[item] = item_ids_alocados[0]
        
        if 'preco' in df_aloc_completo.columns and 'item_id' in df_aloc_completo.columns:
            df_aloc_completo['item_id'] = df_aloc_completo['item_id'].astype(str)
            precos_resultado = df_aloc_completo[['item_id', 'preco']].copy()
            precos_resultado = precos_resultado[precos_resultado['preco'].notna()].drop_duplicates('item_id')
            
            # Criar mapeamento por item (SKU) para fallback quando há mismatch de embalagem
            # Usar qualquer preço disponível do SKU (não apenas se todos forem iguais)
            precos_por_item = df_aloc_completo[['item', 'preco']].copy()
            precos_por_item = precos_por_item[precos_por_item['preco'].notna()]
            # Pegar o primeiro preço disponível para cada SKU
            precos_por_item = precos_por_item.groupby('item')['preco'].first().to_dict()
        
        if 'custo_ytd' in df_aloc_completo.columns and 'item_id' in df_aloc_completo.columns:
            df_aloc_completo['item_id'] = df_aloc_completo['item_id'].astype(str)
            custos_resultado = df_aloc_completo[['item_id', 'custo_ytd']].copy()
            custos_resultado = custos_resultado[custos_resultado['custo_ytd'].notna()].drop_duplicates('item_id')
            
            # Criar mapeamento por item (SKU) para fallback quando há mismatch de embalagem
            # Usar qualquer custo disponível do SKU (não apenas se todos forem iguais)
            custos_por_item = df_aloc_completo[['item', 'custo_ytd']].copy()
            custos_por_item = custos_por_item[custos_por_item['custo_ytd'].notna()]
            # Pegar o primeiro custo disponível para cada SKU
            custos_por_item = custos_por_item.groupby('item')['custo_ytd'].first().to_dict()
    except Exception as e:
        pass
    
    # Fazer merge: primeiro com resultado do modelo (resultado_realocacao_completo_*.csv),
    # depois com arquivos externos (fallback). O output do modelo contém TODOS os item_ids
    # que entraram na otimização (preco, custo_ytd, margem_unitaria, margem_por_ovo, etc.);
    # assim o comparador usa os mesmos valores que a otimização quando o item_id existe no resultado.
    # ESTRATÉGIA: 1) Merge por item_id no resultado | 2) Mismatch: item_id alocado do mesmo SKU
    # | 3) Arquivo externo por item_id | 4) Resultado por item (outra embalagem) | 5) Externo por item
    
    # Normalizar item_id (str, strip) para garantir match no merge com resultado
    comparacao["item_id"] = comparacao["item_id"].astype(str).str.strip()
    if precos_resultado is not None:
        precos_resultado["item_id"] = precos_resultado["item_id"].astype(str).str.strip()
    if custos_resultado is not None:
        custos_resultado["item_id"] = custos_resultado["item_id"].astype(str).str.strip()
    
    # Inicializar flags para rastrear origem dos dados
    comparacao['preco_origem'] = None
    comparacao['custo_origem'] = None
    
    if precos_resultado is not None and len(precos_resultado) > 0:
        # PASSO 1: Merge direto com item_id (resultado da otimização)
        precos_resultado_renamed = precos_resultado[["item_id", "preco"]].rename(columns={"preco": "preco_resultado"})
        comparacao = comparacao.merge(precos_resultado_renamed, on="item_id", how="left")
        # Preencher preço do resultado se não tiver
        if 'preco_resultado' in comparacao.columns:
            # Marcar origem antes de preencher
            mask_tem_preco_resultado = comparacao['preco_resultado'].notna()
            comparacao.loc[mask_tem_preco_resultado, 'preco_origem'] = 'item_id_produzido'
            comparacao['preco'] = comparacao.get('preco', pd.Series()).fillna(comparacao['preco_resultado'])
            comparacao = comparacao.drop(columns=['preco_resultado'])
        elif 'preco' not in comparacao.columns:
            comparacao['preco'] = None
        
        # PASSO 2: Para casos de mismatch, buscar do item_id alocado do mesmo SKU
        if len(mapeamento_sku_item_id_alocado) > 0:
            mask_sem_preco = comparacao['preco'].isna()
            precos_alocados = {}
            for idx in comparacao.loc[mask_sem_preco].index:
                item = comparacao.loc[idx, 'item']
                item_id_produzido = comparacao.loc[idx, 'item_id']
                if item in mapeamento_sku_item_id_alocado:
                    item_id_alocado = mapeamento_sku_item_id_alocado[item]
                    # Só usar se o item_id alocado for diferente do produzido (mismatch)
                    if item_id_alocado != item_id_produzido:
                        preco_alocado = precos_resultado[precos_resultado['item_id'] == item_id_alocado]['preco'].values
                        if len(preco_alocado) > 0:
                            precos_alocados[idx] = preco_alocado[0]
            
            if len(precos_alocados) > 0:
                comparacao.loc[list(precos_alocados.keys()), 'preco'] = pd.Series(precos_alocados)
                comparacao.loc[list(precos_alocados.keys()), 'preco_origem'] = 'item_id_alocado'
        
        # PASSO 3: Fallback para arquivo externo (por item_id)
        mask_sem_preco = comparacao['preco'].isna()
        if mask_sem_preco.any() and len(precos) > 0 and "item_id" in precos.columns:
            precos_merge = precos[["item_id", "preco"]].copy()
            precos_merge["item_id"] = precos_merge["item_id"].astype(str).str.strip()
            precos_merge = precos_merge.rename(columns={"preco": "preco_externo"})
            comparacao = comparacao.merge(precos_merge, on="item_id", how="left")
            preco_fill = comparacao.loc[mask_sem_preco, 'preco'].fillna(
                comparacao.loc[mask_sem_preco, 'preco_externo']
            )
            comparacao.loc[mask_sem_preco, 'preco'] = preco_fill.infer_objects(copy=False)
            # Só marcar arquivo_externo onde ainda não tem origem (preservar item_id_produzido)
            mask_set = mask_sem_preco & comparacao["preco_externo"].notna() & comparacao["preco_origem"].isna()
            comparacao.loc[mask_set, "preco_origem"] = "arquivo_externo"
            comparacao = comparacao.drop(columns=["preco_externo"], errors="ignore")
        
        # PASSO 4: Fallback - usar qualquer preço disponível do mesmo SKU (resultado do modelo)
        if precos_por_item is not None:
            mask_sem_preco = comparacao['preco'].isna()
            if mask_sem_preco.any():
                comparacao.loc[mask_sem_preco, 'preco'] = comparacao.loc[mask_sem_preco, 'item'].map(precos_por_item)
                comparacao.loc[
                    (mask_sem_preco) & (comparacao['preco'].notna()), 
                    'preco_origem'
                ] = 'mesmo_sku_outra_embalagem'
        # PASSO 5: Fallback final - preço por item do arquivo externo (input de preços)
        if len(precos_por_item_externo) > 0:
            mask_sem_preco = comparacao['preco'].isna()
            if mask_sem_preco.any():
                comparacao.loc[mask_sem_preco, 'preco'] = comparacao.loc[mask_sem_preco, 'item'].map(precos_por_item_externo)
                comparacao.loc[
                    (mask_sem_preco) & (comparacao['preco'].notna()), 
                    'preco_origem'
                ] = 'arquivo_externo_por_item'
    else:
        # Se não tem preços do resultado, usar arquivo externo (por item_id e depois por item)
        comparacao = comparacao.merge(precos[["item_id", "preco"]], on="item_id", how="left")
        if "preco" in comparacao.columns:
            comparacao.loc[comparacao["preco"].notna(), "preco_origem"] = "arquivo_externo"
        else:
            comparacao["preco"] = None
        if len(precos_por_item_externo) > 0:
            mask_sem_preco = comparacao["preco"].isna()
            if mask_sem_preco.any():
                comparacao.loc[mask_sem_preco, "preco"] = comparacao.loc[mask_sem_preco, "item"].map(precos_por_item_externo)
                comparacao.loc[(mask_sem_preco) & (comparacao["preco"].notna()), "preco_origem"] = "arquivo_externo_por_item"
    
    if custos_resultado is not None and len(custos_resultado) > 0:
        # PASSO 1: Merge direto com item_id produzido
        # Renomear coluna do resultado para evitar conflito
        custos_resultado_renamed = custos_resultado.rename(columns={'custo_ytd': 'custo_ytd_resultado'})
        comparacao = comparacao.merge(custos_resultado_renamed, on="item_id", how="left")
        # Preencher custo do resultado se não tiver
        if 'custo_ytd_resultado' in comparacao.columns:
            # Marcar origem antes de preencher
            mask_tem_custo_resultado = comparacao['custo_ytd_resultado'].notna()
            comparacao.loc[mask_tem_custo_resultado, 'custo_origem'] = 'item_id_produzido'
            comparacao['custo_ytd'] = comparacao.get('custo_ytd', pd.Series()).fillna(comparacao['custo_ytd_resultado'])
            comparacao = comparacao.drop(columns=['custo_ytd_resultado'])
        elif 'custo_ytd' not in comparacao.columns:
            comparacao['custo_ytd'] = None
        
        # PASSO 2: Para casos de mismatch, buscar do item_id alocado do mesmo SKU
        if len(mapeamento_sku_item_id_alocado) > 0:
            mask_sem_custo = comparacao['custo_ytd'].isna()
            custos_alocados = {}
            for idx in comparacao.loc[mask_sem_custo].index:
                item = comparacao.loc[idx, 'item']
                item_id_produzido = comparacao.loc[idx, 'item_id']
                if item in mapeamento_sku_item_id_alocado:
                    item_id_alocado = mapeamento_sku_item_id_alocado[item]
                    # Só usar se o item_id alocado for diferente do produzido (mismatch)
                    if item_id_alocado != item_id_produzido:
                        custo_alocado = custos_resultado[custos_resultado['item_id'] == item_id_alocado]['custo_ytd'].values
                        if len(custo_alocado) > 0:
                            custos_alocados[idx] = custo_alocado[0]
            
            if len(custos_alocados) > 0:
                comparacao.loc[list(custos_alocados.keys()), 'custo_ytd'] = pd.Series(custos_alocados)
                comparacao.loc[list(custos_alocados.keys()), 'custo_origem'] = 'item_id_alocado'
        
        # PASSO 3: Fallback para arquivo externo se ainda não tiver custo
        mask_sem_custo = comparacao['custo_ytd'].isna()
        if mask_sem_custo.any():
            comparacao = comparacao.merge(custos[["item_id", "custo_ytd"]], on="item_id", how="left", suffixes=('', '_externo'))
            custo_fill = comparacao.loc[mask_sem_custo, 'custo_ytd'].fillna(
                comparacao.loc[mask_sem_custo, 'custo_ytd_externo']
            )
            comparacao.loc[mask_sem_custo, 'custo_ytd'] = custo_fill.infer_objects(copy=False)
            # Só marcar arquivo_externo onde ainda não tem origem (preservar item_id_produzido)
            mask_set = mask_sem_custo & comparacao["custo_ytd_externo"].notna() & comparacao["custo_origem"].isna()
            comparacao.loc[mask_set, "custo_origem"] = "arquivo_externo"
            comparacao = comparacao.drop(columns=["custo_ytd_externo"], errors="ignore")
        
        # PASSO 4: Fallback - usar qualquer custo disponível do mesmo SKU (resultado do modelo)
        if custos_por_item is not None:
            mask_sem_custo = comparacao['custo_ytd'].isna()
            if mask_sem_custo.any():
                comparacao.loc[mask_sem_custo, 'custo_ytd'] = comparacao.loc[mask_sem_custo, 'item'].map(custos_por_item)
                comparacao.loc[
                    (mask_sem_custo) & (comparacao['custo_ytd'].notna()), 
                    'custo_origem'
                ] = 'mesmo_sku_outra_embalagem'
        # PASSO 5: Fallback final - custo por item do arquivo externo (input de custos)
        if len(custos_por_item_externo) > 0:
            mask_sem_custo = comparacao['custo_ytd'].isna()
            if mask_sem_custo.any():
                comparacao.loc[mask_sem_custo, 'custo_ytd'] = comparacao.loc[mask_sem_custo, 'item'].map(custos_por_item_externo)
                comparacao.loc[
                    (mask_sem_custo) & (comparacao['custo_ytd'].notna()), 
                    'custo_origem'
                ] = 'arquivo_externo_por_item'
    else:
        # Se não tem custos do resultado, usar arquivo externo (por item_id e depois por item)
        comparacao = comparacao.merge(custos[["item_id", "custo_ytd"]], on="item_id", how="left")
        if "custo_ytd" in comparacao.columns:
            comparacao.loc[comparacao["custo_ytd"].notna(), "custo_origem"] = "arquivo_externo"
        else:
            comparacao["custo_ytd"] = None
        if len(custos_por_item_externo) > 0:
            mask_sem_custo = comparacao["custo_ytd"].isna()
            if mask_sem_custo.any():
                comparacao.loc[mask_sem_custo, "custo_ytd"] = comparacao.loc[mask_sem_custo, "item"].map(custos_por_item_externo)
                comparacao.loc[(mask_sem_custo) & (comparacao["custo_ytd"].notna()), "custo_origem"] = "arquivo_externo_por_item"
    
    # Calcular margem unitária (em R$/CX360, base normalizada)
    comparacao["margem_unitaria"] = comparacao["preco"] - comparacao["custo_ytd"]
    # Alias explícito de unidade para output (mantém compatibilidade com coluna legada)
    comparacao["margem_unitaria_cx360"] = comparacao["margem_unitaria"]
    
    # ==========================================================================
    # ADICIONAR SKUs COM PEDIDO MAS SEM PRODUÇÃO NA SEMANA
    # Esses SKUs foram reservados pelo modelo mas não aparecem na produção
    # ==========================================================================
    if len(pedidos) > 0:
        skus_em_comparacao = set(comparacao['item'].unique())
        skus_com_pedido = set(pedidos['item'].tolist())
        skus_pedido_sem_producao = skus_com_pedido - skus_em_comparacao
        
        if len(skus_pedido_sem_producao) > 0:
            print(f"\n[INFO] Adicionando {len(skus_pedido_sem_producao)} SKUs com pedido mas sem produção na semana")
            
            # Carregar classes para mapear esses SKUs
            if config:
                classes_path = _resolver_caminho(config, "classes", INPUT_PATH / "base_skus_classes.xlsx")
            else:
                classes_path = INPUT_PATH / "base_skus_classes.xlsx"
            
            try:
                df_classes = pd.read_excel(Path(classes_path))
                col_classe = _resolver_coluna_classe(df_classes)
                if col_classe:
                    classes_dict = df_classes.set_index('item')[col_classe].to_dict()
                else:
                    classes_dict = {}
            except Exception:
                classes_dict = {}
            
            # Criar linhas para SKUs com pedido mas sem produção
            novas_linhas = []
            for item in skus_pedido_sem_producao:
                qtd_pedida = pedidos[pedidos['item'] == item]['quantidade_total_pedida'].iloc[0]
                classe = classes_dict.get(item, 'SEM_CLASSE')
                
                nova_linha = {
                    'item_id': f"{item}_SEM_PRODUCAO",
                    'item': item,
                    'descricao': None,  # Será preenchido depois
                    'embalagem': None,
                    'classe': classe,
                    'periodo_label': periodo_label,
                    'data_producao': producao['data_producao'].iloc[0] if len(producao) > 0 else pd.NaT,
                    'quantidade_produzida': 0,
                    'quantidade_alocada': 0,
                    'diferenca_aloc_menos_prod': 0,
                    'diferenca_absoluta': 0,
                    'origem_dado': 'Somente pedido',
                    'preco': None,
                    'custo_ytd': None,
                    'margem_unitaria': None,
                    'preco_origem': None,
                    'custo_origem': None,
                }
                novas_linhas.append(nova_linha)
                print(f"    SKU {item} ({classe}): {qtd_pedida:,.0f} ovos (reserva sem produção)")
            
            # Concatenar ao DataFrame de comparação
            if novas_linhas:
                df_novas = pd.DataFrame(novas_linhas)
                comparacao = pd.concat([comparacao, df_novas], ignore_index=True)
                
                # Preencher descrição para os novos SKUs (sem duplicar coluna)
                # Carregar descrições do faturamento
                try:
                    if config:
                        path_fat = Path(config.get('paths', {}).get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
                    else:
                        path_fat = Path('inputs/manti_fat_2025_full.parquet')
                    
                    if path_fat.exists():
                        df_desc = pd.read_parquet(path_fat, columns=['item', 'Descrição do item'])
                        df_desc = df_desc.drop_duplicates(subset=['item'])
                        df_desc.columns = ['item', 'descricao_temp']
                        df_desc['item'] = df_desc['item'].astype(int)
                        
                        # Mapear descrição apenas para linhas com descricao nula
                        desc_dict = df_desc.set_index('item')['descricao_temp'].to_dict()
                        mask_sem_desc = comparacao['descricao'].isna()
                        comparacao.loc[mask_sem_desc, 'descricao'] = comparacao.loc[mask_sem_desc, 'item'].map(desc_dict)
                except Exception as e:
                    print(f"[AVISO] Erro ao preencher descrições: {e}")
    
    # Identificar pedidos ignorados
    # Pedidos ignorados = pedidos que NÃO foram atendidos de nenhuma forma:
    #   - NÃO estão na alocação da otimização
    #   - NÃO foram reservados (SKUs sem classe mapeada)
    # NOTA: SKUs de GRANEL/Exportação que tiveram quantidade reservada NÃO são ignorados
    # NOTA: SKUs sem produção mas com pedido também NÃO são ignorados (foram adicionados acima)
    pedidos_ignorados = []
    if len(pedidos) > 0:
        # SKUs atendidos via alocação (otimização)
        pedidos_atendidos_alocacao = set(alocacao['item'].unique())
        
        # SKUs atendidos via reserva (aparecem na comparação - inclui os sem produção adicionados acima)
        skus_em_comparacao = set(comparacao['item'].unique())
        skus_em_producao = set(producao['item'].unique())
        
        # Identificar pedidos realmente ignorados
        for _, row in pedidos.iterrows():
            item = row['item']
            em_alocacao = item in pedidos_atendidos_alocacao
            em_comparacao = item in skus_em_comparacao  # Inclui SKUs sem produção que foram adicionados
            
            # Pedido é ignorado se NÃO foi atendido de nenhuma forma
            # SKUs sem produção mas com classe mapeada foram adicionados à comparação
            if not em_alocacao and not em_comparacao:
                pedidos_ignorados.append({
                    'item': item,
                    'quantidade_total_pedida': row['quantidade_total_pedida'],
                    'motivo': 'SKU sem classe mapeada'
                })
    
    # Adicionar colunas de mapeamento
    # 1. tem_pedido: SKU tem pedido
    # 2. quantidade_reservada: quantidade pedida/reservada para o SKU
    if len(pedidos) > 0:
        skus_com_pedido = set(pedidos['item'].tolist())
        pedidos_dict = pedidos.set_index('item')['quantidade_total_pedida'].to_dict()
        comparacao["tem_pedido"] = comparacao["item"].isin(skus_com_pedido)
        comparacao["quantidade_reservada"] = comparacao["item"].map(pedidos_dict).fillna(0)
        comparacao["quantidade_nao_atendida_pedido"] = comparacao["item"].map(deficit_pedido_por_item).fillna(0.0)
        
        # Campos enriquecidos da carteira vendida
        pedidos_idx = pedidos.set_index('item')
        for col_extra in ['preco_pedido', 'data_entrega_min', 'data_entrega_max',
                          'n_pedidos', 'n_clientes', 'qt_pedida_caixas']:
            if col_extra in pedidos_idx.columns:
                comparacao[col_extra] = comparacao["item"].map(pedidos_idx[col_extra].to_dict())
    else:
        comparacao["tem_pedido"] = False
        comparacao["quantidade_reservada"] = 0
        comparacao["quantidade_nao_atendida_pedido"] = 0.0
    
    # 3. pedido_ignorado: SKU tem pedido que foi ignorado
    if len(pedidos_ignorados) > 0:
        skus_com_pedido_ignorado = set([p['item'] for p in pedidos_ignorados])
        comparacao["pedido_ignorado"] = comparacao["item"].isin(skus_com_pedido_ignorado)
    else:
        comparacao["pedido_ignorado"] = False
    
    # 4. tipo: Indica origem da alocação (compatível com otimizador.py)
    # - 'otimizacao': SKU foi alocado pela otimização
    # - 'reserva': SKU teve volume reservado (pedido garantido) no modelo
    # - 'reserva_sem_producao': SKU teve pedido/reserva MAS sem produção na semana
    # - 'producao': SKU só tem produção, sem alocação nem reserva
    # Quando o CSV do modelo traz coluna 'tipo' (ex.: 'reserva'), preservar; senão inferir.
    def determinar_tipo(row):
        if row['quantidade_alocada'] > 0:
            return 'otimizacao'
        elif row['quantidade_reservada'] > 0:
            if row['quantidade_produzida'] > 0:
                return 'reserva'
            else:
                return 'reserva_sem_producao'
        elif row['quantidade_produzida'] > 0:
            return 'producao'
        else:
            return 'outro'
    
    if 'tipo' in comparacao.columns:
        comparacao['tipo'] = comparacao['tipo'].fillna(comparacao.apply(determinar_tipo, axis=1))
    else:
        comparacao['tipo'] = comparacao.apply(determinar_tipo, axis=1)
    
    # 5. sku_restrito: SKU NÃO está na lista de permitidos (portanto é restrito)
    # NOVA SEMÂNTICA: lista contém SKUs PERMITIDOS para o estabelecimento
    # Quem está na lista é permitido (sku_restrito=False)
    # Quem NÃO está na lista é restrito (sku_restrito=True)
    if len(skus_restritos) > 0:
        skus_permitidos_set = set(skus_restritos['item'].tolist())
        comparacao["sku_restrito"] = ~comparacao["item"].isin(skus_permitidos_set)
    else:
        # Se não há lista de permitidos, ninguém é restrito
        comparacao["sku_restrito"] = False
    
    # 3 e 4. Demanda histórica: usar resultado do modelo + arquivo demanda_historica_*.xlsx (todos os SKUs considerados)
    demanda_max_por_item = {}
    if len(df_demanda_historica) > 0 and "item" in df_demanda_historica.columns and "demanda_max" in df_demanda_historica.columns:
        demanda_max_por_item = df_demanda_historica.groupby("item")["demanda_max"].first().to_dict()
    # limite_demanda_historica: prioridade resultado do modelo, depois demanda_historica xlsx
    comparacao["limite_demanda_historica"] = comparacao["item"].map(limite_demanda_por_item)
    if len(demanda_max_por_item) > 0:
        falta = comparacao["limite_demanda_historica"].isna()
        comparacao.loc[falta, "limite_demanda_historica"] = comparacao.loc[falta, "item"].map(demanda_max_por_item)
    # demanda_max: mesma informação (para consistência com aba Demanda Histórica)
    comparacao["demanda_max"] = comparacao["limite_demanda_historica"]
    # tem_demanda_historica: True se está no resultado com flag ou se tem demanda_max no arquivo
    comparacao["tem_demanda_historica"] = comparacao["item"].isin(skus_com_demanda)
    if len(demanda_max_por_item) > 0:
        comparacao["tem_demanda_historica"] = comparacao["tem_demanda_historica"] | comparacao["item"].isin(demanda_max_por_item)

    # Parâmetros do cálculo de demanda histórica (mesmo valor para todas as linhas; origem: aba Parametros do demanda_historica_*.xlsx)
    if len(df_param_demanda) > 0 and "parametro" in df_param_demanda.columns and "valor" in df_param_demanda.columns:
        param_valor = df_param_demanda.set_index("parametro")["valor"].to_dict()
        for col in ["periodo_demanda_mes_ref", "periodo_demanda_ano_ref", "periodo_demanda_janela_meses", "tipo_calculo_demanda", "granularidade_demanda"]:
            comparacao[col] = param_valor.get(col, None)
    else:
        for col in ["periodo_demanda_mes_ref", "periodo_demanda_ano_ref", "periodo_demanda_janela_meses", "tipo_calculo_demanda", "granularidade_demanda"]:
            comparacao[col] = None
    
    # 5. margem_por_ovo: Margem por ovo (R$/ovo) - do resultado do modelo quando disponível
    # Primeiro: por item_id (valor exato do output da otimização); depois por item (outra embalagem)
    comparacao["margem_por_ovo"] = None
    if df_aloc_completo is not None and len(df_aloc_completo) > 0 and "margem_por_ovo" in df_aloc_completo.columns and "item_id" in df_aloc_completo.columns:
        margem_por_item_id = df_aloc_completo[["item_id", "margem_por_ovo"]].drop_duplicates("item_id").set_index("item_id")["margem_por_ovo"]
        comparacao["margem_por_ovo"] = comparacao["item_id"].astype(str).map(margem_por_item_id)
    if len(margem_por_ovo_por_item) > 0:
        mask_sem = comparacao["margem_por_ovo"].isna()
        comparacao['margem_por_ovo'] = comparacao['margem_por_ovo'].astype('float64')
        comparacao.loc[mask_sem, "margem_por_ovo"] = comparacao.loc[mask_sem, "item"].map(margem_por_ovo_por_item).astype('float64')
    
    # Segundo: calcular margem_por_ovo para SKUs que não têm (baseado na embalagem ou descrição)
    # Função para extrair ovos por caixa da embalagem (ex: "CX 12 BJ 20 UN" = 240; "30 DZ" = 360)
    def extrair_ovos_por_caixa(embalagem):
        if pd.isna(embalagem):
            return None
        import re
        s = str(embalagem).strip()
        # Padrão: "CX X BJ Y UN" onde X = bandejas, Y = ovos por bandeja
        match = re.search(r'CX\s*(?:C?/?\s*)?(\d+)\s*(?:BJ|BK)\s*(\d+)', s, re.IGNORECASE)
        if match:
            bandejas = int(match.group(1))
            ovos_por_bandeja = int(match.group(2))
            return bandejas * ovos_por_bandeja
        # Padrão: "X DZ" ou "CX C/ X DZ" (dúzias) -> 12 ovos por dúzia
        match_dz = re.search(r'(?:CX\s*C?/?\s*)?(\d+)\s*DZ', s, re.IGNORECASE)
        if match_dz:
            return int(match_dz.group(1)) * 12
        return None
    
    # Calcular para registros sem margem_por_ovo mas com margem_unitaria
    sem_margem_ovo = comparacao['margem_por_ovo'].isna() & comparacao['margem_unitaria'].notna()
    if sem_margem_ovo.sum() > 0:
        def _ovos_para_linha(row):
            emb = row.get('embalagem')
            ovos = extrair_ovos_por_caixa(emb)
            if ovos is not None:
                return ovos
            # Embalagem RESERVA: tentar extrair da descrição (ex.: "SANTA CLARA CX C/30 DZ 300 UN")
            if emb == "RESERVA" and 'descricao' in row.index:
                emb_desc = extrair_embalagem_descricao(row['descricao']) if pd.notna(row.get('descricao')) else None
                ovos = extrair_ovos_por_caixa(emb_desc) if emb_desc else None
                if ovos is not None:
                    return ovos
            return None
        ovos_por_caixa = comparacao.loc[sem_margem_ovo].apply(_ovos_para_linha, axis=1)
        # Garantir que a coluna aceita float (compatibilidade pandas 2.x)
        comparacao['margem_por_ovo'] = comparacao['margem_por_ovo'].astype('float64')
        comparacao.loc[sem_margem_ovo, 'margem_por_ovo'] = (comparacao.loc[sem_margem_ovo, 'margem_unitaria'] / ovos_por_caixa).astype('float64')
    
    # 6. usa_custo_medio_classe: Custo foi calculado usando média da classe
    if len(usa_custo_medio_classe_por_item_id) > 0:
        comparacao["usa_custo_medio_classe"] = comparacao["item_id"].map(usa_custo_medio_classe_por_item_id).fillna(False).infer_objects(copy=False)
    else:
        comparacao["usa_custo_medio_classe"] = False
    
    # Estatísticas de margem
    print("\n[INFO] Estatísticas de margem na comparação:")
    total_item_id = len(comparacao)
    com_preco = comparacao["preco"].notna().sum()
    com_custo = comparacao["custo_ytd"].notna().sum()
    com_ambos = comparacao[comparacao["preco"].notna() & comparacao["custo_ytd"].notna()]
    com_margem_positiva = (com_ambos["margem_unitaria"] > 0).sum() if len(com_ambos) > 0 else 0
    com_margem_negativa = (com_ambos["margem_unitaria"] < 0).sum() if len(com_ambos) > 0 else 0
    
    print(f"  Total de item_id: {total_item_id:,}")
    print(f"  Com preço: {com_preco:,} ({com_preco/total_item_id*100:.1f}%)")
    print(f"  Com custo: {com_custo:,} ({com_custo/total_item_id*100:.1f}%)")
    print(f"  Com ambos (preço + custo): {len(com_ambos):,} ({len(com_ambos)/total_item_id*100:.1f}%)")
    if len(com_ambos) > 0:
        print(f"  Com margem positiva: {com_margem_positiva:,} ({com_margem_positiva/len(com_ambos)*100:.1f}%)")
        print(f"  Com margem negativa: {com_margem_negativa:,} ({com_margem_negativa/len(com_ambos)*100:.1f}%)")
        print(f"  Margem média: R$ {com_ambos['margem_unitaria'].mean():.2f}")
        print(f"  Margem mediana: R$ {com_ambos['margem_unitaria'].median():.2f}")
    
    # Estatísticas de origem dos dados (para identificar mismatch de embalagem)
    if 'preco_origem' in comparacao.columns:
        print("\n[INFO] Origem dos preços:")
        origem_preco = comparacao['preco_origem'].value_counts()
        for origem, count in origem_preco.items():
            if pd.notna(origem):
                print(f"  {origem}: {count:,} ({count/com_preco*100:.1f}%)" if com_preco > 0 else f"  {origem}: {count:,}")
        sem_preco = comparacao['preco_origem'].isna().sum()
        if sem_preco > 0:
            print(f"  Sem origem (NaN): {sem_preco:,}")
    
    if 'custo_origem' in comparacao.columns:
        print("\n[INFO] Origem dos custos:")
        origem_custo = comparacao['custo_origem'].value_counts()
        for origem, count in origem_custo.items():
            if pd.notna(origem):
                print(f"  {origem}: {count:,} ({count/com_custo*100:.1f}%)" if com_custo > 0 else f"  {origem}: {count:,}")
        sem_custo = comparacao['custo_origem'].isna().sum()
        if sem_custo > 0:
            print(f"  Sem origem (NaN): {sem_custo:,}")
    
    # Estatísticas de pedidos ignorados
    if len(pedidos_ignorados) > 0:
        total_ignorado = sum(p['quantidade_total_pedida'] for p in pedidos_ignorados)
        print(f"\n[INFO] Pedidos ignorados:")
        print(f"  SKUs com pedidos ignorados: {len(pedidos_ignorados)}")
        print(f"  Quantidade total ignorada: {total_ignorado:,.0f} unidades")
        skus_sem_producao = len([p for p in pedidos_ignorados if 'nao encontrado em producao' in p['motivo']])
        print(f"  SKUs sem producao: {skus_sem_producao}")
        print(f"  Outros motivos: {len(pedidos_ignorados) - skus_sem_producao}")

    RESULTS_DIR.mkdir(exist_ok=True)
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path_csv = RESULTS_DIR / f"comparacao_producao_alocacao_{periodo_label}_{timestamp}.csv"
    output_path_xlsx = RESULTS_DIR / f"comparacao_producao_alocacao_{periodo_label}_{timestamp}.xlsx"
    
    # Reordenar colunas por categoria lógica para facilitar leitura
    # 1. Identificação | 2. Período | 3. Quantidades | 4. Financeiro unitário
    # 5. Restrições | 6. Flags/Status | 7. Origens dos dados
    colunas_ordenadas = [
        # === IDENTIFICAÇÃO ===
        'item_id', 'item', 'descricao', 'embalagem', 'classe',
        # === PERÍODO ===
        'periodo_label', 'data_producao',
        # === QUANTIDADES ===
        'quantidade_produzida', 'quantidade_alocada', 'quantidade_reservada', 'quantidade_nao_atendida_pedido', 'diferenca_aloc_menos_prod', 'diferenca_absoluta',
        # === FINANCEIRO UNITÁRIO ===
        'preco', 'custo_ytd', 'margem_unitaria', 'margem_unitaria_cx360', 'margem_por_ovo',
        # === DEMANDA HISTÓRICA ===
        'tem_demanda_historica', 'demanda_max', 'limite_demanda_historica',
        'periodo_demanda_mes_ref', 'periodo_demanda_ano_ref', 'periodo_demanda_janela_meses',
        'tipo_calculo_demanda', 'granularidade_demanda',
        # === PEDIDOS (CARTEIRA VENDIDA) ===
        'preco_pedido', 'data_entrega_min', 'data_entrega_max',
        'n_pedidos', 'n_clientes', 'qt_pedida_caixas',
        # === FLAGS / STATUS ===
        'tipo', 'origem_dado', 'tem_pedido', 'pedido_ignorado', 'sku_restrito',
        'usa_custo_medio_classe',
        # === ORIGENS DOS DADOS ===
        'preco_origem', 'custo_origem',
    ]
    # Manter apenas colunas que existem e adicionar outras ao final
    colunas_existentes = [c for c in colunas_ordenadas if c in comparacao.columns]
    colunas_restantes = [c for c in comparacao.columns if c not in colunas_ordenadas]
    comparacao = comparacao[colunas_existentes + colunas_restantes]
    
    # Lookup item -> item_id (com embalagem) para itens que só aparecem como _RESERVA: preços, custos e resultado
    # para exibir item_id/embalagem no mesmo padrão dos demais SKUs (evita item_id com _RESERVA quando há fonte)
    lookup_item_id_por_item = {}
    if len(precos) > 0 and "item_id" in precos.columns and "item" in precos.columns:
        df_precos_id = precos[~precos["item_id"].astype(str).str.endswith("_RESERVA")].copy()
        df_precos_id["item"] = pd.to_numeric(df_precos_id["item"], errors="coerce")
        df_precos_id = df_precos_id[df_precos_id["item"].notna()].drop_duplicates("item", keep="first")
        lookup_item_id_por_item = df_precos_id.set_index("item")["item_id"].astype(str).to_dict()
        lookup_item_id_por_item = {int(k): v for k, v in lookup_item_id_por_item.items()}
    if len(custos) > 0 and "item_id" in custos.columns and "item" in custos.columns:
        df_custos_id = custos[~custos["item_id"].astype(str).str.endswith("_RESERVA")].copy()
        df_custos_id["item"] = pd.to_numeric(df_custos_id["item"], errors="coerce")
        df_custos_id = df_custos_id[df_custos_id["item"].notna()].drop_duplicates("item", keep="first")
        custos_por_item = df_custos_id.set_index("item")["item_id"].astype(str).to_dict()
        custos_por_item = {int(k): v for k, v in custos_por_item.items()}
        for k, v in custos_por_item.items():
            lookup_item_id_por_item.setdefault(k, v)
    # Completar com resultado do modelo (item_id não _RESERVA) para itens que tenham preço/custo mas não estavam em preços/custos
    if df_aloc_completo is not None and len(df_aloc_completo) > 0 and "item_id" in df_aloc_completo.columns and "item" in df_aloc_completo.columns:
        df_aloc_completo["item_id"] = df_aloc_completo["item_id"].astype(str)
        df_nao_reserva = df_aloc_completo[~df_aloc_completo["item_id"].str.endswith("_RESERVA")]
        if len(df_nao_reserva) > 0:
            df_nao_reserva = df_nao_reserva.drop_duplicates("item", keep="first")
            for _, row in df_nao_reserva.iterrows():
                it = row.get("item")
                if pd.notna(it):
                    k = int(it)
                    lookup_item_id_por_item.setdefault(k, row["item_id"])
    
    # Uma única visão: uma linha por item (SKU), com item_id e embalagem principais e coluna quantidade_reservada
    # (volume reservado fica explícito na coluna; evita linhas duplicadas 2000885 + 2000885_RESERVA)
    comparacao = agregar_comparacao_por_item(comparacao, lookup_item_id_por_item=lookup_item_id_por_item)

    # Recalcular margem_por_ovo após agregação para linhas que têm preço/custo mas ficaram sem (ex.: embalagem RESERVA)
    # (usa "embalagens" internamente; coluna "embalagens" é removida do output abaixo)
    def _ovos_pos_agg(row):
        ovos = extrair_ovos_por_caixa(row.get("embalagem")) if "embalagem" in row.index else None
        if ovos is not None:
            return ovos
        if row.get("embalagem") == "RESERVA" and "embalagens" in row.index and pd.notna(row.get("embalagens")):
            for part in str(row["embalagens"]).split("|"):
                part = part.strip()
                if part and part != "RESERVA":
                    ovos = extrair_ovos_por_caixa(part)
                    if ovos is not None:
                        return ovos
        if "descricao" in row.index and pd.notna(row.get("descricao")):
            emb_desc = extrair_embalagem_descricao(row["descricao"])
            return extrair_ovos_por_caixa(emb_desc) if emb_desc else None
        return None
    sem_margem = comparacao["margem_por_ovo"].isna() & comparacao["margem_unitaria"].notna()
    if sem_margem.sum() > 0:
        ovos_pos = comparacao.loc[sem_margem].apply(_ovos_pos_agg, axis=1)
        comparacao['margem_por_ovo'] = comparacao['margem_por_ovo'].astype('float64')
        valores = (comparacao.loc[sem_margem, "margem_unitaria"] / ovos_pos).astype('float64')
        comparacao.loc[sem_margem, "margem_por_ovo"] = valores

    # Adicionar SKUs ativos do estabelecimento que não aparecem no output (sem produção, sem pedido, não otimizados)
    # para garantir que o output contenha TODOS os SKUs ativos do estabelecimento
    skus_ativos_completo = _carregar_skus_ativos_completo(config)
    if len(skus_ativos_completo) > 0:
        skus_no_output = set(comparacao['item'].astype(int).unique())
        skus_ativos_set = set(skus_ativos_completo['item'].astype(int).unique())
        skus_ausentes = skus_ativos_set - skus_no_output
        
        if len(skus_ausentes) > 0:
            print(f"\n[INFO] Adicionando {len(skus_ausentes)} SKUs ativos que não apareceram no output:")
            
            # Carregar classes para mapear SKU -> classe
            classes_path = _resolver_caminho(config, "classes", INPUT_PATH / "base_skus_classes.xlsx")
            classe_por_item = {}
            if classes_path.exists():
                try:
                    df_classes = pd.read_excel(classes_path)
                    col_classe = _resolver_coluna_classe(df_classes)
                    if col_classe and 'item' in df_classes.columns:
                        df_classes['item'] = pd.to_numeric(df_classes['item'], errors='coerce')
                        df_classes = df_classes[df_classes['item'].notna()]
                        df_classes['item'] = df_classes['item'].astype(int)
                        classe_por_item = df_classes.set_index('item')[col_classe].to_dict()
                except Exception:
                    pass
            
            # Criar linhas para SKUs ausentes
            linhas_ausentes = []
            for sku in sorted(skus_ausentes):
                # Buscar informações do cadastro
                info_cadastro = skus_ativos_completo[skus_ativos_completo['item'] == sku]
                descricao_cadastro = info_cadastro['descricao_cadastro'].iloc[0] if len(info_cadastro) > 0 and pd.notna(info_cadastro['descricao_cadastro'].iloc[0]) else None
                tipo_cadastro = info_cadastro['tipo_cadastro'].iloc[0] if len(info_cadastro) > 0 and pd.notna(info_cadastro['tipo_cadastro'].iloc[0]) else None
                status_cadastro = info_cadastro['status_cadastro'].iloc[0] if len(info_cadastro) > 0 and pd.notna(info_cadastro['status_cadastro'].iloc[0]) else None
                
                # Buscar classe
                classe = classe_por_item.get(sku, 'SEM_CLASSE')
                
                # Buscar embalagem da descrição
                embalagem = None
                if descricao_cadastro:
                    embalagem = extrair_embalagem_descricao(descricao_cadastro)
                
                # Criar item_id
                if embalagem:
                    item_id = f"{sku}_{embalagem}"
                else:
                    item_id = str(sku)
                
                # Buscar preço e custo se disponíveis
                preco = precos_por_item_externo.get(sku) if precos_por_item_externo else None
                custo = custos_por_item_externo.get(sku) if custos_por_item_externo else None
                margem = (preco - custo) if preco is not None and custo is not None else None
                
                # Buscar demanda histórica se disponível
                limite_demanda = limite_demanda_por_item.get(sku)
                tem_demanda = sku in skus_com_demanda or (limite_demanda is not None)
                
                # Calcular margem_por_ovo se possível
                margem_ovo = None
                if margem is not None and embalagem:
                    ovos = extrair_ovos_por_caixa(embalagem)
                    if ovos and ovos > 0:
                        margem_ovo = margem / ovos
                
                linha = {
                    'item_id': item_id,
                    'item': sku,
                    'descricao': descricao_cadastro,
                    'embalagem': embalagem if embalagem else 'SEM_EMBALAGEM',
                    'classe': classe,
                    'quantidade_produzida': 0.0,
                    'quantidade_alocada': 0.0,
                    'quantidade_reservada': 0.0,
                    'quantidade_nao_atendida_pedido': 0.0,
                    'periodo_label': periodo_label,
                    'data_producao': producao['data_producao'].iloc[0] if len(producao) > 0 else pd.NaT,
                    'diferenca_aloc_menos_prod': 0.0,
                    'diferenca_absoluta': 0.0,
                    'preco': preco,
                    'custo_ytd': custo,
                    'margem_unitaria': margem,
                    'margem_por_ovo': margem_ovo,
                    'tem_demanda_historica': tem_demanda,
                    'demanda_max': limite_demanda,
                    'limite_demanda_historica': limite_demanda,
                    'tipo': 'cadastro',  # Novo tipo para indicar SKU apenas no cadastro
                    'origem_dado': 'Somente cadastro',
                    'tem_pedido': False,
                    'pedido_ignorado': False,
                    'sku_restrito': False,  # Está na lista de ativos, então não é restrito
                    'usa_custo_medio_classe': False,
                }
                linhas_ausentes.append(linha)
                
                # Log resumido
                motivo = []
                if classe == 'SEM_CLASSE':
                    motivo.append('sem classe')
                if tipo_cadastro:
                    motivo.append(f'tipo={tipo_cadastro}')
                if status_cadastro and status_cadastro != 'ATIVO':
                    motivo.append(f'status={status_cadastro}')
                motivo_str = f" ({', '.join(motivo)})" if motivo else ""
                print(f"  - {sku}: {descricao_cadastro[:50] if descricao_cadastro else 'Sem descrição'}...{motivo_str}")
            
            # Adicionar linhas ao comparacao
            df_ausentes = pd.DataFrame(linhas_ausentes)
            # Garantir que todas as colunas existam em ambos os DataFrames
            for col in comparacao.columns:
                if col not in df_ausentes.columns:
                    df_ausentes[col] = None
            for col in df_ausentes.columns:
                if col not in comparacao.columns:
                    comparacao[col] = None
            comparacao = pd.concat([comparacao, df_ausentes], ignore_index=True)
            print(f"  Total: {len(skus_ausentes)} SKUs adicionados ao output")

    # Converter quantidades de ovos para caixas de 360 ovos
    OVOS_POR_CAIXA = 360
    colunas_qtd_cx360 = {
        "quantidade_produzida": "cx360_produzida",
        "quantidade_alocada": "cx360_alocada",
        "quantidade_reservada": "cx360_reservada",
        "quantidade_nao_atendida_pedido": "cx360_nao_atendida_pedido",
        "quantidade_total_pedida": "cx360_total_pedida",
        "diferenca_aloc_menos_prod": "cx360_diferenca",
    }
    for col_origem, col_cx in colunas_qtd_cx360.items():
        if col_origem in comparacao.columns:
            comparacao[col_cx] = (comparacao[col_origem] / OVOS_POR_CAIXA).round(2)

    # Converter quantidades de ovos para caixas físicas reais (baseado na embalagem de cada SKU)
    comparacao["ovos_por_caixa"] = comparacao["embalagem"].apply(extrair_ovos_por_caixa)
    colunas_qtd_cxfisica = {
        "quantidade_produzida": "cxfisica_produzida",
        "quantidade_alocada": "cxfisica_alocada",
        "quantidade_reservada": "cxfisica_reservada",
        "quantidade_nao_atendida_pedido": "cxfisica_nao_atendida_pedido",
        "diferenca_aloc_menos_prod": "cxfisica_diferenca",
    }
    for col_origem, col_cx in colunas_qtd_cxfisica.items():
        if col_origem in comparacao.columns:
            comparacao[col_cx] = (comparacao[col_origem] / comparacao["ovos_por_caixa"]).round(2)

    n_com_ovos = comparacao["ovos_por_caixa"].notna().sum()
    n_sem_ovos = comparacao["ovos_por_caixa"].isna().sum()
    print(f"\n[INFO] Caixas físicas: {n_com_ovos} SKUs com embalagem parseada, {n_sem_ovos} sem (RESERVA/desconhecido)")

    # Ordem de colunas para exportação: quantidades agrupadas (ovos, cx360, cxfisica)
    colunas_ordenadas = [
        "item_id", "item", "descricao", "embalagem", "ovos_por_caixa", "classe",
        "quantidade_produzida", "quantidade_alocada", "quantidade_reservada", "quantidade_nao_atendida_pedido",
        "diferenca_aloc_menos_prod", "diferenca_absoluta",
        "cx360_produzida", "cx360_alocada", "cx360_reservada", "cx360_nao_atendida_pedido", "cx360_diferenca",
        "cxfisica_produzida", "cxfisica_alocada", "cxfisica_reservada", "cxfisica_nao_atendida_pedido", "cxfisica_diferenca",
        "periodo_label", "data_producao",
        "preco", "custo_ytd", "margem_unitaria", "margem_unitaria_cx360", "margem_por_ovo",
        "tem_demanda_historica", "demanda_max", "limite_demanda_historica",
        "periodo_demanda_mes_ref", "periodo_demanda_ano_ref",
        "periodo_demanda_janela_meses", "tipo_calculo_demanda", "granularidade_demanda",
        "tipo", "origem_dado", "tem_pedido", "pedido_ignorado",
        "sku_restrito", "usa_custo_medio_classe", "preco_origem", "custo_origem",
    ]
    colunas_final = [c for c in colunas_ordenadas if c in comparacao.columns]
    colunas_final += [c for c in comparacao.columns if c not in colunas_final and c != "embalagens"]
    comparacao = comparacao[colunas_final]
    comparacao = aplicar_colunas_estabelecimento(comparacao, config)
    if len(df_demanda_historica) > 0:
        df_demanda_historica = aplicar_colunas_estabelecimento(df_demanda_historica, config)
    if len(df_param_demanda) > 0:
        df_param_demanda = aplicar_colunas_estabelecimento(df_param_demanda, config)

    # Salvar CSV (única tabela: uma linha por SKU, quantidade_reservada na coluna específica)
    comparacao.to_csv(output_path_csv, index=False, encoding="utf-8", sep=args.sep, decimal=args.decimal)
    
    # Salvar Excel
    try:
        with pd.ExcelWriter(output_path_xlsx, engine='openpyxl') as writer:
            # Aba 1: Comparacao (uma linha por item; item_id e embalagem principais; volume reservado em quantidade_reservada)
            comparacao.to_excel(writer, sheet_name='Comparacao', index=False)
            # Aba 2: Demanda Histórica (todos os SKUs com limite; origem: demanda_historica_*.xlsx)
            if len(df_demanda_historica) > 0:
                df_demanda_historica.to_excel(writer, sheet_name='Demanda Historica', index=False)
            # Aba 3: Parametros Demanda (parâmetros do cálculo; origem: aba Parametros do demanda_historica_*.xlsx)
            if len(df_param_demanda) > 0:
                df_param_demanda.to_excel(writer, sheet_name='Parametros Demanda', index=False)
            # Aba(s) seguinte(s): Pedidos Ignorados (se houver)
            if len(pedidos_ignorados) > 0:
                df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
                df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_total_pedida', ascending=False)
                df_pedidos_ignorados.to_excel(writer, sheet_name='Pedidos Ignorados', index=False)
    except ImportError:
        print("[AVISO] openpyxl nao instalado. Salve apenas CSV.")
        comparacao.to_excel(output_path_xlsx, index=False, engine='openpyxl')
    except Exception as e:
        print(f"[AVISO] Erro ao salvar Excel: {e}. Salve apenas CSV.")
    
    abas_list = ["Comparacao"]
    
    # Salvar pedidos ignorados em arquivo separado (se houver)
    output_pedidos_ignorados_csv = None
    output_pedidos_ignorados_xlsx = None
    if len(pedidos_ignorados) > 0:
        df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
        df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_total_pedida', ascending=False)
        df_pedidos_ignorados = aplicar_colunas_estabelecimento(df_pedidos_ignorados, config)
        output_pedidos_ignorados_csv = RESULTS_DIR / f"pedidos_ignorados_{periodo_label}_{timestamp}.csv"
        output_pedidos_ignorados_xlsx = RESULTS_DIR / f"pedidos_ignorados_{periodo_label}_{timestamp}.xlsx"
        df_pedidos_ignorados.to_csv(output_pedidos_ignorados_csv, index=False, encoding="utf-8")
        try:
            df_pedidos_ignorados.to_excel(output_pedidos_ignorados_xlsx, index=False, engine='openpyxl')
        except Exception:
            pass

    print("\n[OK] Comparacao concluida")
    granularidade = config.get("modelo", {}).get("granularidade_demanda", "S").upper() if config else "S"
    gran_desc = {"D": "Dia", "S": "Semana", "M": "Mês"}.get(granularidade, "Semana")
    print(f"  {gran_desc}: {periodo_label}")
    print(f"  Producao total: {producao['quantidade_produzida'].sum():,.0f} unidades")
    print(f"  Alocacao total: {alocacao['quantidade_alocada'].sum():,.0f} unidades")
    if config:
        producao_bruta_path = _resolver_caminho(config, "producao_bruta", INPUT_PATH / "PRODUÇÃO DIA.xlsx")
    else:
        producao_bruta_path = INPUT_PATH / "PRODUÇÃO DIA.xlsx"
    print(f"  Arquivo de producao: {os.fspath(Path(producao_bruta_path))} (aba {SHEET_NAME})")
    print(f"  Resultado do modelo: {caminho_resultado}")
    print(f"  Saida gerada em:")
    if len(df_demanda_historica) > 0:
        abas_list.append("Demanda Historica")
    if len(df_param_demanda) > 0:
        abas_list.append("Parametros Demanda")
    if len(pedidos_ignorados) > 0:
        abas_list.append("Pedidos Ignorados")
    num_abas = len(abas_list)
    print(f"    - CSV: {output_path_csv}")
    print(f"    - Excel: {output_path_xlsx} (com {num_abas} aba" + ("s" if num_abas > 1 else "") + ": " + ", ".join(abas_list) + ")")
    if len(pedidos_ignorados) > 0:
        print(f"    - Pedidos ignorados CSV: {output_pedidos_ignorados_csv}")
        print(f"    - Pedidos ignorados Excel: {output_pedidos_ignorados_xlsx}")


if __name__ == "__main__":
    main()
