import argparse
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from extrair_compatibilidade_embalagem import (
    calcular_qtd_embalagem,
    extrair_embalagem_descricao,
)


INPUT_PATH = Path("inputs")
SHEET_NAME = "CE0302"
RESULTS_DIR = Path("resultados")
DEFAULT_YEAR_WEEK = "2025-51"
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

    df_precos = df_precos[["item_id", "preco"]].copy()
    df_precos = df_precos[df_precos["preco"] > 0]
    return df_precos.drop_duplicates(["item_id"])


def _carregar_pedidos(config: Dict) -> pd.DataFrame:
    """Carrega pedidos por SKU."""
    path = _resolver_caminho(config, "pedidos", INPUT_PATH / "pedidos_clientes.csv")
    if not path.exists():
        return pd.DataFrame(columns=["item", "quantidade_total_pedida"])
    
    try:
        df_pedidos = pd.read_csv(path)
        
        # Detectar coluna de quantidade (pode ser 'quantidade_pedida' ou 'quantidade')
        col_qtd = None
        for col in df_pedidos.columns:
            col_lower = col.lower()
            if 'quantidade' in col_lower or 'qtd' in col_lower:
                col_qtd = col
                break
        
        if 'item' not in df_pedidos.columns or col_qtd is None:
            return pd.DataFrame(columns=["item", "quantidade_total_pedida"])
        
        df_pedidos['item'] = pd.to_numeric(df_pedidos['item'], errors='coerce')
        df_pedidos = df_pedidos[df_pedidos['item'].notna()].copy()
        df_pedidos['item'] = df_pedidos['item'].astype(int)
        df_pedidos = df_pedidos[df_pedidos[col_qtd] > 0].copy()
        
        # Agregar pedidos por SKU
        pedidos_por_sku = df_pedidos.groupby('item')[col_qtd].sum().reset_index()
        pedidos_por_sku.columns = ['item', 'quantidade_total_pedida']
        return pedidos_por_sku
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


def _carregar_demanda_historica(config: Dict) -> pd.DataFrame:
    """Carrega demanda histórica (se disponível)."""
    # Tentar carregar do resultado do modelo primeiro (se tiver coluna tem_demanda_historica)
    # Caso contrário, retornar DataFrame vazio
    return pd.DataFrame(columns=["item", "demanda_max"])


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
        meses_janela = dados_config.get('meses_janela_custo', 1)  # Janela de meses (últimos N meses)
        
        # Calcular range de meses
        if meses_janela > 1:
            periodos = []
            for i in range(meses_janela):
                mes = mes_custo - i
                ano = ano_custo
                while mes <= 0:
                    mes += 12
                    ano -= 1
                periodos.append((ano, mes))
            
            mask_estab = df_custo['Estab'] == estab_custo
            mask_periodo = df_custo.apply(lambda row: (row['ano'], row['MÊS']) in periodos, axis=1)
            df_custo = df_custo[mask_estab & mask_periodo].copy()
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
        
        # Extrair embalagem e criar item_id
        col_item_desc = 'Descrição do item'
        df_custo['embalagem'] = df_custo[col_item_desc].apply(extrair_embalagem_descricao)
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
    """Read production data and return aggregation by item_id."""
    if config:
        producao_bruta_path = _resolver_caminho(config, "producao_bruta", INPUT_PATH / "PRODUÇÃO DIA.xlsx")
    else:
        producao_bruta_path = INPUT_PATH / "PRODUÇÃO DIA.xlsx"
    excel_path = Path(producao_bruta_path)
    df_prod = pd.read_excel(excel_path, sheet_name=SHEET_NAME, skiprows=1)

    df_prod["week"] = extract_week(df_prod, "Data Trans")
    df_prod["year"] = extract_year(df_prod, "Data Trans")
    df_prod["year_week"] = df_prod["year"].astype(str) + "-" + df_prod["week"].astype(str).str.zfill(2)

    target_year_week = year_week or sorted(df_prod["year_week"].unique())[-1]
    df_prod = df_prod[df_prod["year_week"] == target_year_week].copy()
    if df_prod.empty:
        raise ValueError(f"Nao encontrei registros para a semana {target_year_week} em {excel_path}")

    df_prod["embalagem"] = df_prod["Desc Item"].apply(extrair_embalagem_descricao)
    df_prod["qtd_embalagem"] = df_prod["embalagem"].apply(calcular_qtd_embalagem)
    df_prod["quantidade"] = df_prod["QUANTIDADE CORRIGIDA"] * df_prod["qtd_embalagem"]

    df_prod["item"] = pd.to_numeric(df_prod["Cod Item"], errors="coerce")
    df_prod = df_prod[
        (df_prod["item"].notna())
        & (df_prod["embalagem"].notna())
        & (df_prod["quantidade"].notna())
        & (df_prod["quantidade"] > 0)
    ].copy()
    df_prod["item"] = df_prod["item"].astype(int)
    df_prod["item_id"] = df_prod["item"].astype(str) + "_" + df_prod["embalagem"]
    df_prod["data_producao"] = df_prod["year_week"].apply(get_week_start_date)

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
    prod_agg["year_week"] = target_year_week
    prod_agg["data_producao"] = get_week_start_date(target_year_week)

    return prod_agg, target_year_week


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

    aloc_agg = (
        df_aloc.groupby(["item_id", "item", "embalagem", "classe",], as_index=False)["quantidade"]
        .sum()
        .rename(columns={"quantidade": "quantidade_alocada"})
    )
    return aloc_agg, csv_path


def construir_comparacao(producao: pd.DataFrame, alocacao: pd.DataFrame, year_week: str, config: Optional[Dict] = None) -> pd.DataFrame:
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

    comparacao["year_week"] = year_week
    comparacao["data_producao"] = comparacao["data_producao"].fillna(get_week_start_date(year_week))
    
    # Adicionar descrição dos itens
    comparacao = _adicionar_descricao(comparacao, config)

    return comparacao.sort_values("diferenca_absoluta", ascending=False).reset_index(drop=True)


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
        help="Ano-semana ISO (ex.: 2025-51). Se vazio, usa a ultima semana disponivel.",
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
    producao, year_week = carregar_producao(year_week, config)
    alocacao, caminho_resultado = carregar_alocacao(args.resultado, sep=args.sep, decimal=args.decimal)
    precos = _carregar_precos(config)
    custos = _carregar_custos(config)
    
    # Carregar informações para mapeamento
    pedidos = _carregar_pedidos(config)
    skus_restritos = _carregar_skus_restritos(config)
    
    # Tentar carregar demanda histórica, limite_demanda_historica, margem_por_ovo e custo_medio_classe do resultado do modelo
    # Usar o caminho já carregado em carregar_alocacao
    skus_com_demanda = set()
    custo_medio_classe_por_item_id = {}  # item_id -> True/False
    limite_demanda_por_item = {}  # item -> limite_demanda_historica
    margem_por_ovo_por_item = {}  # item -> margem_por_ovo (R$/ovo - métrica otimizada)
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
        
        # Extrair custo_medio_classe por item_id
        if 'custo_medio_classe' in df_aloc_completo.columns and 'item_id' in df_aloc_completo.columns:
            df_aloc_completo['item_id'] = df_aloc_completo['item_id'].astype(str)
            custo_medio_classe_por_item_id = df_aloc_completo.set_index('item_id')['custo_medio_classe'].to_dict()
    except Exception:
        pass

    comparacao = construir_comparacao(producao, alocacao, year_week, config)
    
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
    
    # Fazer merge: primeiro com resultado do modelo, depois com arquivos externos (fallback)
    # ESTRATÉGIA MELHORADA PARA MISMATCH DE EMBALAGEM:
    # 1. Tentar buscar do item_id produzido (merge direto)
    # 2. Se não encontrar e houver mismatch, buscar do item_id alocado (que está no output do modelo)
    # 3. Se ainda não encontrar, buscar de qualquer embalagem do mesmo SKU
    
    # Inicializar flags para rastrear origem dos dados
    comparacao['preco_origem'] = None
    comparacao['custo_origem'] = None
    
    if precos_resultado is not None and len(precos_resultado) > 0:
        # PASSO 1: Merge direto com item_id produzido
        # Renomear coluna do resultado para evitar conflito
        precos_resultado_renamed = precos_resultado.rename(columns={'preco': 'preco_resultado'})
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
        
        # PASSO 3: Fallback para arquivo externo se ainda não tiver preço
        mask_sem_preco = comparacao['preco'].isna()
        if mask_sem_preco.any():
            comparacao = comparacao.merge(precos, on="item_id", how="left", suffixes=('', '_externo'))
            comparacao.loc[mask_sem_preco, 'preco'] = comparacao.loc[mask_sem_preco, 'preco'].fillna(
                comparacao.loc[mask_sem_preco, 'preco_externo']
            )
            comparacao.loc[
                (mask_sem_preco) & (comparacao['preco_externo'].notna()), 
                'preco_origem'
            ] = 'arquivo_externo'
            if 'preco_externo' in comparacao.columns:
                comparacao = comparacao.drop(columns=['preco_externo'])
        
        # PASSO 4: Fallback final - usar qualquer preço disponível do mesmo SKU
        if precos_por_item is not None:
            mask_sem_preco = comparacao['preco'].isna()
            if mask_sem_preco.any():
                comparacao.loc[mask_sem_preco, 'preco'] = comparacao.loc[mask_sem_preco, 'item'].map(precos_por_item)
                comparacao.loc[
                    (mask_sem_preco) & (comparacao['preco'].notna()), 
                    'preco_origem'
                ] = 'mesmo_sku_outra_embalagem'
    else:
        # Se não tem preços do resultado, usar apenas arquivo externo
        comparacao = comparacao.merge(precos, on="item_id", how="left")
        if 'preco' in comparacao.columns:
            comparacao.loc[comparacao['preco'].notna(), 'preco_origem'] = 'arquivo_externo'
        else:
            comparacao['preco'] = None
    
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
            comparacao.loc[mask_sem_custo, 'custo_ytd'] = comparacao.loc[mask_sem_custo, 'custo_ytd'].fillna(
                comparacao.loc[mask_sem_custo, 'custo_ytd_externo']
            )
            comparacao.loc[
                (mask_sem_custo) & (comparacao['custo_ytd_externo'].notna()), 
                'custo_origem'
            ] = 'arquivo_externo'
            if 'custo_ytd_externo' in comparacao.columns:
                comparacao = comparacao.drop(columns=['custo_ytd_externo'])
        
        # PASSO 4: Fallback final - usar qualquer custo disponível do mesmo SKU
        if custos_por_item is not None:
            mask_sem_custo = comparacao['custo_ytd'].isna()
            if mask_sem_custo.any():
                comparacao.loc[mask_sem_custo, 'custo_ytd'] = comparacao.loc[mask_sem_custo, 'item'].map(custos_por_item)
                comparacao.loc[
                    (mask_sem_custo) & (comparacao['custo_ytd'].notna()), 
                    'custo_origem'
                ] = 'mesmo_sku_outra_embalagem'
    else:
        # Se não tem custos do resultado, usar apenas arquivo externo
        comparacao = comparacao.merge(custos[["item_id", "custo_ytd"]], on="item_id", how="left")
        if 'custo_ytd' in comparacao.columns:
            comparacao.loc[comparacao['custo_ytd'].notna(), 'custo_origem'] = 'arquivo_externo'
        else:
            comparacao['custo_ytd'] = None
    
    # Calcular margem unitária (em R$/caixa)
    comparacao["margem_unitaria"] = comparacao["preco"] - comparacao["custo_ytd"]
    
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
                    'year_week': year_week,
                    'data_producao': get_week_start_date(year_week),
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
    else:
        comparacao["tem_pedido"] = False
        comparacao["quantidade_reservada"] = 0
    
    # 3. pedido_ignorado: SKU tem pedido que foi ignorado
    if len(pedidos_ignorados) > 0:
        skus_com_pedido_ignorado = set([p['item'] for p in pedidos_ignorados])
        comparacao["pedido_ignorado"] = comparacao["item"].isin(skus_com_pedido_ignorado)
    else:
        comparacao["pedido_ignorado"] = False
    
    # 4. tipo: Indica origem da alocação (compatível com otimizador.py)
    # - 'otimizacao': SKU foi alocado pela otimização
    # - 'reserva': SKU teve pedido/reserva com produção na semana
    # - 'reserva_sem_producao': SKU teve pedido/reserva MAS sem produção na semana
    # - 'producao': SKU só tem produção, sem alocação nem reserva
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
    
    # 3. tem_demanda_historica: SKU tem histórico de demanda
    if len(skus_com_demanda) > 0:
        comparacao["tem_demanda_historica"] = comparacao["item"].isin(skus_com_demanda)
    else:
        comparacao["tem_demanda_historica"] = False
    
    # 4. limite_demanda_historica: Valor do limite de demanda histórica utilizado na restrição
    if len(limite_demanda_por_item) > 0:
        comparacao["limite_demanda_historica"] = comparacao["item"].map(limite_demanda_por_item)
    else:
        comparacao["limite_demanda_historica"] = None
    
    # 5. margem_por_ovo: Margem por ovo (R$/ovo) - métrica otimizada pelo modelo
    # Primeiro: tentar usar valores do resultado do modelo (para SKUs alocados)
    if len(margem_por_ovo_por_item) > 0:
        comparacao["margem_por_ovo"] = comparacao["item"].map(margem_por_ovo_por_item)
    else:
        comparacao["margem_por_ovo"] = None
    
    # Segundo: calcular margem_por_ovo para SKUs que não têm (baseado na embalagem)
    # Função para extrair ovos por caixa da embalagem (ex: "CX 12 BJ 20 UN" = 12*20 = 240)
    def extrair_ovos_por_caixa(embalagem):
        if pd.isna(embalagem):
            return None
        import re
        # Padrão: "CX X BJ Y UN" onde X = bandejas, Y = ovos por bandeja
        match = re.search(r'CX\s*(\d+)\s*BJ\s*(\d+)', str(embalagem), re.IGNORECASE)
        if match:
            bandejas = int(match.group(1))
            ovos_por_bandeja = int(match.group(2))
            return bandejas * ovos_por_bandeja
        return None
    
    # Calcular para registros sem margem_por_ovo mas com margem_unitaria
    sem_margem_ovo = comparacao['margem_por_ovo'].isna() & comparacao['margem_unitaria'].notna()
    if sem_margem_ovo.sum() > 0:
        ovos_por_caixa = comparacao.loc[sem_margem_ovo, 'embalagem'].apply(extrair_ovos_por_caixa)
        comparacao.loc[sem_margem_ovo, 'margem_por_ovo'] = comparacao.loc[sem_margem_ovo, 'margem_unitaria'] / ovos_por_caixa
    
    # 6. custo_medio_classe: Custo foi calculado usando média da classe
    if len(custo_medio_classe_por_item_id) > 0:
        comparacao["custo_medio_classe"] = comparacao["item_id"].map(custo_medio_classe_por_item_id).fillna(False)
    else:
        comparacao["custo_medio_classe"] = False
    
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
    output_path_csv = RESULTS_DIR / f"comparacao_producao_alocacao_{year_week}.csv"
    output_path_xlsx = RESULTS_DIR / f"comparacao_producao_alocacao_{year_week}.xlsx"
    
    # Reordenar colunas por categoria lógica para facilitar leitura
    # 1. Identificação | 2. Período | 3. Quantidades | 4. Financeiro unitário
    # 5. Restrições | 6. Flags/Status | 7. Origens dos dados
    colunas_ordenadas = [
        # === IDENTIFICAÇÃO ===
        'item_id', 'item', 'descricao', 'embalagem', 'classe',
        # === PERÍODO ===
        'year_week', 'data_producao',
        # === QUANTIDADES ===
        'quantidade_produzida', 'quantidade_alocada', 'quantidade_reservada', 'diferenca_aloc_menos_prod', 'diferenca_absoluta',
        # === FINANCEIRO UNITÁRIO ===
        'preco', 'custo_ytd', 'margem_unitaria', 'margem_por_ovo',
        # === RESTRIÇÕES / LIMITES ===
        'limite_demanda_historica',
        # === FLAGS / STATUS ===
        'tipo', 'origem_dado', 'tem_pedido', 'pedido_ignorado', 'sku_restrito', 
        'tem_demanda_historica', 'custo_medio_classe',
        # === ORIGENS DOS DADOS ===
        'preco_origem', 'custo_origem',
    ]
    # Manter apenas colunas que existem e adicionar outras ao final
    colunas_existentes = [c for c in colunas_ordenadas if c in comparacao.columns]
    colunas_restantes = [c for c in comparacao.columns if c not in colunas_ordenadas]
    comparacao = comparacao[colunas_existentes + colunas_restantes]
    
    # Salvar CSV
    comparacao.to_csv(output_path_csv, index=False, encoding="utf-8", sep=args.sep, decimal=args.decimal)
    
    # Salvar Excel
    try:
        with pd.ExcelWriter(output_path_xlsx, engine='openpyxl') as writer:
            # Aba 1: Comparacao
            comparacao.to_excel(writer, sheet_name='Comparacao', index=False)
            
            # Aba 2: Pedidos Ignorados (se houver)
            if len(pedidos_ignorados) > 0:
                df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
                df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_total_pedida', ascending=False)
                df_pedidos_ignorados.to_excel(writer, sheet_name='Pedidos Ignorados', index=False)
    except ImportError:
        print("[AVISO] openpyxl nao instalado. Salve apenas CSV.")
        comparacao.to_excel(output_path_xlsx, index=False, engine='openpyxl')
    except Exception as e:
        print(f"[AVISO] Erro ao salvar Excel: {e}. Salve apenas CSV.")
    
    # Salvar pedidos ignorados em arquivo separado (se houver)
    output_pedidos_ignorados_csv = None
    output_pedidos_ignorados_xlsx = None
    if len(pedidos_ignorados) > 0:
        df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
        df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_total_pedida', ascending=False)
        output_pedidos_ignorados_csv = RESULTS_DIR / f"pedidos_ignorados_{year_week}.csv"
        output_pedidos_ignorados_xlsx = RESULTS_DIR / f"pedidos_ignorados_{year_week}.xlsx"
        df_pedidos_ignorados.to_csv(output_pedidos_ignorados_csv, index=False, encoding="utf-8")
        try:
            df_pedidos_ignorados.to_excel(output_pedidos_ignorados_xlsx, index=False, engine='openpyxl')
        except Exception:
            pass

    print("\n[OK] Comparacao concluida")
    print(f"  Semana: {year_week}")
    print(f"  Producao total: {producao['quantidade_produzida'].sum():,.0f} unidades")
    print(f"  Alocacao total: {alocacao['quantidade_alocada'].sum():,.0f} unidades")
    if config:
        producao_bruta_path = _resolver_caminho(config, "producao_bruta", INPUT_PATH / "PRODUÇÃO DIA.xlsx")
    else:
        producao_bruta_path = INPUT_PATH / "PRODUÇÃO DIA.xlsx"
    print(f"  Arquivo de producao: {os.fspath(Path(producao_bruta_path))} (aba {SHEET_NAME})")
    print(f"  Resultado do modelo: {caminho_resultado}")
    print(f"  Saida gerada em:")
    num_abas = 2 if len(pedidos_ignorados) > 0 else 1
    print(f"    - CSV: {output_path_csv}")
    print(f"    - Excel: {output_path_xlsx} (com {num_abas} aba" + ("s" if num_abas > 1 else "") + ": Comparacao" + (", Pedidos Ignorados" if len(pedidos_ignorados) > 0 else "") + ")")
    if len(pedidos_ignorados) > 0:
        print(f"    - Pedidos ignorados CSV: {output_pedidos_ignorados_csv}")
        print(f"    - Pedidos ignorados Excel: {output_pedidos_ignorados_xlsx}")


if __name__ == "__main__":
    main()
