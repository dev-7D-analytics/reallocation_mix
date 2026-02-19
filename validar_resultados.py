"""
Script de validação de sanidade dos resultados do modelo de otimização.

Verifica:
1. Regras de negócio (SKUs restritos, pedidos garantidos, etc.)
2. Consistência de dados (volumes, cálculos financeiros)
3. Adequação à modelagem (restrições respeitadas, lógica correta)
"""

import pandas as pd
import numpy as np
import sys
import glob
from pathlib import Path
from modelo_otimizacao_com_realocacao import ModeloOtimizacaoComRealocacao

def validar_regras_negocio(df_resultado, modelo):
    """Valida regras de negócio fundamentais."""
    print("\n" + "="*80)
    print("VALIDAÇÃO 1: REGRAS DE NEGÓCIO")
    print("="*80)
    
    erros = []
    avisos = []
    
    # Carregar dados necessários
    skus_restritos = set(modelo.dados.get('skus_restritos', []))
    pedidos_garantidos = modelo.dados.get('pedidos_garantidos_por_sku', {})
    df_classes = modelo.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
    df_producao = modelo.dados.get('producao', pd.DataFrame(columns=['classe', 'producao_total']))
    
    # REGRA 1: SKUs restritos não devem receber EXCEDENTE
    print("\n[REGRA 1] SKUs restritos não devem receber EXCEDENTE")
    df_restritos_excedente = df_resultado[
        (df_resultado['sku_restrito'] == True) & 
        (df_resultado['tipo'] == 'EXCEDENTE') &
        (df_resultado['quantidade'] > 0)
    ]
    if len(df_restritos_excedente) > 0:
        erros.append(f"SKUs restritos receberam EXCEDENTE: {len(df_restritos_excedente)} ocorrências")
        print(f"  ❌ ERRO: {len(df_restritos_excedente)} SKUs restritos receberam EXCEDENTE")
        print(f"     Exemplos: {df_restritos_excedente[['item', 'tipo', 'quantidade']].head(5).to_string()}")
    else:
        print(f"  ✅ OK: Nenhum SKU restrito recebeu EXCEDENTE")
    
    # REGRA 2: SKUs com pedidos garantidos devem aparecer no resultado como PEDIDO
    print("\n[REGRA 2] SKUs com pedidos garantidos devem aparecer como PEDIDO")
    skus_com_pedidos_garantidos = set(pedidos_garantidos.keys())
    skus_com_pedidos_no_resultado = set(df_resultado[df_resultado['tipo'] == 'PEDIDO']['item'].unique())
    
    skus_faltando = skus_com_pedidos_garantidos - skus_com_pedidos_no_resultado
    if len(skus_faltando) > 0:
        avisos.append(f"SKUs com pedidos garantidos não aparecem no resultado: {len(skus_faltando)}")
        print(f"  ⚠️  AVISO: {len(skus_faltando)} SKUs com pedidos garantidos não aparecem no resultado")
        print(f"     Exemplos: {list(skus_faltando)[:10]}")
    else:
        print(f"  ✅ OK: Todos os {len(skus_com_pedidos_garantidos)} SKUs com pedidos aparecem no resultado")
    
    # REGRA 3: SKUs sem pedidos não devem ter tipo PEDIDO
    print("\n[REGRA 3] SKUs sem pedidos não devem ter tipo PEDIDO")
    df_pedidos_sem_pedido = df_resultado[
        (df_resultado['tipo'] == 'PEDIDO') & 
        (df_resultado['tem_pedido'] == False)
    ]
    if len(df_pedidos_sem_pedido) > 0:
        erros.append(f"SKUs sem pedidos marcados como tipo PEDIDO: {len(df_pedidos_sem_pedido)}")
        print(f"  ❌ ERRO: {len(df_pedidos_sem_pedido)} SKUs sem pedidos marcados como PEDIDO")
        print(f"     Exemplos: {df_pedidos_sem_pedido[['item', 'tipo', 'tem_pedido']].head(5).to_string()}")
    else:
        print(f"  ✅ OK: Nenhum SKU sem pedido está marcado como PEDIDO")
    
    # REGRA 4: SKUs com usa_custo_medio_classe=True devem ter custos calculados (não NaN)
    print("\n[REGRA 4] SKUs com usa_custo_medio_classe=True devem ter custos válidos")
    df_custo_medio_sem_custo = df_resultado[
        (df_resultado['usa_custo_medio_classe'] == True) & 
        (df_resultado['custo_ytd'].isna() | (df_resultado['custo_ytd'] == 0))
    ]
    if len(df_custo_medio_sem_custo) > 0:
        erros.append(f"SKUs com usa_custo_medio_classe=True sem custo válido: {len(df_custo_medio_sem_custo)}")
        print(f"  ❌ ERRO: {len(df_custo_medio_sem_custo)} SKUs com usa_custo_medio_classe=True sem custo válido")
        print(f"     Exemplos: {df_custo_medio_sem_custo[['item', 'usa_custo_medio_classe', 'custo_ytd']].head(5).to_string()}")
    else:
        print(f"  ✅ OK: Todos os SKUs com usa_custo_medio_classe=True têm custos válidos")
    
    # REGRA 5: SKUs com usa_custo_medio_classe=False devem ter custos reais (verificar se estão em df_custos)
    print("\n[REGRA 5] SKUs com usa_custo_medio_classe=False devem ter custos reais")
    df_custos = modelo.dados.get('custos', pd.DataFrame(columns=['item']))
    skus_com_custos_reais = set(df_custos['item'].unique()) if len(df_custos) > 0 else set()
    
    df_custo_false = df_resultado[df_resultado['usa_custo_medio_classe'] == False]
    skus_custo_false = set(df_custo_false['item'].unique())
    skus_sem_custo_real = skus_custo_false - skus_com_custos_reais
    
    if len(skus_sem_custo_real) > 0:
        avisos.append(f"SKUs com usa_custo_medio_classe=False sem custo real em df_custos: {len(skus_sem_custo_real)}")
        print(f"  ⚠️  AVISO: {len(skus_sem_custo_real)} SKUs marcados como False sem custo real")
        print(f"     Exemplos: {list(skus_sem_custo_real)[:10]}")
    else:
        print(f"  ✅ OK: Todos os SKUs com usa_custo_medio_classe=False têm custos reais")
    
    return erros, avisos

def validar_consistencia_volumes(df_resultado, modelo):
    """Valida consistência de volumes e restrições."""
    print("\n" + "="*80)
    print("VALIDAÇÃO 2: CONSISTÊNCIA DE VOLUMES")
    print("="*80)
    
    erros = []
    avisos = []
    
    df_producao = modelo.dados.get('producao', pd.DataFrame(columns=['classe', 'producao_total']))
    df_classes = modelo.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
    pedidos_garantidos = modelo.dados.get('pedidos_garantidos_por_sku', {})
    
    # Mapear SKUs para classes
    item_para_classe = dict(zip(df_classes['item'], df_classes['classe']))
    
    # VALIDAÇÃO 1: Volume total por classe não deve exceder produção disponível
    print("\n[VALIDAÇÃO 1] Volume total por classe vs produção disponível")
    
    producao_por_classe = dict(zip(df_producao['classe'], df_producao['producao_total']))
    
    # Agregar volumes por classe no resultado
    df_resultado_com_classe = df_resultado.copy()
    df_resultado_com_classe['classe'] = df_resultado_com_classe['item'].map(item_para_classe)
    df_resultado_com_classe = df_resultado_com_classe[df_resultado_com_classe['classe'].notna()]
    
    volume_por_classe = df_resultado_com_classe.groupby('classe')['quantidade'].sum().to_dict()
    
    for classe, volume_total in volume_por_classe.items():
        producao_disponivel = producao_por_classe.get(classe, 0)
        
        if volume_total > producao_disponivel * 1.01:  # Tolerância de 1% para erros de arredondamento
            erros.append(f"Classe {classe}: volume alocado ({volume_total:,.0f}) > produção ({producao_disponivel:,.0f})")
            print(f"  ❌ ERRO: Classe {classe}")
            print(f"     Volume alocado: {volume_total:,.0f}")
            print(f"     Produção disponível: {producao_disponivel:,.0f}")
            print(f"     Diferença: {volume_total - producao_disponivel:,.0f}")
        else:
            print(f"  ✅ OK: Classe {classe} - Volume: {volume_total:,.0f} / Produção: {producao_disponivel:,.0f}")
    
    # VALIDAÇÃO 2: Pedidos garantidos devem estar descontados da produção
    print("\n[VALIDAÇÃO 2] Pedidos garantidos descontados da produção")
    
    # Calcular volume de pedidos por classe
    pedidos_por_classe = {}
    for item, volume_pedido in pedidos_garantidos.items():
        classe = item_para_classe.get(item)
        if classe:
            pedidos_por_classe[classe] = pedidos_por_classe.get(classe, 0) + volume_pedido
    
    for classe, volume_pedidos in pedidos_por_classe.items():
        producao_total = producao_por_classe.get(classe, 0)
        volume_excedente = volume_por_classe.get(classe, 0) - volume_pedidos
        
        if volume_excedente + volume_pedidos > producao_total * 1.01:
            avisos.append(f"Classe {classe}: pedidos + excedente ({volume_pedidos + volume_excedente:,.0f}) > produção ({producao_total:,.0f})")
            print(f"  ⚠️  AVISO: Classe {classe}")
            print(f"     Pedidos: {volume_pedidos:,.0f}")
            print(f"     Excedente: {volume_excedente:,.0f}")
            print(f"     Total: {volume_pedidos + volume_excedente:,.0f} / Produção: {producao_total:,.0f}")
        else:
            print(f"  ✅ OK: Classe {classe} - Pedidos: {volume_pedidos:,.0f}, Excedente: {volume_excedente:,.0f}, Total: {volume_pedidos + volume_excedente:,.0f}")
    
    # VALIDAÇÃO 3: SKUs restritos não devem estar em df_base (se não tiverem pedidos)
    print("\n[VALIDAÇÃO 3] SKUs restritos sem pedidos não devem estar na otimização")
    df_base = modelo.dados.get('base_otimizacao', pd.DataFrame())
    skus_restritos = set(modelo.dados.get('skus_restritos', []))
    
    if len(df_base) > 0:
        skus_em_df_base = set(df_base['item'].unique())
        skus_restritos_em_base = skus_restritos & skus_em_df_base
        
        # Verificar se esses SKUs restritos têm pedidos
        skus_restritos_com_pedidos = set(pedidos_garantidos.keys()) & skus_restritos
        
        skus_restritos_sem_pedidos_em_base = skus_restritos_em_base - skus_restritos_com_pedidos
        
        if len(skus_restritos_sem_pedidos_em_base) > 0:
            erros.append(f"SKUs restritos sem pedidos estão em df_base: {len(skus_restritos_sem_pedidos_em_base)}")
            print(f"  ❌ ERRO: {len(skus_restritos_sem_pedidos_em_base)} SKUs restritos sem pedidos estão em df_base")
            print(f"     Exemplos: {list(skus_restritos_sem_pedidos_em_base)[:10]}")
        else:
            print(f"  ✅ OK: Nenhum SKU restrito sem pedido está em df_base")
    
    return erros, avisos

def validar_calculos_financeiros(df_resultado):
    """Valida cálculos financeiros."""
    print("\n" + "="*80)
    print("VALIDAÇÃO 3: CÁLCULOS FINANCEIROS")
    print("="*80)
    
    erros = []
    avisos = []
    
    # VALIDAÇÃO 1: Receita = preço × quantidade_caixas (preço é por caixa)
    print("\n[VALIDAÇÃO 1] Receita = preço × quantidade_caixas")
    df_resultado['receita_calculada'] = df_resultado['preco'] * df_resultado['quantidade_caixas']
    df_resultado['diff_receita'] = abs(df_resultado['receita_total'] - df_resultado['receita_calculada'])
    
    df_receita_errada = df_resultado[df_resultado['diff_receita'] > 0.01]
    if len(df_receita_errada) > 0:
        erros.append(f"Receitas calculadas incorretamente: {len(df_receita_errada)} ocorrências")
        print(f"  ❌ ERRO: {len(df_receita_errada)} linhas com receita calculada incorretamente")
        print(f"     Exemplos:")
        print(df_receita_errada[['item', 'preco', 'quantidade', 'receita_total', 'receita_calculada', 'diff_receita']].head(5).to_string())
    else:
        print(f"  ✅ OK: Todas as receitas estão corretas")
    
    # VALIDAÇÃO 2: Custo = custo_ytd × quantidade_caixas (custo_ytd é por caixa)
    print("\n[VALIDAÇÃO 2] Custo = custo_ytd × quantidade_caixas")
    df_resultado['custo_calculado'] = df_resultado['custo_ytd'] * df_resultado['quantidade_caixas']
    df_resultado['diff_custo'] = abs(df_resultado['custo_total'] - df_resultado['custo_calculado'])
    
    # Filtrar apenas linhas com custo válido
    df_com_custo = df_resultado[df_resultado['custo_ytd'].notna() & (df_resultado['custo_ytd'] > 0)]
    df_custo_errado = df_com_custo[df_com_custo['diff_custo'] > 0.01]
    
    if len(df_custo_errado) > 0:
        erros.append(f"Custos calculados incorretamente: {len(df_custo_errado)} ocorrências")
        print(f"  ❌ ERRO: {len(df_custo_errado)} linhas com custo calculado incorretamente")
        print(f"     Exemplos:")
        print(df_custo_errado[['item', 'custo_ytd', 'quantidade', 'custo_total', 'custo_calculado', 'diff_custo']].head(5).to_string())
    else:
        print(f"  ✅ OK: Todos os custos estão corretos")
    
    # VALIDAÇÃO 3: Margem = receita - custo
    print("\n[VALIDAÇÃO 3] Margem = receita - custo")
    df_resultado['margem_calculada'] = df_resultado['receita_total'] - df_resultado['custo_total']
    df_resultado['diff_margem'] = abs(df_resultado['margem_total'] - df_resultado['margem_calculada'])
    
    df_margem_errada = df_resultado[df_resultado['diff_margem'] > 0.01]
    if len(df_margem_errada) > 0:
        erros.append(f"Margens calculadas incorretamente: {len(df_margem_errada)} ocorrências")
        print(f"  ❌ ERRO: {len(df_margem_errada)} linhas com margem calculada incorretamente")
        print(f"     Exemplos:")
        print(df_margem_errada[['item', 'receita_total', 'custo_total', 'margem_total', 'margem_calculada', 'diff_margem']].head(5).to_string())
    else:
        print(f"  ✅ OK: Todas as margens estão corretas")
    
    # VALIDAÇÃO 4: Margem unitária = margem_total / quantidade_caixas (quando quantidade_caixas > 0)
    print("\n[VALIDAÇÃO 4] Margem unitária = margem_total / quantidade_caixas")
    df_com_quantidade = df_resultado[df_resultado['quantidade_caixas'] > 0].copy()
    df_com_quantidade['margem_unitaria_calculada'] = df_com_quantidade['margem_total'] / df_com_quantidade['quantidade_caixas']
    df_com_quantidade['diff_margem_unitaria'] = abs(df_com_quantidade['margem_unitaria'] - df_com_quantidade['margem_unitaria_calculada'])
    
    df_margem_unitaria_errada = df_com_quantidade[df_com_quantidade['diff_margem_unitaria'] > 0.01]
    if len(df_margem_unitaria_errada) > 0:
        avisos.append(f"Margens unitárias calculadas incorretamente: {len(df_margem_unitaria_errada)} ocorrências")
        print(f"  ⚠️  AVISO: {len(df_margem_unitaria_errada)} linhas com margem unitária calculada incorretamente")
        print(f"     Exemplos:")
        print(df_margem_unitaria_errada[['item', 'margem_total', 'quantidade', 'margem_unitaria', 'margem_unitaria_calculada', 'diff_margem_unitaria']].head(5).to_string())
    else:
        print(f"  ✅ OK: Todas as margens unitárias estão corretas")
    
    return erros, avisos

def validar_modelagem(df_resultado, modelo):
    """Valida adequação à modelagem."""
    print("\n" + "="*80)
    print("VALIDAÇÃO 4: ADEQUAÇÃO À MODELAGEM")
    print("="*80)
    
    erros = []
    avisos = []
    
    df_base = modelo.dados.get('base_otimizacao', pd.DataFrame())
    skus_restritos = set(modelo.dados.get('skus_restritos', []))
    pedidos_garantidos = modelo.dados.get('pedidos_garantidos_por_sku', {})
    skus_com_pedidos = set(pedidos_garantidos.keys())
    
    # VALIDAÇÃO 1: Apenas SKUs não-restritos sem pedidos devem estar em df_base
    print("\n[VALIDAÇÃO 1] SKUs em df_base devem ser não-restritos e sem pedidos")
    
    if len(df_base) > 0:
        skus_em_base = set(df_base['item'].unique())
        skus_restritos_em_base = skus_em_base & skus_restritos
        skus_com_pedidos_em_base = skus_em_base & skus_com_pedidos
        
        if len(skus_restritos_em_base) > 0:
            erros.append(f"SKUs restritos em df_base: {len(skus_restritos_em_base)}")
            print(f"  ❌ ERRO: {len(skus_restritos_em_base)} SKUs restritos estão em df_base")
            print(f"     Exemplos: {list(skus_restritos_em_base)[:10]}")
        else:
            print(f"  ✅ OK: Nenhum SKU restrito está em df_base")
        
        if len(skus_com_pedidos_em_base) > 0:
            erros.append(f"SKUs com pedidos em df_base: {len(skus_com_pedidos_em_base)}")
            print(f"  ❌ ERRO: {len(skus_com_pedidos_em_base)} SKUs com pedidos estão em df_base")
            print(f"     Exemplos: {list(skus_com_pedidos_em_base)[:10]}")
        else:
            print(f"  ✅ OK: Nenhum SKU com pedido está em df_base")
    
    # VALIDAÇÃO 2: SKUs com pedidos devem aparecer apenas como PEDIDO no resultado
    print("\n[VALIDAÇÃO 2] SKUs com pedidos devem aparecer apenas como PEDIDO")
    
    df_pedidos_no_resultado = df_resultado[df_resultado['item'].isin(skus_com_pedidos)]
    df_pedidos_com_excedente = df_pedidos_no_resultado[df_pedidos_no_resultado['tipo'] == 'EXCEDENTE']
    
    if len(df_pedidos_com_excedente) > 0:
        erros.append(f"SKUs com pedidos receberam EXCEDENTE: {len(df_pedidos_com_excedente)}")
        print(f"  ❌ ERRO: {len(df_pedidos_com_excedente)} SKUs com pedidos receberam EXCEDENTE")
        print(f"     Exemplos: {df_pedidos_com_excedente[['item', 'tipo', 'quantidade']].head(5).to_string()}")
    else:
        print(f"  ✅ OK: SKUs com pedidos aparecem apenas como PEDIDO")
    
    # VALIDAÇÃO 3: Verificar se custos médios estão sendo usados corretamente
    print("\n[VALIDAÇÃO 3] Uso correto de custos médios vs custos reais")
    
    df_custos = modelo.dados.get('custos', pd.DataFrame(columns=['item']))
    skus_com_custos_reais = set(df_custos['item'].unique()) if len(df_custos) > 0 else set()
    
    df_custo_medio = df_resultado[df_resultado['usa_custo_medio_classe'] == True]
    skus_custo_medio = set(df_custo_medio['item'].unique())
    
    skus_custo_medio_com_custo_real = skus_custo_medio & skus_com_custos_reais
    
    if len(skus_custo_medio_com_custo_real) > 0:
        avisos.append(f"SKUs com custos reais marcados como usa_custo_medio_classe=True: {len(skus_custo_medio_com_custo_real)}")
        print(f"  ⚠️  AVISO: {len(skus_custo_medio_com_custo_real)} SKUs com custos reais marcados como True")
        print(f"     Exemplos: {list(skus_custo_medio_com_custo_real)[:10]}")
    else:
        print(f"  ✅ OK: Custos médios usados apenas para SKUs sem custos reais")
    
    return erros, avisos

def main():
    """Função principal."""
    print("="*80)
    print("VALIDAÇÃO DE SANIDADE DOS RESULTADOS")
    print("="*80)
    
    # Carregar modelo e dados
    print("\n[1/5] Carregando modelo e dados...")
    modelo = ModeloOtimizacaoComRealocacao('config.yaml')
    modelo.carregar_dados()
    
    # Carregar resultado mais recente
    print("\n[2/5] Carregando resultado mais recente...")
    arquivos = sorted(glob.glob('resultados/resultado_realocacao_*_*.csv'))
    if len(arquivos) == 0:
        print("❌ ERRO: Nenhum arquivo de resultado encontrado!")
        return
    
    arquivo_resultado = arquivos[-1]
    print(f"  Arquivo: {arquivo_resultado}")
    df_resultado = pd.read_csv(arquivo_resultado)
    print(f"  Linhas: {len(df_resultado)}")
    
    # Executar validações
    print("\n[3/5] Executando validações...")
    
    erros_total = []
    avisos_total = []
    
    erros, avisos = validar_regras_negocio(df_resultado, modelo)
    erros_total.extend(erros)
    avisos_total.extend(avisos)
    
    erros, avisos = validar_consistencia_volumes(df_resultado, modelo)
    erros_total.extend(erros)
    avisos_total.extend(avisos)
    
    erros, avisos = validar_calculos_financeiros(df_resultado)
    erros_total.extend(erros)
    avisos_total.extend(avisos)
    
    erros, avisos = validar_modelagem(df_resultado, modelo)
    erros_total.extend(erros)
    avisos_total.extend(avisos)
    
    # Resumo final
    print("\n" + "="*80)
    print("RESUMO FINAL")
    print("="*80)
    print(f"\nTotal de ERROS encontrados: {len(erros_total)}")
    print(f"Total de AVISOS encontrados: {len(avisos_total)}")
    
    if len(erros_total) > 0:
        print("\n❌ ERROS:")
        for i, erro in enumerate(erros_total, 1):
            print(f"  {i}. {erro}")
    
    if len(avisos_total) > 0:
        print("\n⚠️  AVISOS:")
        for i, aviso in enumerate(avisos_total, 1):
            print(f"  {i}. {aviso}")
    
    if len(erros_total) == 0 and len(avisos_total) == 0:
        print("\n✅ TODAS AS VALIDAÇÕES PASSARAM!")
    elif len(erros_total) == 0:
        print("\n⚠️  Validações passaram com avisos (não críticos)")
    else:
        print("\n❌ Validações falharam - correções necessárias")
    
    return len(erros_total) == 0

if __name__ == '__main__':
    sucesso = main()
    sys.exit(0 if sucesso else 1)
