#!/usr/bin/env python3
"""
Gera arquivo consolidado PBI-ready a partir dos outputs do modelo.

Saidas:
  - CSV:  resultados/pbi_consolidado_sku_<semana>.csv   (aba 1 flat)
  - CSV:  resultados/pbi_skus_fora_otimizacao_<semana>.csv (quando houver)
  - XLSX: resultados/pbi_consolidado_<semana>.xlsx       (3-4 abas)
      Aba 1 - consolidado_sku   : 1 linha por SKU (comparacao + auditoria + DOW resumido + parametros)
      Aba 2 - distribuicao_diaria: 1 linha por SKU x dia (DOW detalhado + parametros DOW)
      Aba 3 - parametros         : tabela chave-valor com todos os parametros utilizados
      Aba 4 - skus_fora_otimizacao: SKUs ativos que ficaram fora da otimização e motivo principal

Uso:
  python3 output/gerar_consolidado_pbi.py
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

from output.metadados_output import aplicar_colunas_estabelecimento

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def _carregar_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolver_output_dir(config: Dict[str, Any]) -> Path:
    """Resolve diretório de saída a partir do config com fallback legado."""
    output_dir = Path(config.get("paths", {}).get("output_dir", "resultados"))
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _mais_recente(output_dir: Path, padrao: str) -> Optional[Path]:
    arquivos = sorted(output_dir.glob(padrao), key=lambda p: p.stat().st_mtime)
    return arquivos[-1] if arquivos else None


def _extrair_parametros_flat(config: Dict[str, Any]) -> pd.DataFrame:
    """Extrai todos os parametros do config em formato chave-valor."""
    rows = []
    for secao, conteudo in config.items():
        if isinstance(conteudo, dict):
            for chave, valor in conteudo.items():
                if isinstance(valor, (list, dict)):
                    valor = str(valor)
                rows.append({"secao": secao, "parametro": chave, "valor": valor})
        else:
            rows.append({"secao": "", "parametro": secao, "valor": conteudo})
    return pd.DataFrame(rows)


def _parametros_modelo_colunas(config: Dict[str, Any]) -> Dict[str, Any]:
    """Retorna dict com parametros do modelo para injetar como colunas."""
    dados = config.get("dados", {})
    modelo = config.get("modelo", {})
    solver = config.get("solver", {})
    return {
        "param_semana_ref": dados.get("semana_ref"),
        "param_ano_custo": dados.get("ano_custo"),
        "param_mes_custo": dados.get("mes_custo"),
        "param_meses_janela_custo": dados.get("meses_janela_custo"),
        "param_tipo_calculo_demanda": modelo.get("tipo_calculo_demanda"),
        "param_fator_demanda_maxima": modelo.get("fator_demanda_maxima"),
        "param_granularidade_demanda": modelo.get("granularidade_demanda"),
        "param_considerar_demanda_historica": modelo.get("considerar_demanda_historica"),
        "param_atender_pedidos": modelo.get("atender_pedidos"),
        "param_usar_apenas_excedente": modelo.get("usar_apenas_excedente"),
        "param_capar_reserva_na_producao": modelo.get("capar_reserva_na_producao"),
        "param_variaveis_continuas": modelo.get("variaveis_continuas"),
        "param_tipo_objetivo": modelo.get("tipo_objetivo"),
        "param_solver_type": solver.get("solver_type"),
    }


def _parametros_dow_colunas(config: Dict[str, Any]) -> Dict[str, Any]:
    """Retorna dict com parametros DOW para injetar como colunas."""
    dados = config.get("dados", {})
    return {
        "param_dow_ano_ref": dados.get("dow_ano_ref", dados.get("ano_custo")),
        "param_dow_mes_ref": dados.get("dow_mes_ref", dados.get("mes_custo")),
        "param_dow_meses_janela": dados.get("dow_meses_janela", dados.get("meses_janela_custo")),
        "param_semana_ref": dados.get("semana_ref"),
    }


def _carregar_comparacao(output_dir: Path) -> Optional[pd.DataFrame]:
    path = _mais_recente(output_dir, "comparacao_producao_alocacao_*.csv")
    if path is None:
        print("[AVISO] Nenhum arquivo comparacao_producao_alocacao encontrado.")
        return None
    print(f"  Comparacao: {path.name}")
    df = pd.read_csv(path)
    if "preco_pedido" in df.columns and "preco_medio_pedido" not in df.columns:
        df = df.rename(columns={"preco_pedido": "preco_medio_pedido"})
    if "margem_unitaria" in df.columns and "margem_unitaria_cx360" not in df.columns:
        df["margem_unitaria_cx360"] = df["margem_unitaria"]
    # Compatibilidade: comparação antiga pode não ter coluna explícita de déficit.
    if "quantidade_nao_atendida_pedido" not in df.columns:
        if "deficit_pedido" in df.columns:
            df["quantidade_nao_atendida_pedido"] = pd.to_numeric(df["deficit_pedido"], errors="coerce").fillna(0.0)
        else:
            df["quantidade_nao_atendida_pedido"] = 0.0
    return df


def _carregar_auditoria(output_dir: Path) -> Optional[pd.DataFrame]:
    path = _mais_recente(output_dir, "auditoria_baseline_*.xlsx")
    if path is None:
        print("[AVISO] Nenhum arquivo auditoria_baseline encontrado.")
        return None
    print(f"  Auditoria:  {path.name}")
    df = pd.read_excel(path, sheet_name="Detalhe por SKU")
    if "margem_unitaria" in df.columns and "margem_unitaria_cx360" not in df.columns:
        df["margem_unitaria_cx360"] = df["margem_unitaria"]
    if "quantidade_nao_atendida_pedido" not in df.columns:
        if "deficit_pedido" in df.columns:
            df["quantidade_nao_atendida_pedido"] = pd.to_numeric(df["deficit_pedido"], errors="coerce").fillna(0.0)
        else:
            df["quantidade_nao_atendida_pedido"] = 0.0
    return df


def _carregar_dow(output_dir: Path) -> Optional[pd.DataFrame]:
    path = _mais_recente(output_dir, "distribuicao_historica_dow_*.xlsx")
    if path is None:
        print("[AVISO] Nenhum arquivo distribuicao_historica_dow encontrado.")
        return None
    print(f"  DOW:        {path.name}")
    return pd.read_excel(path, sheet_name="distribuicao_sku_dia")


def _carregar_skus_fora_otimizacao(output_dir: Path) -> Optional[pd.DataFrame]:
    path = _mais_recente(output_dir, "skus_fora_otimizacao_*.csv")
    if path is None:
        print("[AVISO] Nenhum arquivo skus_fora_otimizacao encontrado.")
        return None
    print(f"  SKUs fora:  {path.name}")
    return pd.read_csv(path)


def _merge_auditoria(df_comp: pd.DataFrame, df_audit: pd.DataFrame) -> pd.DataFrame:
    """Adiciona colunas da auditoria baseline ao comparacao, por item."""
    colunas_audit = [
        "item",
        "volume_baseline_antes_cap",
        "volume_baseline",
        "receita_baseline",
        "custo_baseline",
        "margem_baseline",
        "volume_otimizado",
        "receita_otimizada",
        "custo_otimizado",
        "margem_otimizada",
        "volume_redistribuido",
        "status_cap_baseline",
        "diferenca_volume",
        "diferenca_receita",
        "diferenca_custo",
        "diferenca_margem",
        "proporcao_historica",
        "volume_historico_total",
    ]
    cols_disponiveis = [c for c in colunas_audit if c in df_audit.columns]
    df_audit_slim = df_audit[cols_disponiveis].copy()

    if "item" in df_audit_slim.columns:
        df_audit_slim["item"] = pd.to_numeric(df_audit_slim["item"], errors="coerce")
        df_audit_agg = df_audit_slim.groupby("item", as_index=False).first()
    else:
        return df_comp

    sufixo = "_audit"
    df_merged = df_comp.merge(df_audit_agg, on="item", how="left", suffixes=("", sufixo))
    for col in df_merged.columns:
        if col.endswith(sufixo):
            df_merged.drop(columns=[col], inplace=True)
    return df_merged


def _merge_dow_resumido(df: pd.DataFrame, df_dow: pd.DataFrame) -> pd.DataFrame:
    """Adiciona colunas resumidas do DOW (metodo, fallback, volume_modelo_semanal)."""
    if df_dow is None or len(df_dow) == 0:
        return df

    dow_resumo = (
        df_dow.groupby("item", as_index=False)
        .agg(
            metodo_dow=("metodo", "first"),
            fallback_motivo_dow=("fallback_motivo", "first"),
            volume_modelo_semanal_dow=("volume_modelo_semanal", "first"),
            volume_semana_estimado_dow=("volume_dia_estimado", "sum"),
            pct_dias_com_zero=("volume_dia_estimado", lambda x: round((x == 0).sum() / len(x) * 100, 1)),
        )
    )
    dow_resumo["item"] = pd.to_numeric(dow_resumo["item"], errors="coerce")
    return df.merge(dow_resumo, on="item", how="left")


def _injetar_parametros(df: pd.DataFrame, params: Dict[str, Any]) -> pd.DataFrame:
    """Adiciona colunas de parametros constantes a todas as linhas."""
    for col, val in params.items():
        df[col] = val
    return df


def gerar_consolidado_pbi(
    config: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """Gera consolidado PBI e retorna metadados da execução."""
    log = logger.info if logger is not None else print

    log("=" * 70)
    log("GERADOR DE CONSOLIDADO PBI")
    log("=" * 70)

    if config is None:
        config = _carregar_config()
    output_dir = _resolver_output_dir(config)
    semana_ref = config.get("dados", {}).get("semana_ref", "sem_ref")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log("\n[1/4] Carregando outputs...")
    df_comp = _carregar_comparacao(output_dir)
    df_audit = _carregar_auditoria(output_dir)
    df_dow = _carregar_dow(output_dir)
    df_fora = _carregar_skus_fora_otimizacao(output_dir)

    if df_comp is None:
        log("[ERRO] Sem arquivo de comparacao. Execute o pipeline antes.")
        raise SystemExit(1)

    # ── Aba 1: consolidado_sku ──
    log("\n[2/4] Montando consolidado_sku...")
    df_sku = df_comp.copy()

    if df_audit is not None:
        df_sku = _merge_auditoria(df_sku, df_audit)
        log(f"  Auditoria mergeada: {len(df_audit)} linhas")

    if df_dow is not None:
        df_sku = _merge_dow_resumido(df_sku, df_dow)
        log(f"  DOW resumido mergeado: {df_dow['item'].nunique()} SKUs")

    params_modelo = _parametros_modelo_colunas(config)
    params_dow = _parametros_dow_colunas(config)
    params_todos = {**params_modelo, **params_dow}
    df_sku = _injetar_parametros(df_sku, params_todos)
    df_sku = aplicar_colunas_estabelecimento(df_sku, config)
    log(f"  Parametros injetados: {len(params_todos)} colunas")
    log(f"  Resultado: {len(df_sku)} linhas x {len(df_sku.columns)} colunas")

    # ── Aba 2: distribuicao_diaria ──
    log("\n[3/4] Montando distribuicao_diaria...")
    if df_dow is not None and len(df_dow) > 0:
        df_diaria = df_dow.copy()
        df_diaria = _injetar_parametros(df_diaria, params_dow)
        df_diaria = aplicar_colunas_estabelecimento(df_diaria, config)
        log(f"  Resultado: {len(df_diaria)} linhas x {len(df_diaria.columns)} colunas")
    else:
        df_diaria = pd.DataFrame()
        log("  DOW nao disponivel, aba vazia.")

    # ── Aba 3: parametros ──
    df_params = _extrair_parametros_flat(config)
    df_params = aplicar_colunas_estabelecimento(df_params, config)
    log(f"  Parametros: {len(df_params)} linhas")

    # ── Aba 4: skus_fora_otimizacao ──
    if df_fora is not None and len(df_fora) > 0:
        df_fora = aplicar_colunas_estabelecimento(df_fora, config)
        log(f"  SKUs fora da otimizacao: {len(df_fora)} linhas")
    else:
        df_fora = pd.DataFrame()
        log("  SKUs fora da otimizacao: nao disponivel/vazio.")

    # ── Exportar ──
    log("\n[4/4] Exportando...")
    path_csv = output_dir / f"pbi_consolidado_sku_{semana_ref}_{timestamp}.csv"
    df_sku.to_csv(path_csv, index=False, encoding="utf-8")
    log(f"  CSV: {path_csv.name}")
    path_fora_csv = None
    if len(df_fora) > 0:
        path_fora_csv = output_dir / f"pbi_skus_fora_otimizacao_{semana_ref}_{timestamp}.csv"
        df_fora.to_csv(path_fora_csv, index=False, encoding="utf-8")
        log(f"  CSV SKUs fora: {path_fora_csv.name}")

    path_xlsx = output_dir / f"pbi_consolidado_{semana_ref}_{timestamp}.xlsx"
    with pd.ExcelWriter(path_xlsx, engine="openpyxl") as writer:
        df_sku.to_excel(writer, sheet_name="consolidado_sku", index=False)
        if len(df_diaria) > 0:
            df_diaria.to_excel(writer, sheet_name="distribuicao_diaria", index=False)
        df_params.to_excel(writer, sheet_name="parametros", index=False)
        if len(df_fora) > 0:
            df_fora.to_excel(writer, sheet_name="skus_fora_otimizacao", index=False)
    log(f"  XLSX: {path_xlsx.name}")

    log("\n[OK] Consolidado PBI gerado com sucesso.")
    log(f"  Aba consolidado_sku:    {len(df_sku)} SKUs x {len(df_sku.columns)} colunas")
    if len(df_diaria) > 0:
        log(f"  Aba distribuicao_diaria: {len(df_diaria)} linhas (SKU x dia)")
    log(f"  Aba parametros:          {len(df_params)} parametros")
    if len(df_fora) > 0:
        log(f"  Aba skus_fora_otimizacao: {len(df_fora)} linhas")

    return {
        "path_csv": str(path_csv),
        "path_xlsx": str(path_xlsx),
        "path_csv_skus_fora_otimizacao": str(path_fora_csv) if path_fora_csv else None,
        "linhas_consolidado_sku": len(df_sku),
        "colunas_consolidado_sku": len(df_sku.columns),
        "linhas_distribuicao_diaria": len(df_diaria),
        "linhas_parametros": len(df_params),
        "linhas_skus_fora_otimizacao": len(df_fora),
    }


def main() -> None:
    gerar_consolidado_pbi()


if __name__ == "__main__":
    main()
