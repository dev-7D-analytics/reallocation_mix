#!/usr/bin/env python3
"""
Valida rodadas do modelo com dados reais usando diferentes parametros.

Foco:
- validar que a execucao funciona;
- validar qualidade do resultado (invariantes e comparacoes entre cenarios).

Uso:
  python3 scripts/validar_rodadas_reais_parametros.py
"""

from __future__ import annotations

import copy
import importlib.util
import json
import logging
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAIN_PATH = PROJECT_ROOT / "main.py"


def _load_local_main():
    spec = importlib.util.spec_from_file_location("mantiqueira_main", MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Nao foi possivel carregar modulo main em {MAIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app_main = _load_local_main()


@dataclass
class ScenarioResult:
    nome: str
    dow_file: str
    total_skus: int
    total_linhas_sku_dia: int
    total_volume_modelo: float
    total_volume_estimado: float
    total_alertas: int
    total_fallback_f1: int
    total_fallback_f2: int
    percentual_linhas_zero: float
    validacao_execucao_ok: bool
    validacao_formato_ok: bool
    validacao_fechamento_ok: bool
    observacoes: List[str]


def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("validacao_rodadas_reais")
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger


def _list_dow_files(output_dir: Path) -> set[Path]:
    if not output_dir.exists():
        return set()
    return set(output_dir.glob("distribuicao_historica_dow_*.xlsx"))


def _extract_scenario_metrics(nome: str, dow_file: Path) -> Tuple[ScenarioResult, pd.DataFrame]:
    df_dia = pd.read_excel(dow_file, sheet_name="distribuicao_sku_dia")
    df_sem = pd.read_excel(dow_file, sheet_name="consolidado_sku_semana")
    df_val = pd.read_excel(dow_file, sheet_name="validacoes")

    obs: List[str] = []
    formato_ok = True
    fechamento_ok = True

    total_skus = int(df_sem["item"].nunique()) if len(df_sem) > 0 else 0
    total_linhas = int(len(df_dia))
    total_modelo = float(df_sem["volume_modelo_semanal"].sum()) if "volume_modelo_semanal" in df_sem.columns else 0.0
    total_estimado = float(df_sem["volume_semana_estimado"].sum()) if "volume_semana_estimado" in df_sem.columns else 0.0
    total_alertas = int((df_val["status"] == "ALERTA").sum()) if "status" in df_val.columns else 0

    # Invariante 1: 7 linhas por SKU na distribuicao diaria
    if total_skus > 0 and total_linhas != total_skus * 7:
        formato_ok = False
        obs.append(f"Esperado {total_skus * 7} linhas em distribuicao_sku_dia, obtido {total_linhas}.")

    # Invariante 2: ordenacao classe/item/dow
    if len(df_dia) > 0:
        ordenado = df_dia.sort_values(["classe", "item", "dow"]).reset_index(drop=True)
        if not df_dia.reset_index(drop=True).equals(ordenado):
            formato_ok = False
            obs.append("Aba distribuicao_sku_dia nao esta ordenada por classe,item,dow.")

    # Invariante 3: fechamento por SKU para metodos != F2 deve ser OK
    if len(df_sem) > 0 and "fallback_motivo" in df_sem.columns:
        skus_f2 = set(df_sem[df_sem["fallback_motivo"] == "sku_e_classe_sem_historico"]["item"].tolist())
        v1 = df_val[df_val["regra"] == "Fechamento SKU-semana"].copy() if "regra" in df_val.columns else pd.DataFrame()
        if len(v1) > 0:
            v1["item"] = v1["chave"].astype(str).str.split("|").str[1].astype("Int64")
            alertas_nao_f2 = v1[(v1["status"] == "ALERTA") & (~v1["item"].isin(skus_f2))]
            if len(alertas_nao_f2) > 0:
                fechamento_ok = False
                obs.append(f"{len(alertas_nao_f2)} alertas de fechamento SKU fora do fallback F2.")

    total_f1 = int((df_dia["metodo"] == "fallback_f1_classe").sum() / 7) if "metodo" in df_dia.columns else 0
    total_f2 = int((df_dia["metodo"] == "fallback_f2_sem_historico").sum() / 7) if "metodo" in df_dia.columns else 0
    pct_zero = (
        float((df_dia["volume_dia_estimado"] == 0).sum()) / float(len(df_dia)) * 100.0
        if len(df_dia) > 0 and "volume_dia_estimado" in df_dia.columns
        else 0.0
    )

    result = ScenarioResult(
        nome=nome,
        dow_file=str(dow_file),
        total_skus=total_skus,
        total_linhas_sku_dia=total_linhas,
        total_volume_modelo=total_modelo,
        total_volume_estimado=total_estimado,
        total_alertas=total_alertas,
        total_fallback_f1=total_f1,
        total_fallback_f2=total_f2,
        percentual_linhas_zero=round(pct_zero, 2),
        validacao_execucao_ok=True,
        validacao_formato_ok=formato_ok,
        validacao_fechamento_ok=fechamento_ok,
        observacoes=obs,
    )

    return result, df_dia


def _run_scenario(
    nome: str,
    base_cfg: Dict[str, Any],
    override: Dict[str, Any],
    logger: logging.Logger,
) -> Tuple[ScenarioResult, pd.DataFrame]:
    cfg = copy.deepcopy(base_cfg)
    _deep_update(cfg, override)

    output_dir = Path(cfg.get("paths", {}).get("output_dir", "resultados"))
    before = _list_dow_files(output_dir)

    # Monkeypatch: main() passa a usar este cfg sem alterar config.yaml no disco.
    original_loader = app_main.carregar_config
    app_main.carregar_config = lambda config_path="config.yaml": cfg
    try:
        app_main.main()
    except Exception as exc:
        failed = ScenarioResult(
            nome=nome,
            dow_file="",
            total_skus=0,
            total_linhas_sku_dia=0,
            total_volume_modelo=0.0,
            total_volume_estimado=0.0,
            total_alertas=0,
            total_fallback_f1=0,
            total_fallback_f2=0,
            percentual_linhas_zero=0.0,
            validacao_execucao_ok=False,
            validacao_formato_ok=False,
            validacao_fechamento_ok=False,
            observacoes=[f"Execucao com excecao: {exc}"],
        )
        return failed, pd.DataFrame()
    finally:
        app_main.carregar_config = original_loader

    after = _list_dow_files(output_dir)
    novos = sorted(list(after - before), key=lambda p: p.stat().st_mtime)
    if not novos:
        failed = ScenarioResult(
            nome=nome,
            dow_file="",
            total_skus=0,
            total_linhas_sku_dia=0,
            total_volume_modelo=0.0,
            total_volume_estimado=0.0,
            total_alertas=0,
            total_fallback_f1=0,
            total_fallback_f2=0,
            percentual_linhas_zero=0.0,
            validacao_execucao_ok=False,
            validacao_formato_ok=False,
            validacao_fechamento_ok=False,
            observacoes=["Nao foi identificado novo arquivo DOW no output_dir."],
        )
        return failed, pd.DataFrame()

    dow_file = novos[-1]
    result, df_dia = _extract_scenario_metrics(nome, dow_file)
    logger.info(f"[{nome}] DOW gerado: {dow_file}")
    return result, df_dia


def _comparar_distribuicoes(base_df: pd.DataFrame, other_df: pd.DataFrame) -> Dict[str, Any]:
    if len(base_df) == 0 or len(other_df) == 0:
        return {"comparavel": False, "linhas_com_diferenca": 0, "max_delta_abs": None}

    cols = ["item", "dow", "volume_dia_estimado"]
    a = base_df[cols].rename(columns={"volume_dia_estimado": "v_a"})
    b = other_df[cols].rename(columns={"volume_dia_estimado": "v_b"})
    merged = a.merge(b, on=["item", "dow"], how="inner")
    if len(merged) == 0:
        return {"comparavel": False, "linhas_com_diferenca": 0, "max_delta_abs": None}
    merged["delta_abs"] = (merged["v_a"] - merged["v_b"]).abs()
    n_diff = int((merged["delta_abs"] > 0.01).sum())
    max_delta = float(merged["delta_abs"].max()) if len(merged) > 0 else 0.0
    return {"comparavel": True, "linhas_com_diferenca": n_diff, "max_delta_abs": round(max_delta, 2)}


def main() -> None:
    logger = _make_logger()
    base_cfg = app_main.carregar_config("config.yaml")

    # Ajuste os cenarios conforme sua analise.
    # Mantemos alteracoes apenas em parametros DOW para isolar efeito da distribuicao diaria.
    scenarios = [
        ("base_atual", {}),
        ("dow_janela_3m", {"dados": {"dow_meses_janela": 3}}),
        ("dow_janela_1m", {"dados": {"dow_meses_janela": 1}}),
    ]

    resultados: List[ScenarioResult] = []
    dfs_dia: Dict[str, pd.DataFrame] = {}

    print("\n=== VALIDACAO DE RODADAS REAIS (MULTI-PARAMETRO) ===")
    for nome, override in scenarios:
        print(f"\n--- Rodando cenario: {nome} ---")
        res, df_dia = _run_scenario(nome, base_cfg, override, logger)
        resultados.append(res)
        dfs_dia[nome] = df_dia
        status = "OK" if (res.validacao_execucao_ok and res.validacao_formato_ok and res.validacao_fechamento_ok) else "ALERTA"
        print(
            f"[{status}] skus={res.total_skus}, linhas={res.total_linhas_sku_dia}, "
            f"vol_modelo={res.total_volume_modelo:,.0f}, vol_estimado={res.total_volume_estimado:,.0f}, "
            f"alertas={res.total_alertas}, f1={res.total_fallback_f1}, f2={res.total_fallback_f2}, "
            f"zeros={res.percentual_linhas_zero:.2f}%"
        )
        if res.observacoes:
            for o in res.observacoes:
                print(f"  - obs: {o}")

    # Comparacoes entre cenarios
    comparativos: Dict[str, Any] = {}
    base_nome = scenarios[0][0]
    base_res = next(r for r in resultados if r.nome == base_nome)
    for nome, _ in scenarios[1:]:
        r = next(x for x in resultados if x.nome == nome)
        vol_ok = math.isclose(base_res.total_volume_modelo, r.total_volume_modelo, rel_tol=0, abs_tol=0.1)
        comp = _comparar_distribuicoes(dfs_dia[base_nome], dfs_dia[nome])
        comparativos[nome] = {
            "volume_modelo_equivalente_ao_base": vol_ok,
            "comparacao_distribuicao": comp,
        }

    # Persistir relatorio
    report_dir = Path(base_cfg.get("paths", {}).get("output_dir", "resultados"))
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "validacao_parametros_reais_dow.json"
    payload = {
        "resultados_cenarios": [asdict(r) for r in resultados],
        "comparativos_vs_base": comparativos,
    }
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n=== COMPARATIVO VS BASE ===")
    for nome, info in comparativos.items():
        comp = info["comparacao_distribuicao"]
        print(
            f"- {nome}: volume_modelo_equivalente={info['volume_modelo_equivalente_ao_base']}, "
            f"linhas_diferentes={comp['linhas_com_diferenca']}, max_delta={comp['max_delta_abs']}"
        )

    all_ok = all(
        r.validacao_execucao_ok and r.validacao_formato_ok and r.validacao_fechamento_ok
        for r in resultados
    )

    print(f"\nRelatorio salvo em: {report_path}")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
