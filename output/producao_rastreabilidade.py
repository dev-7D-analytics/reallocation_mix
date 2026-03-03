"""
Utilitários para rastreabilidade de produção (bruta, estorno e líquida).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from extrair_compatibilidade_embalagem import (
    calcular_qtd_embalagem,
    extrair_embalagem_descricao,
)


SHEET_NAME = "CE0302"


def _extract_week(df: pd.DataFrame, date_column: str) -> pd.Series:
    return pd.to_datetime(df[date_column]).dt.isocalendar().week


def _extract_year(df: pd.DataFrame, date_column: str) -> pd.Series:
    return pd.to_datetime(df[date_column]).dt.isocalendar().year


def obter_producao_classe_rastreabilidade(config: Dict) -> pd.DataFrame:
    """
    Retorna produção por classe com colunas:
      - producao_bruta_classe (ACA)
      - producao_estorno_eac_classe (EAC em volume positivo)
      - producao_liquida_classe (ACA - EAC)
    """
    paths = config.get("paths", {})
    dados_cfg = config.get("dados", {})
    modelo_cfg = config.get("modelo", {})

    path_producao_bruta = Path(paths.get("producao_bruta", "inputs/PRODUÇÃO DIA.xlsx"))
    path_classes = Path(paths.get("classes", "inputs/base_skus_classes.xlsx"))
    path_skus = Path(paths.get("skus_restritos", "inputs/skus_restritos.xlsx"))

    if not path_producao_bruta.exists() or not path_classes.exists():
        return pd.DataFrame(
            columns=[
                "classe",
                "producao_bruta_classe",
                "producao_estorno_eac_classe",
                "producao_liquida_classe",
            ]
        )

    df_prod = pd.read_excel(path_producao_bruta, sheet_name=SHEET_NAME, skiprows=1)
    df_prod["Data Trans"] = pd.to_datetime(df_prod["Data Trans"], errors="coerce")

    estabelecimentos = dados_cfg.get("estabelecimentos", [100])
    if "Est" in df_prod.columns and estabelecimentos:
        df_prod = df_prod[df_prod["Est"].isin(estabelecimentos)].copy()

    granularidade = str(modelo_cfg.get("granularidade_demanda", "S")).upper()
    if granularidade == "D":
        data_ref = dados_cfg.get("data_ref")
        if data_ref:
            data_ref = pd.to_datetime(data_ref)
            df_prod = df_prod[df_prod["Data Trans"].dt.date == data_ref.date()].copy()
    elif granularidade == "M":
        data_ref = dados_cfg.get("data_ref")
        if data_ref:
            data_ref = pd.to_datetime(data_ref)
            df_prod = df_prod[
                (df_prod["Data Trans"].dt.year == data_ref.year)
                & (df_prod["Data Trans"].dt.month == data_ref.month)
            ].copy()
    else:
        semana_ref = dados_cfg.get("semana_ref")
        if semana_ref:
            df_prod["week"] = _extract_week(df_prod, "Data Trans")
            df_prod["year"] = _extract_year(df_prod, "Data Trans")
            df_prod["year_week"] = df_prod["year"].astype(str) + "-" + df_prod["week"].astype(str).str.zfill(2)
            df_prod = df_prod[df_prod["year_week"] == str(semana_ref)].copy()

    if df_prod.empty:
        return pd.DataFrame(
            columns=[
                "classe",
                "producao_bruta_classe",
                "producao_estorno_eac_classe",
                "producao_liquida_classe",
            ]
        )

    df_prod["Quantidade"] = pd.to_numeric(df_prod["Quantidade"], errors="coerce").fillna(0.0)
    qtd_assinada = df_prod["Quantidade"].copy()
    col_esp = next((c for c in ["Esp", "ESP", "Especie", "Espécie"] if c in df_prod.columns), None)
    if col_esp is not None:
        esp = df_prod[col_esp].astype(str).str.strip().str.upper()
        mask_eac = esp == "EAC"
        mask_aca = esp == "ACA"
        qtd_assinada.loc[mask_eac] = -qtd_assinada.loc[mask_eac].abs()
        qtd_assinada.loc[mask_aca] = qtd_assinada.loc[mask_aca].abs()

    df_prod["embalagem"] = df_prod["Desc Item"].apply(extrair_embalagem_descricao)
    df_prod["qtd_embalagem"] = df_prod["embalagem"].apply(calcular_qtd_embalagem)
    df_prod["qtd_embalagem"] = pd.to_numeric(df_prod["qtd_embalagem"], errors="coerce")
    df_prod["item"] = pd.to_numeric(df_prod["Cod Item"], errors="coerce")

    df_prod["producao_bruta"] = qtd_assinada.clip(lower=0) * df_prod["qtd_embalagem"]
    df_prod["producao_estorno_eac"] = (-qtd_assinada.clip(upper=0)) * df_prod["qtd_embalagem"]
    df_prod["producao_liquida"] = qtd_assinada * df_prod["qtd_embalagem"]

    df_prod = df_prod[
        df_prod["item"].notna()
        & df_prod["embalagem"].notna()
        & df_prod["qtd_embalagem"].notna()
        & ((df_prod["producao_bruta"] > 0) | (df_prod["producao_estorno_eac"] > 0))
    ].copy()
    if df_prod.empty:
        return pd.DataFrame(
            columns=[
                "classe",
                "producao_bruta_classe",
                "producao_estorno_eac_classe",
                "producao_liquida_classe",
            ]
        )

    df_prod["item"] = df_prod["item"].astype(int)

    if path_skus.exists():
        df_skus = pd.read_excel(path_skus)
        if {"STATUS", "ESTAB", "item"}.issubset(df_skus.columns):
            ativos = df_skus[
                (df_skus["STATUS"] == "ATIVO")
                & (df_skus["ESTAB"].isin(estabelecimentos))
            ]["item"]
            ativos = pd.to_numeric(ativos, errors="coerce").dropna().astype(int)
            df_prod = df_prod[df_prod["item"].isin(set(ativos.tolist()))].copy()

    if df_prod.empty:
        return pd.DataFrame(
            columns=[
                "classe",
                "producao_bruta_classe",
                "producao_estorno_eac_classe",
                "producao_liquida_classe",
            ]
        )

    df_classes = pd.read_excel(path_classes)
    col_classe = next(
        (c for c in df_classes.columns if "classe" in c.lower() and "produto" in c.lower()),
        None,
    )
    if col_classe is None:
        return pd.DataFrame(
            columns=[
                "classe",
                "producao_bruta_classe",
                "producao_estorno_eac_classe",
                "producao_liquida_classe",
            ]
        )

    df_classes = df_classes[["item", col_classe]].rename(columns={col_classe: "classe"})
    df_classes["item"] = pd.to_numeric(df_classes["item"], errors="coerce")
    df_classes = df_classes[df_classes["item"].notna()].copy()
    df_classes["item"] = df_classes["item"].astype(int)

    df_prod = df_prod.merge(df_classes, on="item", how="left")
    df_prod["classe"] = df_prod["classe"].fillna("OUTROS")

    return (
        df_prod.groupby("classe", as_index=False)
        .agg(
            producao_bruta_classe=("producao_bruta", "sum"),
            producao_estorno_eac_classe=("producao_estorno_eac", "sum"),
            producao_liquida_classe=("producao_liquida", "sum"),
        )
    )

