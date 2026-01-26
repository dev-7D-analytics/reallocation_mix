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

    df_classes = df_classes[["item", col_classe]].rename(columns={col_classe: "Classe_Produto"})
    df_prod = df_prod.merge(df_classes, on="item", how="left")
    df_prod["Classe_Produto"] = df_prod["Classe_Produto"].fillna("OUTROS")

    prod_agg = (
        df_prod.groupby(["item", "embalagem", "item_id", "Classe_Produto"], as_index=False)["quantidade"]
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
        candidatos = sorted(RESULTS_DIR.glob("resultado_realocacao_completo_*.csv"))
        if not candidatos:
            raise FileNotFoundError(f"Nenhum resultado encontrado em {RESULTS_DIR.resolve()}")
        csv_path = candidatos[-1]

    df_aloc = pd.read_csv(csv_path)

    required_cols = {"item_id", "item", "embalagem", "classe", "quantidade"}
    if not required_cols.issubset(set(df_aloc.columns)):
        raise ValueError(
            f"Arquivo de alocacao incompatível: {csv_path}. "
            f"Colunas necessárias: {sorted(required_cols)}. "
            "Use um arquivo resultado_realocacao_completo_*.csv (detalhado por item_id)."
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
        .rename(columns={"quantidade": "quantidade_alocada", "classe": "Classe_Produto"})
    )
    return aloc_agg, csv_path


def construir_comparacao(producao: pd.DataFrame, alocacao: pd.DataFrame, year_week: str) -> pd.DataFrame:
    """Combine producao and alocacao by item_id to compare volumes."""
    comparacao = producao.merge(
        alocacao,
        on=["item_id", "item", "embalagem", "Classe_Produto"],
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

    return comparacao.sort_values("diferenca_absoluta", ascending=False).reset_index(drop=True)


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

    comparacao = construir_comparacao(producao, alocacao, year_week)
    comparacao = comparacao.merge(precos, on="item_id", how="left")
    comparacao = comparacao.merge(custos[["item_id", "custo_ytd"]], on="item_id", how="left")
    
    # Calcular margem unitária (em R$/caixa)
    comparacao["margem_unitaria"] = comparacao["preco"] - comparacao["custo_ytd"]
    
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

    RESULTS_DIR.mkdir(exist_ok=True)
    output_path_csv = RESULTS_DIR / f"comparacao_producao_alocacao_{year_week}.csv"
    output_path_xlsx = RESULTS_DIR / f"comparacao_producao_alocacao_{year_week}.xlsx"
    
    # Salvar CSV
    comparacao.to_csv(output_path_csv, index=False, encoding="utf-8", sep=args.sep, decimal=args.decimal)
    
    # Salvar Excel
    try:
        comparacao.to_excel(output_path_xlsx, index=False, engine='openpyxl')
    except ImportError:
        print("[AVISO] openpyxl nao instalado. Salve apenas CSV.")
    except Exception as e:
        print(f"[AVISO] Erro ao salvar Excel: {e}. Salve apenas CSV.")

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
    print(f"    - CSV: {output_path_csv}")
    print(f"    - Excel: {output_path_xlsx}")


if __name__ == "__main__":
    main()
