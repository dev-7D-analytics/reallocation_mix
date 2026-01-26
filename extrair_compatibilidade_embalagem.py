"""
Extrai compatibilidade SKU x Embalagem do faturamento histórico.

Cria dataset de compatibilidade baseado nas combinações que já foram vendidas.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import re
from datetime import datetime
try:
    import yaml
except ImportError:
    yaml = None

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

def main():
    print("="*80)
    print("EXTRAÇÃO DE COMPATIBILIDADE SKU x EMBALAGEM")
    print("="*80)
    
    # Carregar faturamento
    print("\n[1/3] Carregando faturamento...")
    path_fat = Path("../manti_fat_2024.parquet")
    if not path_fat.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_fat}")
        return
    
    df_fat = pd.read_parquet(path_fat)
    print(f"  Registros: {len(df_fat):,}")
    
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
    
    df_fat['embalagem'] = df_fat[col_desc].apply(extrair_embalagem_descricao)
    df_fat['qtd_embalagem'] = df_fat['embalagem'].apply(calcular_qtd_embalagem)
    
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
        'descricoes_nao_capturadas': [len(df_sem_embalagem)]
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
