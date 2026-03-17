#!/usr/bin/env python3
"""
Concatena múltiplos arquivos pbi_consolidado_*.xlsx (por aba) em um único Excel.

Uso:
  python3 output/concatenar_pbi_consolidados.py
  python3 output/concatenar_pbi_consolidados.py --input-dir resultados/pbi_concat
  python3 output/concatenar_pbi_consolidados.py --output resultados/pbi_concat/pbi_consolidado_concat.xlsx
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = PROJECT_ROOT / "resultados" / "pbi_concat"
DEFAULT_PATTERN = "pbi_consolidado_*.xlsx"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Concatena arquivos pbi_consolidado por aba (consolidado_sku, parametros, etc.)."
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help="Diretório contendo os arquivos pbi_consolidado_*.xlsx",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help="Padrão de arquivos para concatenação (glob).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Caminho do arquivo .xlsx de saída. Se omitido, grava no input-dir com timestamp.",
    )
    return parser.parse_args()


def _extrair_metadados_nome_arquivo(nome_arquivo: str) -> Tuple[str, str]:
    """
    Extrai metadados do nome esperado:
      pbi_consolidado_<periodo>_<YYYYMMDD_HHMMSS>.xlsx
    """
    pattern = re.compile(r"^pbi_consolidado_(?P<periodo>.+)_(?P<ts>\d{8}_\d{6})\.xlsx$")
    m = pattern.match(nome_arquivo)
    if not m:
        return "desconhecido", "desconhecido"
    return m.group("periodo"), m.group("ts")


def _listar_arquivos(input_dir: Path, pattern: str) -> List[Path]:
    arquivos = sorted(input_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    # Ignora artefatos temporários/metadata comuns.
    filtrados: List[Path] = []
    for arq in arquivos:
        nome = arq.name
        if nome.startswith("~$"):
            continue
        if ":Zone.Identifier" in nome:
            continue
        # Evita concatenar saídas já concatenadas em execuções anteriores.
        if nome.startswith("pbi_consolidado_concat_"):
            continue
        filtrados.append(arq)
    return filtrados


def _reordenar_colunas(df: pd.DataFrame) -> pd.DataFrame:
    meta_cols = [
        "arquivo_origem_pbi",
        "periodo_origem_pbi",
        "timestamp_origem_pbi",
    ]
    existentes = [c for c in meta_cols if c in df.columns]
    restantes = [c for c in df.columns if c not in existentes]
    return df[existentes + restantes]


def concatenar_workbooks(arquivos: List[Path]) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
    acumulador: Dict[str, List[pd.DataFrame]] = {}
    linhas_info: List[Dict[str, object]] = []

    for arquivo in arquivos:
        periodo, ts = _extrair_metadados_nome_arquivo(arquivo.name)
        xls = pd.ExcelFile(arquivo)
        print(f"[INFO] Lendo {arquivo.name} | abas={xls.sheet_names}")

        for aba in xls.sheet_names:
            df = pd.read_excel(arquivo, sheet_name=aba)
            df["arquivo_origem_pbi"] = arquivo.name
            df["periodo_origem_pbi"] = periodo
            df["timestamp_origem_pbi"] = ts
            df = _reordenar_colunas(df)
            acumulador.setdefault(aba, []).append(df)

            linhas_info.append(
                {
                    "arquivo_origem_pbi": arquivo.name,
                    "periodo_origem_pbi": periodo,
                    "timestamp_origem_pbi": ts,
                    "aba": aba,
                    "linhas_lidas": len(df),
                    "colunas_lidas": len(df.columns),
                }
            )

    consolidados: Dict[str, pd.DataFrame] = {}
    for aba, dfs in acumulador.items():
        consolidados[aba] = pd.concat(dfs, ignore_index=True, sort=False)

    df_info = pd.DataFrame(linhas_info)
    return consolidados, df_info


def main() -> None:
    args = _parse_args()
    input_dir = Path(args.input_dir)
    if not input_dir.is_absolute():
        input_dir = PROJECT_ROOT / input_dir
    if not input_dir.exists():
        raise SystemExit(f"[ERRO] Diretório não encontrado: {input_dir}")

    arquivos = _listar_arquivos(input_dir, args.pattern)
    if len(arquivos) == 0:
        raise SystemExit(
            f"[ERRO] Nenhum arquivo encontrado em {input_dir} com padrão {args.pattern}."
        )

    consolidados, df_info = concatenar_workbooks(arquivos)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = PROJECT_ROOT / output_path
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = input_dir / f"pbi_consolidado_concat_{timestamp}.xlsx"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for aba, df in consolidados.items():
            df.to_excel(writer, sheet_name=aba[:31], index=False)
        # Aba adicional com rastreio da concatenação.
        df_info.to_excel(writer, sheet_name="consolidacao_info", index=False)

    print("\n[OK] Concatenação concluída.")
    print(f"  Arquivos lidos: {len(arquivos)}")
    for aba, df in consolidados.items():
        print(f"  Aba '{aba}': {len(df):,} linhas x {len(df.columns):,} colunas")
    print(f"  Saída: {output_path}")


if __name__ == "__main__":
    main()

