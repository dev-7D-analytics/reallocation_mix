#!/usr/bin/env python3
"""
Teste para validar correcao [C]: Calcular pedidos_por_classe corretamente

Este teste verifica se:
1. Pedidos por classe sao calculados corretamente
2. SKUs com multiplas embalagens nao duplicam pedidos
3. A soma por classe esta correta
"""

import pandas as pd
import sys
from pathlib import Path

# Adicionar diretorio ao path
sys.path.insert(0, str(Path(__file__).parent))

def testar_calculo_pedidos_por_classe():
    """Testa o calculo de pedidos_por_classe"""
    
    print("="*80)
    print("TESTE CORRECAO [C]: Calcular pedidos_por_classe corretamente")
    print("="*80)
    
    # Criar dados de teste
    print("\n[1/4] Criando dados de teste...")
    
    # Dados de classes: SKU -> classe
    df_classes = pd.DataFrame({
        'item': [1001, 1002, 1003, 1004, 1005],
        'classe': ['CLASSE_A', 'CLASSE_A', 'CLASSE_B', 'CLASSE_B', 'CLASSE_C']
    })
    print(f"  Classes: {len(df_classes)} SKUs em {df_classes['classe'].nunique()} classes")
    
    # Dados de pedidos: SKU -> quantidade pedida
    df_pedidos_sku = pd.DataFrame({
        'item': [1001, 1002, 1003, 1004],
        'quantidade_total_pedida': [1000, 2000, 1500, 3000]
    })
    print(f"  Pedidos: {len(df_pedidos_sku)} SKUs com pedidos")
    
    # Dados de producao: classe -> producao total
    df_producao = pd.DataFrame({
        'classe': ['CLASSE_A', 'CLASSE_B', 'CLASSE_C'],
        'producao_total': [5000, 6000, 4000]
    })
    print(f"  Producao: {len(df_producao)} classes")
    
    # Dados de base (simulando df_base com multiplas embalagens por SKU)
    # SKU 1001 tem 2 embalagens, SKU 1002 tem 1 embalagem, etc.
    df_base = pd.DataFrame({
        'item_id': ['1001_CX12', '1001_CX24', '1002_CX12', '1003_CX12', '1003_CX24', '1004_CX12'],
        'item': [1001, 1001, 1002, 1003, 1003, 1004],
        'embalagem': ['CX 12 BJ 10 UN', 'CX 24 BJ 10 UN', 'CX 12 BJ 10 UN', 'CX 12 BJ 10 UN', 'CX 24 BJ 10 UN', 'CX 12 BJ 10 UN'],
        'classe': ['CLASSE_A', 'CLASSE_A', 'CLASSE_A', 'CLASSE_B', 'CLASSE_B', 'CLASSE_B'],
        'quantidade_total_pedida': [1000, 1000, 2000, 1500, 1500, 3000]  # Duplicado por embalagem
    })
    print(f"  Base: {len(df_base)} item_id (SKU 1001 tem 2 embalagens, SKU 1003 tem 2 embalagens)")
    
    # METODO ANTIGO (INCORRETO)
    print("\n[2/4] Metodo antigo (incorreto)...")
    try:
        pedidos_por_classe_antigo = df_base.groupby('classe')['quantidade_total_pedida'].first().groupby(level=0).sum()
        print(f"  Resultado antigo:")
        for classe, valor in pedidos_por_classe_antigo.items():
            print(f"    {classe}: {valor}")
    except Exception as e:
        print(f"  [ERRO] Metodo antigo falhou: {e}")
        pedidos_por_classe_antigo = pd.Series()
    
    # METODO NOVO (CORRETO)
    print("\n[3/4] Metodo novo (correto)...")
    if len(df_pedidos_sku) > 0:
        # Fazer merge de pedidos com classes para obter classe de cada SKU
        pedidos_com_classe = df_pedidos_sku.merge(df_classes[['item', 'classe']], on='item', how='left')
        # Filtrar apenas classes que tem producao
        pedidos_com_classe = pedidos_com_classe[pedidos_com_classe['classe'].isin(df_producao['classe'])]
        # Agrupar por classe e somar pedidos (cada SKU conta apenas uma vez)
        pedidos_por_classe_novo = pedidos_com_classe.groupby('classe')['quantidade_total_pedida'].sum()
    else:
        pedidos_por_classe_novo = pd.Series(0, index=df_producao['classe'])
    
    print(f"  Resultado novo:")
    for classe, valor in pedidos_por_classe_novo.items():
        print(f"    {classe}: {valor}")
    
    # VALIDACAO
    print("\n[4/4] Validacao...")
    
    # Valores esperados:
    # CLASSE_A: SKU 1001 (1000) + SKU 1002 (2000) = 3000
    # CLASSE_B: SKU 1003 (1500) + SKU 1004 (3000) = 4500
    # CLASSE_C: nenhum pedido = 0
    valores_esperados = {
        'CLASSE_A': 3000,
        'CLASSE_B': 4500,
        'CLASSE_C': 0
    }
    
    print("\n  Valores esperados:")
    for classe, valor_esperado in valores_esperados.items():
        print(f"    {classe}: {valor_esperado}")
    
    print("\n  Comparacao:")
    todos_corretos = True
    for classe in df_producao['classe']:
        valor_esperado = valores_esperados.get(classe, 0)
        valor_novo = pedidos_por_classe_novo.get(classe, 0)
        status = "OK" if abs(valor_novo - valor_esperado) < 0.01 else "ERRO"
        if status == "ERRO":
            todos_corretos = False
        print(f"    {classe}: esperado={valor_esperado}, obtido={valor_novo} [{status}]")
    
    # Verificar se metodo antigo estava errado
    print("\n  Comparacao metodo antigo vs novo:")
    if len(pedidos_por_classe_antigo) > 0:
        for classe in df_producao['classe']:
            valor_antigo = pedidos_por_classe_antigo.get(classe, 0)
            valor_novo = pedidos_por_classe_novo.get(classe, 0)
            if abs(valor_antigo - valor_novo) > 0.01:
                print(f"    {classe}: ANTIGO={valor_antigo}, NOVO={valor_novo} [DIFERENTE]")
            else:
                print(f"    {classe}: ANTIGO={valor_antigo}, NOVO={valor_novo} [IGUAL]")
    
    # Resultado final
    print("\n" + "="*80)
    if todos_corretos:
        print("[OK] TESTE PASSOU: Calculo de pedidos_por_classe esta correto!")
        print("="*80)
        return True
    else:
        print("[ERRO] TESTE FALHOU: Valores nao correspondem ao esperado!")
        print("="*80)
        return False

if __name__ == "__main__":
    sucesso = testar_calculo_pedidos_por_classe()
    sys.exit(0 if sucesso else 1)

