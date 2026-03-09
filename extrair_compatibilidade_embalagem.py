"""
Extrai compatibilidade SKU x Embalagem do faturamento histórico.

Cria dataset de compatibilidade baseado nas combinações que já foram vendidas.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
try:
    import yaml
except ImportError:
    yaml = None


OVERRIDE_COLUNAS = [
    "item",
    "descricao",
    "descricao_normalizada",
    "embalagem",
    "qtd_embalagem",
    "ativo",
    "origem",
    "observacao",
    "updated_at",
]

def load_config(config_path: str = "config.yaml") -> dict:
    """
    Loads configuration from YAML file.
    
    Args:
        config_path: Path to config.yaml file
        
    Returns:
        Dictionary with configuration settings
    """
    if yaml is None:
        print("[ERRO] Dependencia 'pyyaml' nao instalada. Retornando configuracao vazia.")
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        print(f"[ERRO] Arquivo de configuração não encontrado: {config_path}")
        return {}

def extrair_embalagem_descricao(descricao: str) -> str:
    """
    Extrai padrão de embalagem da descrição do item.
    
    Exemplos:
    - "OVO MANTIQUEIRA GR BRCO CX 12 BJ 30 UN" -> "CX 12 BJ 30 UN"
    - "OVO BRANCO JUMBO GRANEL MANTIQUEIRA CX COM 12 BJ DE 30 UN" -> "CX 12 BJ 30 UN"
    - "2000211 - OVO MANTIQUEIRA GR BRCO CX 12 BJ 30 UN" -> "CX 12 BJ 30 UN"
    """
    if pd.isna(descricao):
        return None
    
    desc_upper = str(descricao).upper()
    
    # Padrão 1: CX COM [número] BJ DE [número] UN (mais comum no faturamento)
    # Ex: "CX COM 12 BJ DE 30 UN"
    padrao1 = r'CX\s+COM\s+(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao1, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 2: CX [número] BJ [número] UN (sem COM/DE)
    # Ex: "CX 12 BJ 30 UN"
    padrao2 = r'CX\s+(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao2, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 3: CX [número] BJ DE [número] UN
    # Ex: "CX 12 BJ DE 30 UN"
    padrao3 = r'CX\s+(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao3, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 4: CX COM [número] BJ [número] UN (sem DE)
    # Ex: "CX COM 12 BJ 30 UN"
    padrao4 = r'CX\s+COM\s+(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao4, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 4b: CX COM[número] BJ DE [número] UN (COM sem espaço)
    # Ex: "CX COM12 BJ DE 30 UN"
    padrao4b = r'CX\s+COM(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao4b, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 4c: CX COM[número] BJ [número] UN (COM sem espaço, sem DE)
    # Ex: "CX COM12 BJ 30 UN"
    padrao4c = r'CX\s+COM(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao4c, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 4d: CX C/ [número] BJ [número] UN (C/ = COM abreviado)
    # Ex: "CX C/ 6 BJ 30 UN"
    padrao4d = r'CX\s+C/\s*(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao4d, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 4e: CX C/ [número] BJ DE [número] UN
    # Ex: "CX C/ 6 BJ DE 30 UN"
    padrao4e = r'CX\s+C/\s*(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
    match = re.search(padrao4e, desc_upper)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 5: CX [número] BJ (sem UN, tentar encontrar UN depois)
    padrao5 = r'CX\s+(?:COM\s+)?(\d+)\s+BJ'
    match5 = re.search(padrao5, desc_upper)
    if match5:
        num_bj = match5.group(1)
        # Tentar encontrar quantidade de unidades em qualquer lugar
        padrao_un = r'(\d+)\s+UN'
        match_un = re.search(padrao_un, desc_upper)
        if match_un:
            num_un = match_un.group(1)
            return f"CX {num_bj} BJ {num_un} UN"
        else:
            # Se não encontrou UN, retornar apenas CX BJ (mas não será válido)
            return None
    
    # Padrão 6: [número] BJ [número] UN (sem CX no início)
    # Ex: "20 BJ 30 UN" -> "CX 20 BJ 30 UN"
    # Só aplicar se não encontrou nenhum padrão com CX antes
    if 'CX' not in desc_upper:
        padrao6 = r'(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
        match = re.search(padrao6, desc_upper)
        if match:
            num_bj = match.group(1)
            num_un = match.group(2)
            return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 6b: [número]BJ [número]UN (sem espaços, sem CX)
    # Ex: "20BJ 30UN" -> "CX 20 BJ 30 UN"
    if 'CX' not in desc_upper:
        padrao6b = r'(\d+)BJ\s+(\d+)UN'
        match = re.search(padrao6b, desc_upper)
        if match:
            num_bj = match.group(1)
            num_un = match.group(2)
            return f"CX {num_bj} BJ {num_un} UN"
    
    # Padrão 6c: [número] BJ DE [número] UN (sem CX)
    # Ex: "20 BJ DE 30 UN" -> "CX 20 BJ 30 UN"
    if 'CX' not in desc_upper:
        padrao6c = r'(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
        match = re.search(padrao6c, desc_upper)
        if match:
            num_bj = match.group(1)
            num_un = match.group(2)
            return f"CX {num_bj} BJ {num_un} UN"

    # -------------------------------------------------------------------------
    # EXTENSÕES (sem regressão):
    # Os padrões originais acima permanecem intactos. Os blocos abaixo só rodam
    # quando nada foi capturado antes, ampliando cobertura para variações novas.
    # -------------------------------------------------------------------------

    # Normalização auxiliar para padrões com abreviações e barras
    desc_norm = desc_upper
    desc_norm = re.sub(r'\bBANDEJAS?\b', 'BJ', desc_norm)
    desc_norm = re.sub(r'\bBDJ\b', 'BJ', desc_norm)
    desc_norm = re.sub(r'\bBD\b', 'BJ', desc_norm)
    desc_norm = re.sub(r'\bBAND\.?\b', 'BJ', desc_norm)
    desc_norm = re.sub(r'\bUNDS?\b', 'UN', desc_norm)
    desc_norm = re.sub(r'BJ\.', 'BJ', desc_norm)
    desc_norm = re.sub(r'\s+', ' ', desc_norm).strip()

    # Padrão 7: CX/10 BJ COM 30 UN | CX 3BJ 10UN | CX/10 BJ 30UN
    # Aceita BJ/BD, barra opcional e espaços opcionais (ex.: "3BJ", "10UN")
    padrao7 = r'CX\s*(?:/|COM|C/|C)?\s*(\d+)\s*(?:BJ|BD)\s*(?:COM|DE)?\s*(\d+)(?:\s*UN)?'
    match = re.search(padrao7, desc_norm)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"

    # Padrão 8: CX C 24BJ DE 10 UN | CX C 24BJ 10UN
    # (variação com "C" abreviado e "24BJ" sem espaço)
    padrao8 = r'CX\s+C\s*(\d+)\s*BJ\s+(?:DE|COM)?\s*(\d+)\s*UN?'
    match = re.search(padrao8, desc_norm)
    if match:
        num_bj = match.group(1)
        num_un = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"

    # Padrão 8b: C/18UN CX/20UN (ordem invertida, sem BJ explícito)
    # Interpreta como 20 bandejas de 18 unidades.
    padrao8b = r'C/\s*(\d+)\s*UN.*?CX\s*/\s*(\d+)\s*UN'
    match = re.search(padrao8b, desc_norm)
    if match:
        num_un = match.group(1)
        num_bj = match.group(2)
        return f"CX {num_bj} BJ {num_un} UN"

    # Padrão 8c: CX C/ 12UN (sem BJ explícito)
    # Interpreta como 1 bandeja com 12 unidades por caixa.
    padrao8c = r'CX\s*(?:C/|C|COM)\s*(\d+)\s*UN\b'
    match = re.search(padrao8c, desc_norm)
    if match:
        num_un = match.group(1)
        return f"CX 1 BJ {num_un} UN"

    # Padrão 9: CX 30 DUZIAS | CX/30 DZ | CX COM 30 DZ
    # Normaliza dúzia para BJ 12 UN para manter compatibilidade downstream.
    padrao9 = r'CX\s*(?:/|COM|C/)?\s*(\d+)\s*(?:DUZIAS?|DUZIA|DZ|DZS)\b'
    match = re.search(padrao9, desc_norm)
    if match:
        num_bj = match.group(1)
        return f"CX {num_bj} BJ 12 UN"

    # Padrão 9b: CX/60 MEIA DZ | CX 30 MEIA DUZIA
    # Meia dúzia = 6 ovos por bandeja.
    padrao9b = r'CX\s*(?:/|COM|C/)?\s*(\d+)\s*MEIA\s*(?:DUZIA|DZ)\b'
    match = re.search(padrao9b, desc_norm)
    if match:
        num_bj = match.group(1)
        return f"CX {num_bj} BJ 6 UN"

    # Padrão 9c: MEIA DZ 60 ESTOJOS (sem CX explícito)
    # Interpreta quantidade de estojos como bandejas equivalentes.
    padrao9c = r'MEIA\s*(?:DUZIA|DZ)\s*(\d+)\s*ESTOJ'
    match = re.search(padrao9c, desc_norm)
    if match:
        num_bj = match.group(1)
        return f"CX {num_bj} BJ 6 UN"

    # Padrão 9d: 1440 DZ (sem CX explícito)
    padrao9d = r'\b(\d+)\s*(?:DUZIAS?|DUZIA|DZ|DZS)\b'
    match = re.search(padrao9d, desc_norm)
    if match and 'CX' not in desc_norm:
        num_bj = match.group(1)
        return f"CX {num_bj} BJ 12 UN"

    # Padrão 10: CX180 | CX240 | CX360 (total de ovos na caixa)
    # Converte para formato canônico: CX 1 BJ <total> UN
    padrao10 = r'\bCX\s*(\d{2,4})\b'
    match = re.search(padrao10, desc_norm)
    if match:
        total_ovos = int(match.group(1))
        if total_ovos > 0:
            return f"CX 1 BJ {total_ovos} UN"
    
    # Se não encontrou padrão, retornar None
    return None

def calcular_qtd_embalagem(embalagem: str) -> int:
    """
    Calcula quantidade de ovos na embalagem.
    
    Exemplo: "CX 12 BJ 30 UN" -> 12 * 30 = 360
    """
    if pd.isna(embalagem) or embalagem is None:
        return None
    
    embalagem_upper = str(embalagem).upper()
    
    # Padrão: CX [número] BJ [número] UN
    padrao = r'CX\s+(\d+)\s+BJ\s+(\d+)\s+UN'
    match = re.search(padrao, embalagem_upper)
    
    if match:
        num_bj = int(match.group(1))
        num_un = int(match.group(2))
        return num_bj * num_un
    
    return None


def normalizar_descricao_chave(descricao: str) -> str:
    """
    Normaliza descrição para chave técnica de deduplicação/lookup.
    """
    if pd.isna(descricao) or descricao is None:
        return ""
    texto = str(descricao).upper().strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def _resolver_path_override(config: Dict) -> Path:
    path = config.get("paths", {}).get("embalagens_override", "inputs/embalagens_override.xlsx")
    return Path(path)


def _carregar_override(path_override: Path) -> pd.DataFrame:
    """
    Carrega override editável, garantindo schema mínimo.
    """
    if path_override.exists():
        df = pd.read_excel(path_override, sheet_name="override")
    else:
        df = pd.DataFrame(columns=OVERRIDE_COLUNAS)

    # Garantir todas as colunas previstas (sem apagar custom cols)
    for col in OVERRIDE_COLUNAS:
        if col not in df.columns:
            df[col] = None

    # Padronizar campos
    df["item"] = pd.to_numeric(df["item"], errors="coerce")
    df["descricao"] = df["descricao"].astype(str).replace("nan", "").fillna("")
    df["descricao_normalizada"] = df["descricao_normalizada"].fillna("").astype(str)
    sem_chave = df["descricao_normalizada"].str.strip() == ""
    df.loc[sem_chave, "descricao_normalizada"] = df.loc[sem_chave, "descricao"].apply(normalizar_descricao_chave)
    # Permite cadastro mínimo (item + embalagem) mesmo sem descrição.
    sem_chave = df["descricao_normalizada"].str.strip() == ""
    df.loc[sem_chave & df["item"].notna(), "descricao_normalizada"] = (
        "__ITEM__" + df.loc[sem_chave & df["item"].notna(), "item"].astype(int).astype(str)
    )

    df["embalagem"] = df["embalagem"].astype(str).replace("nan", "").fillna("")
    df["qtd_embalagem"] = pd.to_numeric(df["qtd_embalagem"], errors="coerce")
    qtd_calc = df["embalagem"].apply(calcular_qtd_embalagem)
    df["qtd_embalagem"] = df["qtd_embalagem"].fillna(qtd_calc)

    # ativo default true
    ativos = df["ativo"].astype(str).str.strip().str.lower()
    map_bool = {"true": True, "1": True, "sim": True, "yes": True, "y": True, "false": False, "0": False, "nao": False, "não": False, "no": False, "n": False}
    df["ativo"] = ativos.map(map_bool)
    df["ativo"] = df["ativo"].fillna(True)

    # origem default manual
    df["origem"] = df["origem"].fillna("manual")
    df["observacao"] = df["observacao"].fillna("")
    df["updated_at"] = df["updated_at"].fillna("")
    return df


def _validar_override(df_override: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Retorna (validos, inconsistencias).
    """
    if len(df_override) == 0:
        return df_override.copy(), pd.DataFrame(columns=OVERRIDE_COLUNAS + ["motivo_inconsistencia"])

    df = df_override.copy()
    motivos: List[str] = []
    for _, row in df.iterrows():
        if pd.isna(row["item"]):
            motivos.append("item_invalido")
            continue
        if str(row["embalagem"]).strip() == "":
            motivos.append("embalagem_vazia")
            continue
        if pd.isna(row["qtd_embalagem"]) or float(row["qtd_embalagem"]) <= 0:
            motivos.append("qtd_embalagem_invalida")
            continue
        motivos.append("")

    df["motivo_inconsistencia"] = motivos
    inconsist = df[df["motivo_inconsistencia"] != ""].copy()
    validos = df[df["motivo_inconsistencia"] == ""].copy()
    if "motivo_inconsistencia" in validos.columns:
        validos = validos.drop(columns=["motivo_inconsistencia"])
    return validos, inconsist


def _aplicar_fallback_override(
    df_fat: pd.DataFrame,
    col_desc: str,
    df_override_validos: pd.DataFrame,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Aplica fallback de embalagem via override para casos não capturados no regex.
    Prioridade:
      1) chave item + descricao_normalizada
      2) item com cadastro único ativo
    """
    out = df_fat.copy()
    out["descricao_normalizada"] = out[col_desc].apply(normalizar_descricao_chave)
    out["fonte_embalagem"] = np.where(out["embalagem"].notna(), "regex", "nao_encontrada")

    stats = {
        "fallback_override_chave": 0,
        "fallback_override_item": 0,
    }

    if len(df_override_validos) == 0:
        return out, stats

    ov = df_override_validos.copy()
    ov["item"] = ov["item"].astype(int)
    ov["descricao_normalizada"] = ov["descricao_normalizada"].astype(str)

    # 1) Fallback por chave item + descricao_normalizada
    ov_chave = ov.drop_duplicates(subset=["item", "descricao_normalizada"], keep="last")
    mapa_emb_chave = {
        (int(r["item"]), str(r["descricao_normalizada"])): r["embalagem"]
        for _, r in ov_chave.iterrows()
    }
    mapa_qtd_chave = {
        (int(r["item"]), str(r["descricao_normalizada"])): int(r["qtd_embalagem"])
        for _, r in ov_chave.iterrows()
    }

    mask_sem_regex = out["embalagem"].isna() & out["item"].notna()
    for idx in out[mask_sem_regex].index:
        key = (int(out.at[idx, "item"]), str(out.at[idx, "descricao_normalizada"]))
        emb = mapa_emb_chave.get(key)
        if emb:
            out.at[idx, "embalagem"] = emb
            out.at[idx, "qtd_embalagem"] = mapa_qtd_chave.get(key)
            out.at[idx, "fonte_embalagem"] = "override_chave"
            stats["fallback_override_chave"] += 1

    # 2) Fallback por item (somente quando item tem 1 embalagem ativa no override)
    mask_ainda_sem = out["embalagem"].isna() & out["item"].notna()
    if mask_ainda_sem.any():
        ov_item_cnt = ov.groupby("item")["embalagem"].nunique().reset_index(name="n")
        itens_unicos = set(ov_item_cnt[ov_item_cnt["n"] == 1]["item"].astype(int).tolist())
        ov_item_unico = ov[ov["item"].isin(itens_unicos)].drop_duplicates(subset=["item"], keep="last")
        mapa_emb_item = {int(r["item"]): r["embalagem"] for _, r in ov_item_unico.iterrows()}
        mapa_qtd_item = {int(r["item"]): int(r["qtd_embalagem"]) for _, r in ov_item_unico.iterrows()}
        for idx in out[mask_ainda_sem].index:
            item = int(out.at[idx, "item"])
            emb = mapa_emb_item.get(item)
            if emb:
                out.at[idx, "embalagem"] = emb
                out.at[idx, "qtd_embalagem"] = mapa_qtd_item.get(item)
                out.at[idx, "fonte_embalagem"] = "override_item"
                stats["fallback_override_item"] += 1

    return out, stats


def _atualizar_override_com_auto(
    df_override_existente: pd.DataFrame,
    df_fat: pd.DataFrame,
    col_desc: str,
) -> Tuple[pd.DataFrame, int]:
    """
    Faz append de novos padrões automáticos (regex) no override, sem sobrescrever existentes.
    """
    base = df_override_existente.copy()
    if len(base) == 0:
        base = pd.DataFrame(columns=OVERRIDE_COLUNAS)

    for col in OVERRIDE_COLUNAS:
        if col not in base.columns:
            base[col] = None

    # Chave de unicidade: item + descricao_normalizada + embalagem
    existentes = set(
        (
            int(r["item"]) if pd.notna(r["item"]) else -1,
            str(r["descricao_normalizada"]),
            str(r["embalagem"]),
        )
        for _, r in base.iterrows()
        if pd.notna(r["item"]) and str(r["descricao_normalizada"]).strip() != "" and str(r["embalagem"]).strip() != ""
    )

    auto = df_fat[df_fat["embalagem_auto"].notna() & df_fat["item"].notna()].copy()
    if len(auto) == 0:
        return base, 0

    auto["descricao_norm"] = auto[col_desc].apply(normalizar_descricao_chave)
    auto_seed = (
        auto.groupby(["item", "descricao_norm", "embalagem_auto"], as_index=False)
        .agg(qtd_embalagem=("qtd_embalagem", "first"), descricao=(col_desc, "first"))
    )

    novos = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for _, r in auto_seed.iterrows():
        key = (int(r["item"]), str(r["descricao_norm"]), str(r["embalagem_auto"]))
        if key in existentes:
            continue
        novos.append(
            {
                "item": int(r["item"]),
                "descricao": r["descricao"],
                "descricao_normalizada": r["descricao_norm"],
                "embalagem": r["embalagem_auto"],
                "qtd_embalagem": r["qtd_embalagem"],
                "ativo": True,
                "origem": "auto_seed",
                "observacao": "",
                "updated_at": ts,
            }
        )
        existentes.add(key)

    if len(novos) == 0:
        return base, 0

    df_novos = pd.DataFrame(novos)
    if len(base) == 0:
        out = df_novos.copy()
    else:
        out = pd.concat([base, df_novos], ignore_index=True)
    return out, len(df_novos)


def _salvar_override(
    path_override: Path,
    df_override: pd.DataFrame,
    df_inconsistencias: pd.DataFrame,
    auditoria: Dict[str, int],
) -> None:
    """
    Salva planilha de override preservando aba editável e auditoria operacional.
    """
    path_override.parent.mkdir(exist_ok=True, parents=True)
    df_override_export = df_override.copy()
    for col in OVERRIDE_COLUNAS:
        if col not in df_override_export.columns:
            df_override_export[col] = None
    df_override_export = df_override_export[OVERRIDE_COLUNAS]

    df_auditoria = pd.DataFrame([auditoria])
    with pd.ExcelWriter(path_override, engine="openpyxl") as writer:
        df_override_export.to_excel(writer, sheet_name="override", index=False)
        if len(df_inconsistencias) > 0:
            df_inconsistencias.to_excel(writer, sheet_name="inconsistencias", index=False)
        else:
            pd.DataFrame(columns=OVERRIDE_COLUNAS + ["motivo_inconsistencia"]).to_excel(
                writer, sheet_name="inconsistencias", index=False
            )
        df_auditoria.to_excel(writer, sheet_name="auditoria", index=False)

def main():
    print("="*80)
    print("EXTRAÇÃO DE COMPATIBILIDADE SKU x EMBALAGEM")
    print("="*80)
    
    # Carregar faturamento (do config.yaml ou fallback)
    print("\n[1/3] Carregando faturamento...")
    config = load_config()
    path_fat = Path(config.get('paths', {}).get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
    if not path_fat.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_fat}")
        return
    
    df_fat = pd.read_parquet(path_fat)
    print(f"  Registros totais: {len(df_fat):,}")

    # Filtrar por estabelecimento
    estabelecimentos = config.get('dados', {}).get('estabelecimentos', [100])
    if 'Estab' in df_fat.columns and estabelecimentos:
        antes = len(df_fat)
        df_fat = df_fat[df_fat['Estab'].isin(estabelecimentos)].copy()
        print(f"  Filtro Estab={estabelecimentos}: {antes:,} -> {len(df_fat):,}")

    # Filtrar apenas SKUs ativos no estabelecimento
    path_skus = Path(config.get('paths', {}).get('skus_restritos', 'inputs/skus_restritos.xlsx'))
    if path_skus.exists():
        df_skus = pd.read_excel(path_skus)
        if 'STATUS' in df_skus.columns and 'ESTAB' in df_skus.columns:
            df_ativos = df_skus[
                (df_skus['STATUS'] == 'ATIVO') &
                (df_skus['ESTAB'].isin(estabelecimentos))
            ]
            skus_ativos = set(df_ativos['item'].astype(int).tolist())
            if 'item' in df_fat.columns:
                antes = len(df_fat)
                df_fat['item'] = pd.to_numeric(df_fat['item'], errors='coerce')
                df_fat = df_fat[df_fat['item'].isin(skus_ativos)].copy()
                print(f"  Filtro SKUs ativos: {antes:,} -> {len(df_fat):,}")

    print(f"  Registros após filtros: {len(df_fat):,}")

    # Detectar coluna de descrição (tentar múltiplas opções)
    col_desc = None
    # Prioridade 1: "ITEM -  DESCRIÇÃO"
    for col in df_fat.columns:
        if col == 'ITEM -  DESCRIÇÃO' or col == 'ITEM - DESCRIÇÃO':
            col_desc = col
            break
    
    # Prioridade 2: qualquer coluna com "descri" e "item"
    if col_desc is None:
        for col in df_fat.columns:
            if 'descri' in col.lower() and 'item' in col.lower():
                col_desc = col
                break
    
    # Prioridade 3: "Descrição do item"
    if col_desc is None:
        for col in df_fat.columns:
            if col == 'Descrição do item':
                col_desc = col
                break
    
    if col_desc is None:
        print("[ERRO] Coluna de descrição não encontrada")
        print(f"  Colunas disponíveis: {list(df_fat.columns)}")
        return
    
    print(f"  Coluna de descrição: {col_desc}")
    
    # Mostrar alguns exemplos para debug
    print(f"\n  Exemplos de descrições (primeiras 5):")
    exemplos = df_fat[col_desc].dropna().head(5).tolist()
    for i, ex in enumerate(exemplos, 1):
        print(f"    {i}. {ex}")
    
    # Extrair embalagem
    print("\n[2/3] Extraindo embalagens das descrições...")
    
    # Estatísticas antes
    tem_cx = df_fat[col_desc].str.contains('CX', case=False, na=False).sum()
    print(f"  Registros com 'CX' na descrição: {tem_cx:,} ({tem_cx/len(df_fat)*100:.1f}%)")
    
    df_fat['embalagem_auto'] = df_fat[col_desc].apply(extrair_embalagem_descricao)
    df_fat['embalagem'] = df_fat['embalagem_auto']
    df_fat['qtd_embalagem'] = df_fat['embalagem'].apply(calcular_qtd_embalagem)

    # Fallback manual via inputs/embalagens_override.xlsx (sem sobrescrever regex)
    path_override = _resolver_path_override(config)
    df_override = _carregar_override(path_override)
    df_override_validos, df_override_incons = _validar_override(df_override)
    df_fat, stats_override = _aplicar_fallback_override(df_fat, col_desc, df_override_validos)
    # Recalcular qtd para casos preenchidos via override
    mask_qtd_na = df_fat["qtd_embalagem"].isna() & df_fat["embalagem"].notna()
    if mask_qtd_na.any():
        df_fat.loc[mask_qtd_na, "qtd_embalagem"] = df_fat.loc[mask_qtd_na, "embalagem"].apply(calcular_qtd_embalagem)

    # Atualizar override com novos padrões automáticos, preservando entradas existentes
    df_override_atualizado, novos_auto_seed = _atualizar_override_com_auto(df_override, df_fat, col_desc)
    df_override_validos_final, df_override_incons_final = _validar_override(df_override_atualizado)
    auditoria_override = {
        "data_geracao": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "override_total_linhas": len(df_override_atualizado),
        "override_validas": len(df_override_validos_final),
        "override_inconsistencias": len(df_override_incons_final),
        "fallback_override_chave": stats_override["fallback_override_chave"],
        "fallback_override_item": stats_override["fallback_override_item"],
        "novos_auto_seed_adicionados": novos_auto_seed,
        "capturas_regex": int((df_fat["fonte_embalagem"] == "regex").sum()) if "fonte_embalagem" in df_fat.columns else 0,
    }
    _salvar_override(path_override, df_override_atualizado, df_override_incons_final, auditoria_override)
    
    # Estatísticas após extração
    embalagens_extraidas = df_fat['embalagem'].notna().sum()
    print(f"  Embalagens extraídas: {embalagens_extraidas:,} ({embalagens_extraidas/len(df_fat)*100:.1f}%)")
    
    # Mostrar exemplos de descrições com CX mas sem embalagem extraída (para debug)
    df_sem_embalagem = df_fat[
        (df_fat[col_desc].str.contains('CX', case=False, na=False)) &
        (df_fat['embalagem'].isna())
    ]
    if len(df_sem_embalagem) > 0:
        print(f"\n  [DEBUG] {len(df_sem_embalagem):,} descrições com 'CX' mas sem embalagem extraída")
        print(f"  Exemplos:")
        for ex in df_sem_embalagem[col_desc].unique()[:5]:
            print(f"    - {ex}")
    
    # Filtrar apenas registros com embalagem válida
    df_validos = df_fat[
        (df_fat['embalagem'].notna()) &
        (df_fat['qtd_embalagem'].notna()) &
        (df_fat['item'].notna()) &
        (df_fat['Quantidade'] > 0)
    ].copy()
    
    print(f"\n  Registros com embalagem válida: {len(df_validos):,} ({len(df_validos)/len(df_fat)*100:.1f}%)")
    
    # Agregar por (item, embalagem) para criar compatibilidade
    print("\n[3/3] Criando dataset de compatibilidade...")
    df_compat = df_validos.groupby(['item', 'embalagem']).agg({
        'Quantidade': 'sum',
        'Receita Liquida': 'sum',
        col_desc: 'first'
    }).reset_index()
    
    df_compat['qtd_embalagem'] = df_compat['embalagem'].apply(calcular_qtd_embalagem)
    
    # Adicionar informações do SKU
    df_compat = df_compat.rename(columns={
        col_desc: 'descricao_item',
        'Quantidade': 'volume_total_vendido',
        'Receita Liquida': 'receita_total'
    })
    
    # Ordenar por volume
    df_compat = df_compat.sort_values('volume_total_vendido', ascending=False)
    
    # Adicionar timestamp e metadados para auditoria
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Salvar dataset principal
    output_path = Path("inputs/compatibilidade_sku_embalagem.csv")
    output_path.parent.mkdir(exist_ok=True)
    df_compat.to_csv(output_path, index=False, encoding='utf-8')
    
    # Criar relatorio de auditoria
    relatorio_auditoria = {
        'data_geracao': [timestamp],
        'total_registros_faturamento': [len(df_fat)],
        'registros_com_cx': [tem_cx],
        'percentual_com_cx': [tem_cx/len(df_fat)*100 if len(df_fat) > 0 else 0],
        'embalagens_extraidas': [embalagens_extraidas],
        'percentual_extraido': [embalagens_extraidas/len(df_fat)*100 if len(df_fat) > 0 else 0],
        'registros_validos': [len(df_validos)],
        'percentual_validos': [len(df_validos)/len(df_fat)*100 if len(df_fat) > 0 else 0],
        'combinacoes_unicas': [len(df_compat)],
        'skus_unicos': [df_compat['item'].nunique()],
        'embalagens_unicas': [df_compat['embalagem'].nunique()],
        'volume_total_vendido': [df_compat['volume_total_vendido'].sum()],
        'receita_total': [df_compat['receita_total'].sum()],
        'descricoes_nao_capturadas': [len(df_sem_embalagem)],
        'fallback_override_chave': [stats_override.get("fallback_override_chave", 0)],
        'fallback_override_item': [stats_override.get("fallback_override_item", 0)],
        'novos_auto_seed_adicionados': [novos_auto_seed],
    }
    
    df_auditoria = pd.DataFrame(relatorio_auditoria)
    path_auditoria = Path("inputs/compatibilidade_sku_embalagem_auditoria.csv")
    df_auditoria.to_csv(path_auditoria, index=False, encoding='utf-8')
    
    # Salvar exemplos de descricoes nao capturadas para analise
    if len(df_sem_embalagem) > 0:
        df_descricoes_nao_capturadas = df_sem_embalagem[[col_desc]].drop_duplicates()
        df_descricoes_nao_capturadas.columns = ['descricao_nao_capturada']
        path_descricoes = Path("inputs/descricoes_nao_capturadas.csv")
        df_descricoes_nao_capturadas.to_csv(path_descricoes, index=False, encoding='utf-8')
        print(f"\n[INFO] Descricoes nao capturadas salvas: {path_descricoes}")
    
    print(f"\n[OK] Dataset salvo: {output_path}")
    print(f"  Combinações únicas: {len(df_compat):,}")
    print(f"  SKUs únicos: {df_compat['item'].nunique():,}")
    print(f"  Embalagens únicas: {df_compat['embalagem'].nunique():,}")
    print(f"\n[OK] Relatorio de auditoria salvo: {path_auditoria}")
    print(f"[OK] Override de embalagens atualizado: {path_override}")
    print(
        "  Override: "
        f"{stats_override.get('fallback_override_chave', 0)} por chave, "
        f"{stats_override.get('fallback_override_item', 0)} por item, "
        f"{novos_auto_seed} novos auto_seed"
    )
    
    # Estatísticas
    print("\n" + "="*80)
    print("ESTATÍSTICAS")
    print("="*80)
    print(f"\nTop 10 combinações por volume:")
    print(df_compat.head(10)[['item', 'embalagem', 'qtd_embalagem', 'volume_total_vendido']].to_string(index=False))
    
    print(f"\nEmbalagens mais comuns:")
    embalagens_count = df_compat.groupby('embalagem').agg({
        'item': 'nunique',
        'volume_total_vendido': 'sum'
    }).sort_values('volume_total_vendido', ascending=False)
    embalagens_count.columns = ['num_skus', 'volume_total']
    print(embalagens_count.head(10).to_string())

if __name__ == "__main__":
    main()
