"""
Gera distribuição histórica DOW (dia da semana) em arquivo XLSX separado.

Abordagem B — distribui o volume decidido pelo modelo *por SKU* nos 7 dias
da semana, usando o perfil histórico de produção do próprio SKU.

Alinhado à formulação em docs/formulacao_modelo.tex (seção DOW):
  V_hat(i, w*, d) = V_mod(i, w*) * p_hist(i, d)

Onde:
  V_mod(i, w*) = x_aloc + r_reserva   (mesmo valor do comparador)
  p_hist(i, d) = V(i, ·, d) / V(i, ·, ·)

Fallbacks:
  F1: SKU sem histórico individual -> usar perfil da classe
  F2: SKU e classe sem histórico   -> distribuição zerada (com motivo)
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

import pandas as pd

from extrair_compatibilidade_embalagem import (
    extrair_embalagem_descricao,
    calcular_qtd_embalagem,
)
from metadados_output import aplicar_colunas_estabelecimento


# ---------------------------------------------------------------------------
#  Funções auxiliares
# ---------------------------------------------------------------------------

def _dow_domingo_1(dt_series: pd.Series) -> pd.Series:
    """Converte datetime para DOW no padrão 1=Dom, ..., 7=Sáb."""
    iso_day = dt_series.dt.isocalendar().day.astype(int)  # 1=Seg ... 7=Dom
    return (iso_day % 7) + 1  # 1=Dom ... 7=Sáb


def _get_week_start(semana_ref: str) -> date:
    """Retorna segunda-feira da semana ISO YYYY-WW."""
    ano, semana = semana_ref.split("-")
    return datetime.fromisocalendar(int(ano), int(semana), 1).date()


def _periodos_mensais(ano_ref: int, mes_ref: int, meses_janela: int) -> List[Tuple[int, int]]:
    periodos: List[Tuple[int, int]] = []
    for i in range(meses_janela):
        mes = mes_ref - i
        ano = ano_ref
        while mes <= 0:
            mes += 12
            ano -= 1
        periodos.append((ano, mes))
    return periodos


def _resolver_janela_dow(config: Dict[str, Any]) -> Tuple[int, int, int]:
    """Parâmetros dedicados de DOW em dados; fallback para janela de custo."""
    dados_cfg = config.get("dados", {})
    ano_ref = dados_cfg.get("dow_ano_ref", dados_cfg.get("ano_custo", 2025))
    mes_ref = dados_cfg.get("dow_mes_ref", dados_cfg.get("mes_custo", 11))
    meses_janela = dados_cfg.get("dow_meses_janela", dados_cfg.get("meses_janela_custo", 6))
    return int(ano_ref), int(mes_ref), int(meses_janela)


def _carregar_skus_ativos(config: Dict[str, Any]) -> set[int]:
    path_skus = Path(config.get("paths", {}).get("skus_restritos", "inputs/skus_restritos.xlsx"))
    if not path_skus.exists():
        return set()
    df = pd.read_excel(path_skus)
    if "STATUS" not in df.columns or "ESTAB" not in df.columns or "item" not in df.columns:
        return set()
    estabs = config.get("dados", {}).get("estabelecimentos", [100])
    ativos = df[(df["STATUS"] == "ATIVO") & (df["ESTAB"].isin(estabs))].copy()
    ativos["item"] = pd.to_numeric(ativos["item"], errors="coerce")
    ativos = ativos[ativos["item"].notna()].copy()
    return set(ativos["item"].astype(int).tolist())


def _carregar_classe_por_item(config: Dict[str, Any], skus_ativos: set[int] | None = None) -> pd.DataFrame:
    """Carrega mapeamento item→classe, filtrado opcionalmente por SKUs ativos."""
    path_classes = Path(config.get("paths", {}).get("classes", "inputs/base_skus_classes.xlsx"))
    df_classes = pd.read_excel(path_classes)
    col_classe = next(
        (c for c in df_classes.columns if "classe" in c.lower() and "produto" in c.lower()),
        None,
    )
    if col_classe is None or "item" not in df_classes.columns:
        raise ValueError("Base de classes sem colunas esperadas ('item' e 'classe produto').")
    out = df_classes[["item", col_classe]].copy()
    out.columns = ["item", "classe"]
    out["item"] = pd.to_numeric(out["item"], errors="coerce")
    out = out[out["item"].notna()].copy()
    out["item"] = out["item"].astype(int)
    out["classe"] = out["classe"].fillna("OUTROS")
    out = out.drop_duplicates("item")
    if skus_ativos:
        out = out[out["item"].isin(skus_ativos)].copy()
    return out


# ---------------------------------------------------------------------------
#  Carregamento do histórico com produção líquida (ACA - EAC)
# ---------------------------------------------------------------------------

def _carregar_historico_producao(
    config: Dict[str, Any], logger
) -> Tuple[pd.DataFrame, set[Tuple[int, int]]]:
    """Carrega produção bruta e agrega por (item, data) nettando ACA/EAC.

    Retorna:
        df_agg: DataFrame agregado com produção líquida > 0.
        pares_brutos_dow: conjunto de (item, dow) que tiveram registros brutos
            na janela (antes do netting), para diagnosticar zeros por estorno.
    """
    path = Path(config.get("paths", {}).get("producao_bruta", "inputs/PRODUÇÃO DIA.xlsx"))
    df = pd.read_excel(path, sheet_name="CE0302", skiprows=1)

    df["Data Trans"] = pd.to_datetime(df["Data Trans"], errors="coerce")
    df["item"] = pd.to_numeric(df.get("Cod Item"), errors="coerce")
    df["Quantidade"] = pd.to_numeric(df["Quantidade"], errors="coerce").fillna(0.0)

    estabs = config.get("dados", {}).get("estabelecimentos", [100])
    if "Est" in df.columns and estabs:
        antes = len(df)
        df = df[df["Est"].isin(estabs)].copy()
        logger.info(f"  DOW: filtro Est={estabs}: {antes:,} -> {len(df):,}")

    col_esp = next((c for c in ["Esp", "ESP", "Especie", "Espécie"] if c in df.columns), None)
    if col_esp is not None:
        esp = df[col_esp].astype(str).str.strip().str.upper()
        df.loc[esp == "EAC", "Quantidade"] = -df.loc[esp == "EAC", "Quantidade"].abs()
        df.loc[esp == "ACA", "Quantidade"] = df.loc[esp == "ACA", "Quantidade"].abs()

    df["embalagem"] = df["Desc Item"].apply(extrair_embalagem_descricao)
    df["qtd_embalagem"] = df["embalagem"].apply(calcular_qtd_embalagem)
    df["quantidade_ovos"] = df["Quantidade"] * df["qtd_embalagem"]

    df = df[
        df["Data Trans"].notna()
        & df["item"].notna()
        & df["embalagem"].notna()
        & df["quantidade_ovos"].notna()
    ].copy()
    df["item"] = df["item"].astype(int)

    skus_ativos = _carregar_skus_ativos(config)
    if skus_ativos:
        antes = len(df)
        df = df[df["item"].isin(skus_ativos)].copy()
        logger.info(f"  DOW: filtro SKUs ativos: {antes:,} -> {len(df):,}")

    df_classes = _carregar_classe_por_item(config, skus_ativos)
    df = df.merge(df_classes, on="item", how="left")
    df["classe"] = df["classe"].fillna("OUTROS")

    # Registrar (item, dow) que tiveram registros brutos ANTES do netting
    df["_dow_bruto"] = _dow_domingo_1(df["Data Trans"])
    pares_brutos_dow: set[Tuple[int, int]] = set(
        zip(df["item"].astype(int), df["_dow_bruto"].astype(int))
    )

    # Agregar por (item, data) para obter produção LÍQUIDA (ACA - EAC)
    df["data_date"] = df["Data Trans"].dt.date
    df_agg = (
        df.groupby(["item", "classe", "data_date"], as_index=False)["quantidade_ovos"]
        .sum()
    )
    df_agg = df_agg[df_agg["quantidade_ovos"] > 0].copy()

    df_agg["Data Trans"] = pd.to_datetime(df_agg["data_date"])
    df_agg["week"] = df_agg["Data Trans"].dt.isocalendar().week.astype(int)
    df_agg["year"] = df_agg["Data Trans"].dt.isocalendar().year.astype(int)
    df_agg["year_week"] = df_agg["year"].astype(str) + "-" + df_agg["week"].astype(str).str.zfill(2)
    df_agg["dow"] = _dow_domingo_1(df_agg["Data Trans"])

    logger.info(f"  DOW: registros após agregação líquida (item×dia): {len(df_agg):,}")
    return df_agg, pares_brutos_dow


# ---------------------------------------------------------------------------
#  Montagem do volume-modelo por SKU (alocação + reserva)
# ---------------------------------------------------------------------------

def _montar_volume_modelo(resultado, resultado_etl, logger) -> pd.DataFrame:
    """Retorna DataFrame com (item, classe, volume_modelo) por SKU.

    V_mod = quantidade no resultado (otimização) + reserva de pedido (se houver).
    Consistente com o comparador: quantidade_alocada + quantidade_reservada.
    """
    df_res = resultado.resultado.copy()
    if len(df_res) == 0:
        return pd.DataFrame(columns=["item", "classe", "volume_modelo"])

    df_res["item"] = pd.to_numeric(df_res["item"], errors="coerce").astype("Int64")

    pedidos = resultado_etl.pedidos_garantidos_por_sku or {}

    vol_por_item: Dict[int, Dict[str, Any]] = {}
    for _, row in df_res.iterrows():
        item_int = int(row["item"]) if pd.notna(row["item"]) else None
        if item_int is None:
            continue
        classe = row.get("classe", "OUTROS")
        qtd = float(row.get("quantidade", 0) or 0)
        tipo = str(row.get("tipo", "otimizacao"))
        desc = row.get("descricao", None)

        if item_int not in vol_por_item:
            vol_por_item[item_int] = {"classe": classe, "vol": 0.0, "tipo": tipo, "descricao": desc}
        vol_por_item[item_int]["vol"] += qtd
        if desc and not vol_por_item[item_int].get("descricao"):
            vol_por_item[item_int]["descricao"] = desc

    # Adicionar reserva de pedido para SKUs permitidos (tipo != reserva)
    for item_int, info in vol_por_item.items():
        if info["tipo"] != "reserva":
            info["vol"] += float(pedidos.get(item_int, pedidos.get(str(item_int), 0)))

    rows = [
        {
            "item": k, "descricao": v.get("descricao", ""),
            "classe": v["classe"], "volume_modelo": v["vol"], "tipo_modelo": v["tipo"],
        }
        for k, v in vol_por_item.items()
        if v["vol"] > 0 and v["classe"] != "OUTROS"
    ]
    df_vol = pd.DataFrame(rows)
    logger.info(f"  DOW: {len(df_vol)} SKUs com volume modelo > 0 (excl. OUTROS)")
    logger.info(f"  DOW: volume total modelo: {df_vol['volume_modelo'].sum():,.0f}")
    return df_vol


# ---------------------------------------------------------------------------
#  Função principal
# ---------------------------------------------------------------------------

def gerar_distribuicao_historica_dow(
    resultado, resultado_etl, config: Dict[str, Any], logger
) -> Path | None:
    """Gera XLSX com distribuição histórica DOW (Abordagem B: SKU direto)."""
    gran = config.get("modelo", {}).get("granularidade_demanda", "S").upper()
    if gran != "S":
        logger.info("  DOW: não executado (granularidade != semanal).")
        return None

    semana_ref = config.get("dados", {}).get("semana_ref")
    if not semana_ref:
        logger.warning("  DOW: semana_ref não definida, saída não gerada.")
        return None

    logger.info("\n>>> FASE 5: DISTRIBUIÇÃO HISTÓRICA DOW")

    # ── 1. Volume decidido pelo modelo por SKU ──
    df_vol = _montar_volume_modelo(resultado, resultado_etl, logger)
    if len(df_vol) == 0:
        logger.warning("  DOW: nenhum SKU com volume modelo.")
        return None

    # ── 2. Histórico de produção (líquido) ──
    df_hist, pares_brutos_dow = _carregar_historico_producao(config, logger)

    ano_ref, mes_ref, meses_janela = _resolver_janela_dow(config)
    periodos = _periodos_mensais(ano_ref, mes_ref, meses_janela)
    periodos_set = set(periodos)
    antes = len(df_hist)
    df_hist["_periodo"] = list(zip(df_hist["Data Trans"].dt.year, df_hist["Data Trans"].dt.month))
    df_hist = df_hist[df_hist["_periodo"].isin(periodos_set)].copy()
    df_hist = df_hist[df_hist["year_week"] != semana_ref].copy()
    logger.info(
        f"  DOW: janela {periodos} (exceto {semana_ref}) -> {antes:,} -> {len(df_hist):,}"
    )

    if len(df_hist) == 0:
        logger.warning("  DOW: histórico vazio após filtros.")
        return None

    # ── 3. Agregações históricas ──
    # Por SKU×dia
    v_i_d = (
        df_hist.groupby(["item", "dow"], as_index=False)["quantidade_ovos"]
        .sum()
        .rename(columns={"quantidade_ovos": "v_item_dia"})
    )
    v_i_total = (
        df_hist.groupby("item", as_index=False)["quantidade_ovos"]
        .sum()
        .rename(columns={"quantidade_ovos": "v_item_total"})
    )
    map_v_i_d: Dict[Tuple[int, int], float] = {
        (int(r["item"]), int(r["dow"])): float(r["v_item_dia"]) for _, r in v_i_d.iterrows()
    }
    map_v_i_total: Dict[int, float] = {
        int(r["item"]): float(r["v_item_total"]) for _, r in v_i_total.iterrows()
    }

    # Por classe×dia (para fallback F1)
    v_c_d = (
        df_hist.groupby(["classe", "dow"], as_index=False)["quantidade_ovos"]
        .sum()
        .rename(columns={"quantidade_ovos": "v_classe_dia"})
    )
    v_c_total = (
        df_hist.groupby("classe", as_index=False)["quantidade_ovos"]
        .sum()
        .rename(columns={"quantidade_ovos": "v_classe_total"})
    )
    map_v_c_d: Dict[Tuple[str, int], float] = {
        (r["classe"], int(r["dow"])): float(r["v_classe_dia"]) for _, r in v_c_d.iterrows()
    }
    map_v_c_total: Dict[str, float] = {
        r["classe"]: float(r["v_classe_total"]) for _, r in v_c_total.iterrows()
    }

    # ── 4. Datas da semana alvo ──
    week_start = _get_week_start(semana_ref)
    datas_semana = [week_start + timedelta(days=i) for i in range(7)]
    dow_por_data = {d: ((d.isoweekday() % 7) + 1) for d in datas_semana}

    # ── 5. Distribuição: V_hat(i,d) = V_mod(i) × p_hist(i,d) ──
    linhas_distrib: List[Dict[str, Any]] = []
    linhas_perfil: List[Dict[str, Any]] = []

    # Conjunto de (item, dow) com produção líquida > 0 (derivado das agregações)
    pares_liquidos_dow: set[Tuple[int, int]] = set(map_v_i_d.keys())

    # Ordenar datas_semana por DOW crescente (1=Dom, 2=Seg, ..., 7=Sáb)
    datas_por_dow = sorted(datas_semana, key=lambda d: dow_por_data[d])

    for _, row_vol in df_vol.iterrows():
        item = int(row_vol["item"])
        descricao = row_vol.get("descricao", "")
        classe = row_vol["classe"]
        vol_modelo = float(row_vol["volume_modelo"])
        tipo_modelo = row_vol.get("tipo_modelo", "otimizacao")

        v_total_sku = map_v_i_total.get(item, 0.0)
        v_total_classe = map_v_c_total.get(classe, 0.0)

        if v_total_sku > 0:
            metodo = "historico_sku"
            fallback_motivo = None
        elif v_total_classe > 0:
            metodo = "fallback_f1_classe"
            fallback_motivo = "sku_sem_historico_individual"
        else:
            metodo = "fallback_f2_sem_historico"
            fallback_motivo = "sku_e_classe_sem_historico"

        for data_d in datas_por_dow:
            dow = dow_por_data[data_d]

            if metodo == "historico_sku":
                p_dia = map_v_i_d.get((item, dow), 0.0) / v_total_sku
            elif metodo == "fallback_f1_classe":
                p_dia = map_v_c_d.get((classe, dow), 0.0) / v_total_classe
            else:
                p_dia = 0.0

            vol_dia = vol_modelo * p_dia

            # Diagnóstico de zeros
            log_zero = ""
            if vol_dia == 0 and vol_modelo > 0:
                if metodo == "fallback_f2_sem_historico":
                    log_zero = "SKU e classe sem historico na janela"
                elif metodo == "fallback_f1_classe" and map_v_c_d.get((classe, dow), 0.0) == 0:
                    if (item, dow) in pares_brutos_dow:
                        log_zero = "Classe sem producao liquida neste dia (ACA cancelado por EAC)"
                    else:
                        log_zero = "Classe nunca produzida neste dia da semana"
                elif (item, dow) in pares_brutos_dow and (item, dow) not in pares_liquidos_dow:
                    log_zero = "Producao registrada e integralmente estornada (ACA cancelado por EAC)"
                elif (item, dow) not in pares_brutos_dow:
                    log_zero = "SKU nunca produzido neste dia da semana"

            linhas_distrib.append({
                "semana_alvo": semana_ref,
                "classe": classe,
                "item": item,
                "descricao": descricao,
                "dow": dow,
                "data": data_d.isoformat(),
                "tipo_modelo": tipo_modelo,
                "volume_modelo_semanal": vol_modelo,
                "perc_hist_dia": round(p_dia, 8),
                "volume_dia_estimado": round(vol_dia, 2),
                "metodo": metodo,
                "fallback_motivo": fallback_motivo,
                "log_zero": log_zero,
            })

            linhas_perfil.append({
                "item": item,
                "descricao": descricao,
                "classe": classe,
                "dow": dow,
                "perc_hist_dia": round(p_dia, 8),
                "metodo": metodo,
                "fallback_motivo": fallback_motivo,
            })

    df_distrib = pd.DataFrame(linhas_distrib)

    if len(df_distrib) == 0:
        logger.warning("  DOW: distribuição vazia.")
        return None

    # Ordenar: classe → item → dow (1..7)
    df_distrib = df_distrib.sort_values(["classe", "item", "dow"]).reset_index(drop=True)

    # ── 6. Consolidado semanal ──
    df_consolidado = (
        df_distrib.groupby(
            ["semana_alvo", "classe", "item", "descricao", "tipo_modelo"], as_index=False
        )
        .agg(
            volume_modelo_semanal=("volume_modelo_semanal", "first"),
            volume_semana_estimado=("volume_dia_estimado", "sum"),
            metodo=("metodo", "first"),
            fallback_motivo=("fallback_motivo", "first"),
        )
    )
    df_consolidado = df_consolidado.sort_values(["classe", "item"]).reset_index(drop=True)

    # ── 7. Perfil classe (auxiliar para referência) ──
    linhas_classe: List[Dict[str, Any]] = []
    for classe in sorted(df_vol["classe"].unique()):
        vt = map_v_c_total.get(classe, 0.0)
        for data_d in datas_por_dow:
            dow = dow_por_data[data_d]
            p = map_v_c_d.get((classe, dow), 0.0) / vt if vt > 0 else 0.0
            linhas_classe.append({
                "classe": classe, "dow": dow, "data": data_d.isoformat(),
                "perc_hist_classe_dia": round(p, 8),
            })
    df_perfil_classe = pd.DataFrame(linhas_classe)

    # ── 8. Validações ──
    # Mapa item → descrição para rastreabilidade
    map_descricao = df_vol.set_index("item")["descricao"].to_dict()

    # V1: fechamento por SKU (soma 7 dias == volume_modelo)
    v1 = df_consolidado.copy()
    v1["regra"] = "Fechamento SKU-semana"
    v1["chave"] = semana_ref + "|" + v1["item"].astype(str) + "|" + v1["classe"]
    v1["item_descricao"] = v1["item"].map(map_descricao).fillna("")
    v1["valor_esperado"] = v1["volume_modelo_semanal"]
    v1["valor_calculado"] = v1["volume_semana_estimado"]
    v1["erro_abs"] = (v1["valor_calculado"] - v1["valor_esperado"]).abs()
    v1["status"] = v1["erro_abs"].apply(lambda x: "OK" if x <= 0.1 else "ALERTA")

    # V2: fechamento por classe (soma dos SKUs == soma V_mod da classe)
    vol_classe_modelo = df_vol.groupby("classe")["volume_modelo"].sum()
    vol_classe_dow = df_consolidado.groupby("classe")["volume_semana_estimado"].sum()
    v2_rows = []
    for classe in sorted(vol_classe_modelo.index):
        esp = float(vol_classe_modelo.get(classe, 0))
        calc = float(vol_classe_dow.get(classe, 0))
        v2_rows.append({
            "regra": "Fechamento classe-semana",
            "chave": f"{semana_ref}|{classe}",
            "item_descricao": "",
            "valor_esperado": esp,
            "valor_calculado": calc,
            "erro_abs": abs(calc - esp),
            "status": "OK" if abs(calc - esp) <= 0.1 else "ALERTA",
        })

    cols_val = ["regra", "chave", "item_descricao", "valor_esperado", "valor_calculado", "erro_abs", "status"]
    df_validacoes = pd.concat(
        [v1[cols_val], pd.DataFrame(v2_rows)],
        ignore_index=True,
    )
    df_distrib = aplicar_colunas_estabelecimento(df_distrib, config)
    df_consolidado = aplicar_colunas_estabelecimento(df_consolidado, config)
    df_perfil_classe = aplicar_colunas_estabelecimento(df_perfil_classe, config)
    df_validacoes = aplicar_colunas_estabelecimento(df_validacoes, config)

    # ── 9. Salvar XLSX ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(config.get("paths", {}).get("output_dir", "resultados"))
    output_dir.mkdir(exist_ok=True)
    out_path = output_dir / f"distribuicao_historica_dow_{semana_ref}_{timestamp}.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_distrib.to_excel(writer, sheet_name="distribuicao_sku_dia", index=False)
        df_consolidado.to_excel(writer, sheet_name="consolidado_sku_semana", index=False)
        df_perfil_classe.to_excel(writer, sheet_name="perfil_classe_dow", index=False)
        df_validacoes.to_excel(writer, sheet_name="validacoes", index=False)

    n_fallback = df_distrib["fallback_motivo"].notna().sum() // 7
    n_alertas = (df_validacoes["status"] == "ALERTA").sum()
    logger.info(f"  DOW: arquivo gerado: {out_path}")
    logger.info(f"  DOW: {len(df_vol)} SKUs, {len(df_distrib):,} linhas (SKU×dia)")
    logger.info(f"  DOW: {n_fallback} SKUs com fallback, {n_alertas} alertas de validação")
    return out_path
