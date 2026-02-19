"""
Script para gerar dataset de producao por classe de produtos.

Este script agrega o estoque atual por classe de produtos para criar
um dataset de producao que sera usado como input do modelo de otimizacao.

Formato de saida:
- Classe_Produto: Nome da classe biologica
- quantidade: Quantidade total de producao da classe
- data_producao: Data da producao (formato YYYY-MM-DD)

Autor: Romulo Brito
Data: 2025-01-XX
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import yaml

from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem

def carregar_config(config_path='config.yaml'):
    """Carrega configuracoes do YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def extract_week(df, date_column):
    """Extract week number from a date column."""
    return pd.to_datetime(df[date_column]).dt.isocalendar().week

def extract_year(df, date_column):
    """Extract year number from a date column."""
    return pd.to_datetime(df[date_column]).dt.isocalendar().year

def get_week_start_date(year_week):
    """Get the Monday date of the given ISO year-week."""
    year, week = year_week.split('-')
    return pd.to_datetime(f"{year}-W{week}-1", format="%G-W%V-%u")

def main():
    """Funcao principal."""
    print("="*80)
    print("GERADOR DE DATASET DE PRODUCAO POR CLASSE")
    print("="*80)
    
    # Carregar config
    config = carregar_config()
    
    # Caminhos
    path_producao_bruta = Path(config['paths']['producao_bruta'])
    path_classes = Path(config['paths']['classes'])
    path_output = Path(config['paths']['producao']) 
    
    # 1. Carregar produção
    print("\n[1/3] Carregando produção...")
    df_prod = pd.read_excel(path_producao_bruta, sheet_name="CE0302", skiprows=1)
    
    # Detectar colunas
    col_item = 'Cod Item' if 'Cod Item' in df_prod.columns else 'CODIGO ITEM'
    col_data = 'Data Trans'
    col_qtd = 'QUANTIDADE CORRIGIDA'
    
    # Converter data
    df_prod[col_data] = pd.to_datetime(df_prod[col_data], errors='coerce')
    
    # Calcular embalagem e quantidade em ovos
    df_prod['embalagem'] = df_prod["Desc Item"].apply(extrair_embalagem_descricao)
    df_prod['qtd_embalagem'] = df_prod['embalagem'].apply(calcular_qtd_embalagem)
    df_prod['quantidade'] = df_prod[col_qtd] * df_prod['qtd_embalagem']
    
    # Filtrar por período conforme granularidade
    granularidade = config.get('modelo', {}).get('granularidade_demanda', 'S').upper()
    
    if granularidade == 'D':
        # Diário: filtrar por data específica
        data_ref = config['dados'].get('data_ref')
        if not data_ref:
            raise ValueError("Para granularidade diária (D), é necessário definir 'data_ref' em dados no config.yaml")
        data_ref = pd.to_datetime(data_ref)
        df_filtrado = df_prod[df_prod[col_data].dt.date == data_ref.date()].copy().reset_index(drop=True)
        periodo_desc = f"dia {data_ref.strftime('%Y-%m-%d')}"
        data_producao = data_ref
        
    elif granularidade == 'M':
        # Mensal: filtrar por mês da data de referência
        data_ref = config['dados'].get('data_ref')
        if not data_ref:
            raise ValueError("Para granularidade mensal (M), é necessário definir 'data_ref' em dados no config.yaml")
        data_ref = pd.to_datetime(data_ref)
        df_filtrado = df_prod[
            (df_prod[col_data].dt.year == data_ref.year) &
            (df_prod[col_data].dt.month == data_ref.month)
        ].copy().reset_index(drop=True)
        periodo_desc = f"mês {data_ref.strftime('%Y-%m')}"
        data_producao = data_ref.replace(day=1)
        
    else:
        # Semanal (padrão): filtrar por semana ISO
        semana_ref = config['dados']['semana_ref']
        df_prod['week'] = extract_week(df_prod, col_data)
        df_prod['year'] = extract_year(df_prod, col_data)
        df_prod['year_week'] = df_prod['year'].astype(str) + '-' + df_prod['week'].astype(str).str.zfill(2)
        df_filtrado = df_prod[df_prod['year_week'] == semana_ref].copy().reset_index(drop=True)
        periodo_desc = f"semana {semana_ref}"
        data_producao = get_week_start_date(semana_ref)
    
    print(f"  Granularidade: {granularidade} ({periodo_desc})")
    print(f"  Registros no período: {len(df_filtrado)}")

    # Agregar por item
    df_estoque_agg = df_filtrado.groupby(col_item).agg({
        'quantidade': 'sum'
    }).reset_index()
    df_estoque_agg.columns = ['item', 'quantidade']
    df_estoque_agg = df_estoque_agg[df_estoque_agg['quantidade'] > 0]
    
    print(f"  SKUs com produção: {len(df_estoque_agg)}")
    print(f"  Produção total: {df_estoque_agg['quantidade'].sum():,.0f} unidades")
    
    # 2. Carregar classes
    print("\n[2/3] Carregando classificacao de SKUs...")
    df_classes = pd.read_excel(path_classes)
    
    # Detectar coluna de classe
    col_classe = None
    for col in df_classes.columns:
        if 'classe' in col.lower() and 'produto' in col.lower():
            col_classe = col
            break
    
    if col_classe is None:
        raise ValueError("Coluna de classe nao encontrada em base_skus_classes.xlsx")
    
    df_classes = df_classes[['item', col_classe]].copy()
    df_classes.columns = ['item', 'Classe_Produto']
    
    # Merge com estoque
    df_estoque_com_classe = df_estoque_agg.merge(df_classes, on='item', how='left')
    
    # Contagem ANTES do fillna (após fillna, isna() sempre retorna 0)
    n_com_classe = df_estoque_com_classe['Classe_Produto'].notna().sum()
    n_sem_classe = df_estoque_com_classe['Classe_Produto'].isna().sum()
    
    # Atribuir classe OUTROS para SKUs sem classificacao
    df_estoque_com_classe['Classe_Produto'] = df_estoque_com_classe['Classe_Produto'].fillna('OUTROS')
    
    print(f"  SKUs com classe: {n_com_classe}")
    print(f"  SKUs sem classe (-> OUTROS): {n_sem_classe}")
    print(f"  Classes unicas: {df_estoque_com_classe['Classe_Produto'].nunique()}")
    
    # 3. Agregar por classe
    print("\n[3/3] Agregando producao por classe...")
    df_producao = df_estoque_com_classe.groupby('Classe_Produto')['quantidade'].sum().reset_index()
    df_producao = df_producao[df_producao['quantidade'] > 0]
    df_producao = df_producao.sort_values('quantidade', ascending=False)
    
    print(f"  Classes com producao: {len(df_producao)}")
    print(f"  Producao total: {df_producao['quantidade'].sum():,.0f} unidades")
    
    print("\n  Distribuicao por classe (top 10):")
    for _, row in df_producao.head(10).iterrows():
        print(f"    {row['Classe_Produto']}: {row['quantidade']:,.0f} unidades")
    
    # 4. Adicionar data de producao
    df_producao['data_producao'] = data_producao
    
    # Reordenar colunas
    df_producao = df_producao[['Classe_Produto', 'quantidade', 'data_producao']]
    
    # 5. Salvar
    df_producao.to_csv(path_output, index=False, encoding='utf-8')
    
    print(f"\n[OK] Dataset de producao salvo em: {path_output}")
    print(f"  Total de linhas: {len(df_producao)}")
    print(f"  Colunas: {', '.join(df_producao.columns)}")
    
    # Estatisticas
    print("\n  ESTATISTICAS:")
    print(f"    Producao media por classe: {df_producao['quantidade'].mean():,.0f} unidades")
    print(f"    Producao mediana por classe: {df_producao['quantidade'].median():,.0f} unidades")
    print(f"    Producao minima: {df_producao['quantidade'].min():,.0f} unidades")
    print(f"    Producao maxima: {df_producao['quantidade'].max():,.0f} unidades")
    
    return df_producao

if __name__ == '__main__':
    df_producao = main()

