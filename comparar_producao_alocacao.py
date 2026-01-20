import argparse
import os
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from extrair_compatibilidade_embalagem import (
    calcular_qtd_embalagem,
    extrair_embalagem_descricao,
)


INPUT_PATH = Path("/mnt/c/Users/MarceloHenriqueGagli/OneDrive - 7D Analytics/mantiqueira/inputs")
SHEET_NAME = "CE0302"
RESULTS_DIR = Path("resultados")
DEFAULT_YEAR_WEEK = "2025-51"


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


def carregar_producao(year_week: Optional[str]) -> Tuple[pd.DataFrame, str]:
    """Read production data and return aggregation by item_id."""
    excel_path = INPUT_PATH / "PRODUÇÃO DIA.xlsx"
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
    df_classes = pd.read_excel(INPUT_PATH / "base_skus_classes.xlsx")
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


def carregar_alocacao(arquivo_resultado: Optional[str]) -> Tuple[pd.DataFrame, Path]:
    """Load model allocation results aggregated by item_id."""
    if arquivo_resultado:
        csv_path = Path(arquivo_resultado)
    else:
        candidatos = sorted(RESULTS_DIR.glob("resultado_realocacao_completo_*.csv"))
        if not candidatos:
            raise FileNotFoundError(f"Nenhum resultado encontrado em {RESULTS_DIR.resolve()}")
        csv_path = candidatos[-1]

    df_aloc = pd.read_csv(csv_path)
    df_aloc["item"] = pd.to_numeric(df_aloc["item"], errors="coerce")
    df_aloc = df_aloc[df_aloc["item"].notna()].copy()
    df_aloc["item"] = df_aloc["item"].astype(int)
    df_aloc["item_id"] = df_aloc["item_id"].astype(str)
    df_aloc["embalagem"] = df_aloc["embalagem"].astype(str)
    df_aloc["classe"] = df_aloc["classe"].fillna("OUTROS")

    aloc_agg = (
        df_aloc.groupby(["item_id", "item", "embalagem", "classe", "margem_unitaria", "custo_ytd"], as_index=False)["quantidade"]
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

    producao, year_week = carregar_producao(year_week)
    alocacao, caminho_resultado = carregar_alocacao(args.resultado)
    comparacao = construir_comparacao(producao, alocacao, year_week)

    RESULTS_DIR.mkdir(exist_ok=True)
    output_path = RESULTS_DIR / f"comparacao_producao_alocacao_{year_week}.csv"
    comparacao.to_csv(output_path, index=False, encoding="utf-8", sep=args.sep, decimal=args.decimal)

    print("\n[OK] Comparacao concluida")
    print(f"  Semana: {year_week}")
    print(f"  Producao total: {producao['quantidade_produzida'].sum():,.0f} unidades")
    print(f"  Alocacao total: {alocacao['quantidade_alocada'].sum():,.0f} unidades")
    print(f"  Arquivo de producao: {os.fspath(INPUT_PATH / 'PRODUÇÃO DIA.xlsx')} (aba {SHEET_NAME})")
    print(f"  Resultado do modelo: {caminho_resultado}")
    print(f"  Saida gerada em: {output_path}")


if __name__ == "__main__":
    main()
