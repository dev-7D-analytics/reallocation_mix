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

def carregar_config(config_path='config.yaml'):
    """Carrega configuracoes do YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

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
    output_dir = Path('inputs')
    output_dir.mkdir(exist_ok=True)
    
    # 1. Carregar estoque
    print("\n[1/3] Carregando estoque...")
    df_prod = pd.read_parquet(path_producao_bruta)
    
    # Detectar colunas
    col_item = 'ITEM' if 'ITEM' in df_prod.columns else 'CODIGO ITEM'
    col_data = 'DATA DA CONTAGEM'
    col_qtd = 'QUANTIDADE'
    
    # Converter data
    df_prod[col_data] = pd.to_datetime(df_prod[col_data], errors='coerce')
    
    # Filtrar por data (assumir que tudo esta disponivel para venda)
    data_producao = pd.to_datetime(config['dados']['data_estoque'])  # Usar data_estoque como data_producao
    
    df_filtrado = df_prod[
        (df_prod[col_data] == data_producao)
    ].copy()
    
    # Agregar por item
    df_estoque_agg = df_filtrado.groupby(col_item).agg({
        col_qtd: 'sum'
    }).reset_index()
    df_estoque_agg.columns = ['item', 'quantidade']
    df_estoque_agg = df_estoque_agg[df_estoque_agg['quantidade'] > 0]
    
    print(f"  SKUs com estoque: {len(df_estoque_agg)}")
    print(f"  Estoque total: {df_estoque_agg['quantidade'].sum():,.0f} unidades")
    
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
    
    # Atribuir classe OUTROS para SKUs sem classificacao
    df_estoque_com_classe['Classe_Produto'] = df_estoque_com_classe['Classe_Produto'].fillna('OUTROS')
    
    print(f"  SKUs com classe: {df_estoque_com_classe['Classe_Produto'].notna().sum()}")
    print(f"  SKUs sem classe (-> OUTROS): {df_estoque_com_classe['Classe_Produto'].isna().sum()}")
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
    df_producao['data_producao'] = data_producao.strftime('%Y-%m-%d')
    
    # Reordenar colunas
    df_producao = df_producao[['Classe_Produto', 'quantidade', 'data_producao']]
    
    # 5. Salvar
    arquivo_saida = output_dir / 'producao_classe.csv'
    df_producao.to_csv(arquivo_saida, index=False, encoding='utf-8')
    
    print(f"\n[OK] Dataset de producao salvo em: {arquivo_saida}")
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

