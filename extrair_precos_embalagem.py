"""
Extrai preços médios por (SKU, Embalagem) do faturamento histórico.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import argparse
import yaml

from typing import Dict

# Importar função de extração de embalagem
sys.path.append(str(Path(__file__).parent))
from extrair_compatibilidade_embalagem import extrair_embalagem_descricao

def carregar_config(config_path: str = 'config.yaml') -> Dict:
    """Carrega configuracoes do YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Extrai preços médios por (SKU, Embalagem) do faturamento."
    )
    parser.add_argument(
        "--estab",
        nargs="+",
        help="Filtra registros pela coluna 'Estab'. Aceita múltiplos valores.",
    )
    return parser.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    config = carregar_config()
    print("="*80)
    print("EXTRAÇÃO DE PREÇOS POR (SKU, EMBALAGEM)")
    print("="*80)
    
    # Carregar faturamento
    print("\n[1/3] Carregando faturamento...")
    path_fat = Path(config['paths'].get('faturamento', '../manti_fat_2024.parquet'))
    if not path_fat.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_fat}")
        return
    
    df_fat = pd.read_parquet(path_fat)
    print(f"  Registros: {len(df_fat):,}")

    # Filtro opcional por estabelecimento
    if args.estab:
        if 'Estab' not in df_fat.columns:
            print("[ERRO] Coluna 'Estab' não encontrada no faturamento.")
            print(f"  Colunas disponíveis: {list(df_fat.columns)}")
            return
        df_fat = df_fat[df_fat['Estab'].astype(str).isin(args.estab)].copy()
        print(f"  Filtro aplicado na coluna 'Estab': {args.estab}")
        print(f"  Registros após filtro: {len(df_fat):,}")
        if len(df_fat) == 0:
            print("  [ALERTA] Nenhum registro encontrado após o filtro. Encerrando.")
            return
    
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
    
    # Extrair embalagem
    print("\n[2/3] Extraindo embalagens e calculando preços...")
    df_fat['embalagem'] = df_fat[col_desc].apply(extrair_embalagem_descricao)
    
    # Filtrar registros válidos
    df_validos = df_fat[
        (df_fat['embalagem'].notna()) &
        (df_fat['item'].notna()) &
        (df_fat['Quantidade'] > 0) &
        (df_fat['Receita Liquida'] > 0)
    ].copy()
    
    # Calcular preço unitário
    df_validos['preco_unitario'] = df_validos['Receita Liquida'] / df_validos['Quantidade']
    
    # Remover outliers (preços muito altos ou muito baixos)
    q1 = df_validos['preco_unitario'].quantile(0.01)
    q99 = df_validos['preco_unitario'].quantile(0.99)
    df_validos = df_validos[
        (df_validos['preco_unitario'] >= q1) &
        (df_validos['preco_unitario'] <= q99)
    ].copy()
    
    print(f"  Registros válidos: {len(df_validos):,}")
    print(f"  Faixa de preços: R$ {df_validos['preco_unitario'].min():.2f} - R$ {df_validos['preco_unitario'].max():.2f}")
    
    # Agregar por (item, embalagem)
    print("\n[3/3] Agregando preços por (SKU, Embalagem)...")
    df_precos = df_validos.groupby(['item', 'embalagem']).agg({
        'preco_unitario': ['mean', 'median', 'std', 'count'],
        'Quantidade': 'sum',
        'Receita Liquida': 'sum',
        col_desc: 'first'
    }).reset_index()
    
    # Flatten column names
    df_precos.columns = [
        'item', 'embalagem', 'preco_medio', 'preco_mediano', 
        'preco_std', 'num_transacoes', 'volume_total', 'receita_total', 'descricao_item'
    ]
    
    # Calcular preço ponderado por volume
    df_precos['preco_ponderado'] = df_precos['receita_total'] / df_precos['volume_total']
    
    # Usar preço ponderado como preço principal
    df_precos['preco'] = df_precos['preco_ponderado']
    
    # Ordenar por volume
    df_precos = df_precos.sort_values('volume_total', ascending=False)
    
    # Salvar
    output_path = Path("inputs/precos_sku_embalagem.csv")
    output_path.parent.mkdir(exist_ok=True)
    df_precos.to_csv(output_path, index=False, encoding='utf-8')
    
    print(f"\n[OK] Dataset salvo: {output_path}")
    print(f"  Combinações únicas: {len(df_precos):,}")
    print(f"  SKUs únicos: {df_precos['item'].nunique():,}")
    print(f"  Embalagens únicas: {df_precos['embalagem'].nunique():,}")
    print(f"  Granjas únicas: {df_validos['Estab'].nunique():,}")
    
    # Estatísticas
    print("\n" + "="*80)
    print("ESTATÍSTICAS")
    print("="*80)
    print(f"\nPreço médio geral: R$ {df_precos['preco'].mean():.2f}")
    print(f"Preço mediano geral: R$ {df_precos['preco'].median():.2f}")
    print(f"\nTop 10 combinações por volume:")
    print(df_precos.head(10)[['item', 'embalagem', 'preco', 'volume_total']].to_string(index=False))

if __name__ == "__main__":
    main()
