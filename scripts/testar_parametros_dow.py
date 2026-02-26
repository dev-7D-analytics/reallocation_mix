#!/usr/bin/env python3
"""
Bateria de testes funcionais para distribuicao_historica_dow.py.

Objetivo:
- Validar comportamento com mudancas de parametros.
- Validar resultados (nao apenas "rodou sem erro").

Coberturas principais:
1) Granularidade nao semanal => DOW nao executa.
2) Sensibilidade da janela historica (1 vs 2 meses) altera distribuicao.
3) Fallbacks F1/F2 e diagnosticos de zero (incluindo ACA/EAC netting).
4) Fallback de configuracao (dow_* ausente usa mes_* de custo).
5) Validacoes de fechamento sinalizam ALERTA quando esperado (F2).
"""

from __future__ import annotations

import copy
import logging
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from output.distribuicao_historica_dow import gerar_distribuicao_historica_dow


@dataclass
class FakeResultado:
    resultado: pd.DataFrame


@dataclass
class FakeResultadoETL:
    pedidos_garantidos_por_sku: Dict[int, float]


def _logger() -> logging.Logger:
    logger = logging.getLogger("teste_dow_parametros")
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
        logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger


def _write_inputs(base_dir: Path) -> Dict[str, Path]:
    inputs_dir = base_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # SKUs ativos
    df_skus = pd.DataFrame(
        [
            {"item": 101, "STATUS": "ATIVO", "ESTAB": 100},
            {"item": 102, "STATUS": "ATIVO", "ESTAB": 100},
            {"item": 201, "STATUS": "ATIVO", "ESTAB": 100},
        ]
    )
    path_skus = inputs_dir / "skus_restritos.xlsx"
    df_skus.to_excel(path_skus, index=False)

    # Classes por SKU
    df_classes = pd.DataFrame(
        [
            {"item": 101, "Classe Produto": "A"},
            {"item": 102, "Classe Produto": "A"},
            {"item": 201, "Classe Produto": "B"},
        ]
    )
    path_classes = inputs_dir / "base_skus_classes.xlsx"
    df_classes.to_excel(path_classes, index=False)

    # Producao bruta diaria (ACA/EAC)
    # Desc Item com padrao parseavel para embalagem (CX 1 BJ 10 UN => 10 ovos por unidade).
    rows = [
        # Janeiro/2026 (semana 2)
        {"Data Trans": "2026-01-05", "Cod Item": 101, "Quantidade": 10, "Est": 100, "Esp": "ACA", "Desc Item": "OVO TESTE CX 1 BJ 10 UN"},
        {"Data Trans": "2026-01-06", "Cod Item": 101, "Quantidade": 30, "Est": 100, "Esp": "ACA", "Desc Item": "OVO TESTE CX 1 BJ 10 UN"},
        # Fevereiro/2026 (semana 6)
        {"Data Trans": "2026-02-02", "Cod Item": 101, "Quantidade": 10, "Est": 100, "Esp": "ACA", "Desc Item": "OVO TESTE CX 1 BJ 10 UN"},
        {"Data Trans": "2026-02-03", "Cod Item": 101, "Quantidade": 10, "Est": 100, "Esp": "ACA", "Desc Item": "OVO TESTE CX 1 BJ 10 UN"},
        # ACA/EAC que se anulam (deve gerar log especifico)
        {"Data Trans": "2026-02-04", "Cod Item": 101, "Quantidade": 5, "Est": 100, "Esp": "ACA", "Desc Item": "OVO TESTE CX 1 BJ 10 UN"},
        {"Data Trans": "2026-02-04", "Cod Item": 101, "Quantidade": 5, "Est": 100, "Esp": "EAC", "Desc Item": "OVO TESTE CX 1 BJ 10 UN"},
    ]
    df_prod = pd.DataFrame(rows)
    path_prod = inputs_dir / "PRODUCAO_DIA.xlsx"
    # O leitor usa skiprows=1; por isso gravamos cabecalho a partir da linha 2.
    with pd.ExcelWriter(path_prod, engine="openpyxl") as writer:
        df_prod.to_excel(writer, sheet_name="CE0302", index=False, startrow=1)

    return {
        "skus_restritos": path_skus,
        "classes": path_classes,
        "producao_bruta": path_prod,
    }


def _config(base_dir: Path, paths: Dict[str, Path]) -> Dict[str, Any]:
    return {
        "dados": {
            "estabelecimentos": [100],
            "semana_ref": "2026-07",
            "dow_ano_ref": 2026,
            "dow_mes_ref": 2,
            "dow_meses_janela": 2,
            "ano_custo": 2026,
            "mes_custo": 2,
            "meses_janela_custo": 2,
        },
        "modelo": {
            "granularidade_demanda": "S",
        },
        "paths": {
            "output_dir": str(base_dir / "resultados"),
            "skus_restritos": str(paths["skus_restritos"]),
            "classes": str(paths["classes"]),
            "producao_bruta": str(paths["producao_bruta"]),
        },
    }


def _resultado_modelo() -> FakeResultado:
    df = pd.DataFrame(
        [
            {"item": 101, "classe": "A", "quantidade": 700.0, "tipo": "otimizacao", "descricao": "SKU 101"},
            {"item": 102, "classe": "A", "quantidade": 350.0, "tipo": "otimizacao", "descricao": "SKU 102"},
            {"item": 201, "classe": "B", "quantidade": 210.0, "tipo": "otimizacao", "descricao": "SKU 201"},
        ]
    )
    return FakeResultado(resultado=df)


def _resultado_etl() -> FakeResultadoETL:
    return FakeResultadoETL(pedidos_garantidos_por_sku={})


def _run(config: Dict[str, Any], logger: logging.Logger) -> Path | None:
    return gerar_distribuicao_historica_dow(_resultado_modelo(), _resultado_etl(), config, logger)


def _load_output(path_xlsx: Path) -> Dict[str, pd.DataFrame]:
    return {
        "distribuicao_sku_dia": pd.read_excel(path_xlsx, sheet_name="distribuicao_sku_dia"),
        "consolidado_sku_semana": pd.read_excel(path_xlsx, sheet_name="consolidado_sku_semana"),
        "perfil_classe_dow": pd.read_excel(path_xlsx, sheet_name="perfil_classe_dow"),
        "validacoes": pd.read_excel(path_xlsx, sheet_name="validacoes"),
    }


def _assert_close(value: float, expected: float, tol: float, msg: str) -> None:
    if not math.isclose(value, expected, rel_tol=0, abs_tol=tol):
        raise AssertionError(f"{msg}: esperado={expected}, obtido={value}")


def test_granularidade_nao_semanal(base_config: Dict[str, Any], logger: logging.Logger) -> None:
    cfg = copy.deepcopy(base_config)
    cfg["modelo"]["granularidade_demanda"] = "D"
    out = _run(cfg, logger)
    if out is not None:
        raise AssertionError("Esperado None quando granularidade_demanda != 'S'.")


def test_janela_muda_resultado(base_config: Dict[str, Any], logger: logging.Logger) -> None:
    # Janela de 1 mes (fev/2026): 101 tem 100 ovos seg e 100 ovos ter -> 50%/50%
    cfg_1m = copy.deepcopy(base_config)
    cfg_1m["dados"]["dow_meses_janela"] = 1
    out_1m = _run(cfg_1m, logger)
    if out_1m is None:
        raise AssertionError("Saida DOW nao gerada para janela de 1 mes.")
    d1 = _load_output(out_1m)["distribuicao_sku_dia"]
    d1_101 = d1[d1["item"] == 101]
    seg_1m = float(d1_101[d1_101["dow"] == 2]["volume_dia_estimado"].iloc[0])
    ter_1m = float(d1_101[d1_101["dow"] == 3]["volume_dia_estimado"].iloc[0])
    _assert_close(seg_1m, 350.0, 0.01, "Janela 1m - volume segunda")
    _assert_close(ter_1m, 350.0, 0.01, "Janela 1m - volume terca")

    # Janela de 2 meses (jan+fev/2026): 101 seg=200 ovos, ter=400 ovos -> 1/3 e 2/3
    cfg_2m = copy.deepcopy(base_config)
    cfg_2m["dados"]["dow_meses_janela"] = 2
    out_2m = _run(cfg_2m, logger)
    if out_2m is None:
        raise AssertionError("Saida DOW nao gerada para janela de 2 meses.")
    d2 = _load_output(out_2m)["distribuicao_sku_dia"]
    d2_101 = d2[d2["item"] == 101]
    seg_2m = float(d2_101[d2_101["dow"] == 2]["volume_dia_estimado"].iloc[0])
    ter_2m = float(d2_101[d2_101["dow"] == 3]["volume_dia_estimado"].iloc[0])
    _assert_close(seg_2m, 233.33, 0.02, "Janela 2m - volume segunda")
    _assert_close(ter_2m, 466.67, 0.02, "Janela 2m - volume terca")

    if math.isclose(seg_1m, seg_2m, abs_tol=0.01):
        raise AssertionError("Mudanca de janela nao alterou resultado como esperado.")


def test_fallbacks_e_logs_zero(base_config: Dict[str, Any], logger: logging.Logger) -> None:
    cfg = copy.deepcopy(base_config)
    cfg["dados"]["dow_meses_janela"] = 2
    out = _run(cfg, logger)
    if out is None:
        raise AssertionError("Saida DOW nao gerada para teste de fallback/log.")
    dist = _load_output(out)["distribuicao_sku_dia"]

    # Metodo por SKU
    met_101 = set(dist[dist["item"] == 101]["metodo"].dropna().unique().tolist())
    met_102 = set(dist[dist["item"] == 102]["metodo"].dropna().unique().tolist())
    met_201 = set(dist[dist["item"] == 201]["metodo"].dropna().unique().tolist())
    if met_101 != {"historico_sku"}:
        raise AssertionError(f"Metodo inesperado para item 101: {met_101}")
    if met_102 != {"fallback_f1_classe"}:
        raise AssertionError(f"Metodo inesperado para item 102: {met_102}")
    if met_201 != {"fallback_f2_sem_historico"}:
        raise AssertionError(f"Metodo inesperado para item 201: {met_201}")

    # Log de netting ACA/EAC para quarta-feira (dow=4) no SKU 101
    log_101_d4 = str(
        dist[(dist["item"] == 101) & (dist["dow"] == 4)]["log_zero"].iloc[0]
    )
    if "ACA cancelado por EAC" not in log_101_d4:
        raise AssertionError("Nao encontrou log ACA/EAC esperado para item 101 no dow=4.")

    # Log de nunca produzido para um dia sem historico bruto (ex.: dow=5)
    log_101_d5 = str(
        dist[(dist["item"] == 101) & (dist["dow"] == 5)]["log_zero"].iloc[0]
    )
    if "SKU nunca produzido neste dia da semana" not in log_101_d5:
        raise AssertionError("Nao encontrou log 'SKU nunca produzido...' esperado para item 101 no dow=5.")

    # F2 deve zerar todos os dias do item 201
    d201 = dist[dist["item"] == 201]
    if not (d201["volume_dia_estimado"] == 0).all():
        raise AssertionError("Item 201 deveria estar zerado em todos os dias (fallback F2).")


def test_fallback_config_dow_para_custo(base_config: Dict[str, Any], logger: logging.Logger) -> None:
    # Cenário A: explicito por DOW
    cfg_a = copy.deepcopy(base_config)
    cfg_a["dados"]["dow_meses_janela"] = 1
    out_a = _run(cfg_a, logger)
    if out_a is None:
        raise AssertionError("Cenario A sem saida.")
    dist_a = _load_output(out_a)["distribuicao_sku_dia"]
    seg_a = float(dist_a[(dist_a["item"] == 101) & (dist_a["dow"] == 2)]["volume_dia_estimado"].iloc[0])

    # Cenário B: remove dow_* e usa fallback mes_custo/meses_janela_custo
    cfg_b = copy.deepcopy(base_config)
    cfg_b["dados"].pop("dow_ano_ref", None)
    cfg_b["dados"].pop("dow_mes_ref", None)
    cfg_b["dados"].pop("dow_meses_janela", None)
    cfg_b["dados"]["ano_custo"] = 2026
    cfg_b["dados"]["mes_custo"] = 2
    cfg_b["dados"]["meses_janela_custo"] = 1
    out_b = _run(cfg_b, logger)
    if out_b is None:
        raise AssertionError("Cenario B sem saida.")
    dist_b = _load_output(out_b)["distribuicao_sku_dia"]
    seg_b = float(dist_b[(dist_b["item"] == 101) & (dist_b["dow"] == 2)]["volume_dia_estimado"].iloc[0])

    _assert_close(seg_a, seg_b, 0.01, "Fallback de configuracao DOW->custo divergente")


def test_validacoes_fechamento(base_config: Dict[str, Any], logger: logging.Logger) -> None:
    cfg = copy.deepcopy(base_config)
    out = _run(cfg, logger)
    if out is None:
        raise AssertionError("Saida DOW nao gerada para teste de validacoes.")
    val = _load_output(out)["validacoes"]

    # Espera-se ao menos um ALERTA por causa do fallback F2 (item 201).
    alertas = val[val["status"] == "ALERTA"]
    if len(alertas) == 0:
        raise AssertionError("Esperava ao menos um ALERTA nas validacoes.")

    chaves_alerta = set(alertas["chave"].astype(str).tolist())
    esperado = "2026-07|201|B"
    if esperado not in chaves_alerta:
        raise AssertionError(f"Esperava ALERTA de fechamento para {esperado}. Alertas: {sorted(chaves_alerta)}")


def main() -> None:
    logger = _logger()
    tests = []

    with tempfile.TemporaryDirectory(prefix="dow_param_tests_") as tmp:
        base_dir = Path(tmp)
        paths = _write_inputs(base_dir)
        base_cfg = _config(base_dir, paths)

        tests = [
            ("granularidade_nao_semanal", lambda: test_granularidade_nao_semanal(base_cfg, logger)),
            ("janela_muda_resultado", lambda: test_janela_muda_resultado(base_cfg, logger)),
            ("fallbacks_e_logs_zero", lambda: test_fallbacks_e_logs_zero(base_cfg, logger)),
            ("fallback_config_dow_para_custo", lambda: test_fallback_config_dow_para_custo(base_cfg, logger)),
            ("validacoes_fechamento", lambda: test_validacoes_fechamento(base_cfg, logger)),
        ]

        passed = 0
        failed = 0
        failures: list[str] = []

        print("\n=== BATERIA DE TESTES DOW (PARAMETROS + RESULTADOS) ===")
        for name, fn in tests:
            try:
                fn()
                print(f"[PASSOU] {name}")
                passed += 1
            except Exception as exc:
                print(f"[FALHOU] {name}: {exc}")
                failed += 1
                failures.append(f"{name}: {exc}")

        print("\n=== RESUMO ===")
        print(f"Passou: {passed}")
        print(f"Falhou: {failed}")
        if failed > 0:
            print("Falhas:")
            for f in failures:
                print(f" - {f}")
            raise SystemExit(1)

        print("Todos os testes passaram.")


if __name__ == "__main__":
    main()
