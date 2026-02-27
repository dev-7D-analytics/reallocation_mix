"""
Funções utilitárias para metadados de output.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def _normalizar_estabelecimentos(config: Dict[str, Any]) -> List[str]:
    dados = config.get("dados", {}) if isinstance(config, dict) else {}
    estabs = dados.get("estabelecimentos")
    if estabs is None:
        estab_custo = dados.get("estab_custo")
        estabs = [] if estab_custo is None else [estab_custo]
    if not isinstance(estabs, list):
        estabs = [estabs]

    out: List[str] = []
    for v in estabs:
        if v is None:
            continue
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def obter_colunas_estabelecimento(config: Dict[str, Any]) -> Dict[str, str]:
    estabs = _normalizar_estabelecimentos(config)
    if len(estabs) == 0:
        return {
            "estabelecimento": "",
            "estabelecimentos": "",
        }
    if len(estabs) == 1:
        return {
            "estabelecimento": estabs[0],
            "estabelecimentos": estabs[0],
        }
    return {
        "estabelecimento": "MULTI",
        "estabelecimentos": ",".join(estabs),
    }


def aplicar_colunas_estabelecimento(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    cols = obter_colunas_estabelecimento(config)
    out = df.copy()
    for col, val in cols.items():
        out[col] = val
    ordem = list(cols.keys()) + [c for c in out.columns if c not in cols]
    return out[ordem]
