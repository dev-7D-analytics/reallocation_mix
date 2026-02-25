"""
Extrai preços médios por (SKU, Embalagem) do dataset de custos.
Calcula preço como: Receita Liquida / Quantidade
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
from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem

def carregar_config(config_path: str = 'config.yaml') -> Dict:
    """Carrega configuracoes do YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Extrai preços médios por (SKU, Embalagem) do dataset de custos."
    )
    parser.add_argument(
        "--estab",
        nargs="+",
        help="Filtra registros pela coluna 'Estab'. Aceita múltiplos valores.",
    )
    parser.add_argument(
        "--mes",
        type=int,
        help="Filtra registros pelo mês (1-12).",
    )
    parser.add_argument(
        "--ano",
        type=int,
        help="Filtra registros pelo ano (ex: 2025).",
    )
    parser.add_argument(
        "--arquivo",
        type=str,
        help="Caminho do arquivo de custos (sobrescreve config.yaml).",
    )
    parser.add_argument(
        "--meses_janela",
        type=int,
        default=1,
        help="Janela de meses a partir do mês/ano especificado (ex: 6 = últimos 6 meses).",
    )
    return parser.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    config = carregar_config()
    print("="*80)
    print("EXTRAÇÃO DE PREÇOS POR (SKU, EMBALAGEM)")
    print("="*80)
    
    # Carregar dataset de custos
    print("\n[1/3] Carregando dataset de custos...")
    
    # Usar arquivo do argumento ou do config
    if args.arquivo:
        path_custo = Path(args.arquivo)
    else:
        path_custo = Path(config['paths'].get('custos', 'inputs/MANTI-PRIC_Custos_12012026.parquet'))
    
    if not path_custo.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_custo}")
        return
    
    print(f"  Arquivo: {path_custo}")
    
    # Ler apenas colunas necessárias para economizar memória
    colunas_necessarias = ['Estab', 'item', 'MÊS', 'ano', 'Quantidade', 'Receita Liquida', 'Descrição do item', 'UF']
    
    # Ler parquet (mesma abordagem que funciona no notebook)
    df_custo = pd.read_parquet(path_custo, columns=colunas_necessarias, engine='pyarrow')
    
    print(f"  Registros totais: {len(df_custo):,}")
    
    # Converter tipos para numérico (tratamento importante do código de verificação)
    print("  Convertendo tipos para numérico...")
    df_custo['Receita Liquida'] = pd.to_numeric(df_custo['Receita Liquida'], errors='coerce')
    df_custo['Quantidade'] = pd.to_numeric(df_custo['Quantidade'], errors='coerce')
    df_custo['item'] = pd.to_numeric(df_custo['item'], errors='coerce')
    df_custo['MÊS'] = pd.to_numeric(df_custo['MÊS'], errors='coerce')
    df_custo['ano'] = pd.to_numeric(df_custo['ano'], errors='coerce')
    df_custo['Estab'] = pd.to_numeric(df_custo['Estab'], errors='coerce')
    
    # Remover registros com valores inválidos após conversão
    antes_conversao = len(df_custo)
    df_custo = df_custo[
        (df_custo['item'].notna()) &
        (df_custo['MÊS'].notna()) &
        (df_custo['ano'].notna()) &
        (df_custo['Estab'].notna())
    ].copy()
    removidos_invalidos = antes_conversao - len(df_custo)
    if removidos_invalidos > 0:
        print(f"  Removidos {removidos_invalidos:,} registros com valores inválidos após conversão")
    
    print(f"  Registros após conversão: {len(df_custo):,}")
    
    # FILTRO OBRIGATÓRIO: Remover exportação (UF != 'EX')
    if 'UF' in df_custo.columns:
        antes_filtro_uf = len(df_custo)
        df_custo = df_custo[df_custo['UF'] != 'EX'].copy()
        removidos_exportacao = antes_filtro_uf - len(df_custo)
        if removidos_exportacao > 0:
            print(f"  Filtro UF != 'EX': removidos {removidos_exportacao:,} registros de exportação")
            print(f"  Registros após filtro UF: {len(df_custo):,}")
    else:
        print("  [AVISO] Coluna 'UF' não encontrada. Não foi possível filtrar exportação.")

    # Aplicar filtros
    filtros_aplicados = []
    
    # Filtro por estabelecimento
    # Se nao foi passado via argumento, usar do config.yaml
    if args.estab:
        estab_filtro = args.estab
    else:
        # Ler do config.yaml (estab_custo ou estabelecimentos)
        dados_config = config.get('dados', {})
        estab_filtro = dados_config.get('estab_custo', dados_config.get('estabelecimentos', [100]))
        # Se for lista, usar o primeiro elemento
        if isinstance(estab_filtro, list):
            estab_filtro = estab_filtro[0] if len(estab_filtro) > 0 else 100
        estab_filtro = [str(estab_filtro)]  # Converter para lista de strings
    
    if estab_filtro:
        if 'Estab' not in df_custo.columns:
            print("[ERRO] Coluna 'Estab' não encontrada no dataset de custos.")
            print(f"  Colunas disponíveis: {list(df_custo.columns)}")
            return
        antes_filtro_estab = len(df_custo)
        df_custo = df_custo[df_custo['Estab'].astype(str).isin(estab_filtro)].copy()
        removidos_estab = antes_filtro_estab - len(df_custo)
        filtros_aplicados.append(f"Estab={estab_filtro}")
        print(f"  Filtro Estab: {estab_filtro} -> {len(df_custo):,} registros")
        if removidos_estab > 0:
            print(f"  Removidos {removidos_estab:,} registros de outros estabelecimentos")
    
    # Filtro por mês/ano com janela de tempo
    # Se não veio via CLI, ler do config.yaml (mesmos params que custos/demanda)
    dados_config = config.get('dados', {})
    mes_filtro = args.mes or dados_config.get('mes_preco', dados_config.get('mes_custo'))
    ano_filtro = args.ano or dados_config.get('ano_preco', dados_config.get('ano_custo'))
    meses_janela = args.meses_janela if args.mes else dados_config.get('meses_janela_preco', dados_config.get('meses_janela_custo', 6))

    if mes_filtro and ano_filtro:
        if 'MÊS' not in df_custo.columns or 'ano' not in df_custo.columns:
            print("[ERRO] Colunas 'MÊS' ou 'ano' não encontradas no dataset de custos.")
            print(f"  Colunas disponíveis: {list(df_custo.columns)}")
            return
        
        if meses_janela > 1:
            periodos = []
            for i in range(meses_janela):
                mes = mes_filtro - i
                ano = ano_filtro
                while mes <= 0:
                    mes += 12
                    ano -= 1
                periodos.append((ano, mes))
            
            print(f"  Aplicando janela de {meses_janela} meses a partir de {ano_filtro}-{mes_filtro:02d}...")
            print(f"  Períodos incluídos: {', '.join([f'{a}-{m:02d}' for a, m in periodos])}")
            
            periodos_set = set(periodos)
            antes_periodo = len(df_custo)
            df_custo['_periodo'] = list(zip(df_custo['ano'], df_custo['MÊS']))
            df_custo = df_custo[df_custo['_periodo'].isin(periodos_set)].drop(columns=['_periodo']).copy()
            filtros_aplicados.append(f"janela={meses_janela}meses")
            print(f"  Registros após filtro de período: {len(df_custo):,} (removidos {antes_periodo - len(df_custo):,})")
        else:
            df_custo = df_custo[df_custo['MÊS'] == mes_filtro].copy()
            filtros_aplicados.append(f"MÊS={mes_filtro}")
            print(f"  Filtro Mês: {mes_filtro} -> {len(df_custo):,} registros")
            
            df_custo = df_custo[df_custo['ano'] == ano_filtro].copy()
            filtros_aplicados.append(f"ano={ano_filtro}")
            print(f"  Filtro Ano: {ano_filtro} -> {len(df_custo):,} registros")
    else:
        print("  [AVISO] Sem filtro de período (mes/ano não definidos no CLI nem no config.yaml)")
    
    if len(df_custo) == 0:
        print("  [ALERTA] Nenhum registro encontrado após os filtros. Encerrando.")
        return
    
    if filtros_aplicados:
        print(f"  Filtros aplicados: {', '.join(filtros_aplicados)}")
        print(f"  Registros após filtros: {len(df_custo):,}")
    
    # Detectar coluna de descrição (tentar múltiplas opções)
    col_desc = None
    # Prioridade 1: "ITEM -  DESCRIÇÃO"
    for col in df_custo.columns:
        if col == 'ITEM -  DESCRIÇÃO' or col == 'ITEM - DESCRIÇÃO':
            col_desc = col
            break
    
    # Prioridade 2: qualquer coluna com "descri" e "item"
    if col_desc is None:
        for col in df_custo.columns:
            if 'descri' in col.lower() and 'item' in col.lower():
                col_desc = col
                break
    
    # Prioridade 3: "Descrição do item"
    if col_desc is None:
        for col in df_custo.columns:
            if col == 'Descrição do item':
                col_desc = col
                break
    
    if col_desc is None:
        print("[ERRO] Coluna de descrição não encontrada")
        print(f"  Colunas disponíveis: {list(df_custo.columns)}")
        return
    
    # Extrair embalagem
    print("\n[2/3] Extraindo embalagens e calculando preços...")
    df_custo['embalagem'] = df_custo[col_desc].apply(extrair_embalagem_descricao)
    
    # FILTROS OBRIGATÓRIOS: Remover devoluções/estornos
    # 1. Quantidade > 0 (remove devoluções)
    # 2. Receita Liquida > 0 (remove estornos/registros sem receita)
    # 3. embalagem e item válidos
    antes_filtros_validos = len(df_custo)
    df_validos = df_custo[
        (df_custo['embalagem'].notna()) &
        (df_custo['item'].notna()) &
        (df_custo['Quantidade'] > 0) &
        (df_custo['Receita Liquida'] > 0)
    ].copy()
    removidos_invalidos = antes_filtros_validos - len(df_validos)
    if removidos_invalidos > 0:
        print(f"  Filtros de validação: removidos {removidos_invalidos:,} registros inválidos (devoluções/estornos)")
        print(f"  Registros válidos: {len(df_validos):,}")
    
    # Calcular ovos por caixa
    df_validos['ovos_por_caixa'] = df_validos['embalagem'].apply(calcular_qtd_embalagem)
    df_validos = df_validos[df_validos['ovos_por_caixa'].notna()].copy()
    
    # Calcular preço: Receita Liquida / Quantidade (método validado)
    print("  Calculando preço = Receita Liquida / Quantidade...")
    df_validos['preco_unitario'] = df_validos['Receita Liquida'] / df_validos['Quantidade']
    
    # Remover outliers (preços muito altos ou muito baixos) - percentis 1-99
    antes_filtro_outliers = len(df_validos)
    q1 = df_validos['preco_unitario'].quantile(0.01)
    q99 = df_validos['preco_unitario'].quantile(0.99)
    df_validos = df_validos[
        (df_validos['preco_unitario'] >= q1) &
        (df_validos['preco_unitario'] <= q99)
    ].copy()
    removidos_outliers = antes_filtro_outliers - len(df_validos)
    if removidos_outliers > 0:
        print(f"  Filtro de outliers (percentis 1-99%): removidos {removidos_outliers:,} registros")
        print(f"  Faixa de preços mantida: R$ {q1:.2f} - R$ {q99:.2f}")
    
    print(f"  Registros válidos após todos os filtros: {len(df_validos):,}")
    if len(df_validos) > 0:
        print(f"  Faixa de preços final: R$ {df_validos['preco_unitario'].min():.2f} - R$ {df_validos['preco_unitario'].max():.2f}")
    
    # Agregar por (item, embalagem, ano_mes) primeiro (média ponderada por volume)
    print("\n[3/3] Agregando preços por (SKU, Embalagem)...")
    
    # Criar coluna ano_mes para agregação intermediária
    df_validos['ano_mes'] = df_validos['ano'].astype(str) + '-' + df_validos['MÊS'].astype(str).str.zfill(2)
    
    # Primeira agregação: por (item, embalagem, ano_mes)
    # Calcular receita e volume totais, depois preço ponderado = receita_total / volume_total
    print("  Agregando por (item, embalagem, ano_mes)...")
    df_precos_ano_mes = df_validos.groupby(['item', 'embalagem', 'ano_mes']).agg({
        'Quantidade': 'sum',
        'Receita Liquida': 'sum',
        col_desc: 'first'
    }).reset_index()
    
    # Calcular preço ponderado por ano_mes
    df_precos_ano_mes['preco_ponderado_ano_mes'] = df_precos_ano_mes['Receita Liquida'] / df_precos_ano_mes['Quantidade']
    df_precos_ano_mes.columns = ['item', 'embalagem', 'ano_mes', 'volume_ano_mes', 'receita_ano_mes', 'descricao_item', 'preco_ponderado_ano_mes']
    
    # Segunda agregação: por (item, embalagem) usando média ponderada dos valores de ano_mes
    print("  Agregando por (item, embalagem) usando média ponderada dos valores de ano_mes...")
    
    # Calcular receita e volume totais por (item, embalagem)
    df_precos = df_precos_ano_mes.groupby(['item', 'embalagem']).agg({
        'volume_ano_mes': 'sum',
        'receita_ano_mes': 'sum',
        'descricao_item': 'first',
        'ano_mes': 'count'  # Número de meses com dados
    }).reset_index()
    df_precos.columns = ['item', 'embalagem', 'volume_total', 'receita_total', 'descricao_item', 'num_meses']
    
    # Calcular preço final ponderado por volume total
    df_precos['preco_ponderado'] = df_precos['receita_total'] / df_precos['volume_total']
    df_precos['preco'] = df_precos['preco_ponderado']
    
    # Adicionar estatísticas adicionais para referência
    df_precos['preco_medio'] = df_precos['preco_ponderado']  # Mesmo valor, mas mantém compatibilidade
    df_precos['num_transacoes'] = df_precos['num_meses']  # Aproximação
    
    # Ordenar por volume
    df_precos = df_precos.sort_values('volume_total', ascending=False)
    
    print(f"  Combinações (item+embalagem) antes filtro ativos: {len(df_precos):,}")

    # Filtrar apenas SKUs ativos no estabelecimento
    path_skus = Path(config.get('paths', {}).get('skus_restritos', 'inputs/skus_restritos.xlsx'))
    estabelecimentos = config.get('dados', {}).get('estabelecimentos', [100])
    if path_skus.exists():
        df_skus = pd.read_excel(path_skus)
        if 'STATUS' in df_skus.columns and 'ESTAB' in df_skus.columns:
            df_ativos = df_skus[
                (df_skus['STATUS'] == 'ATIVO') &
                (df_skus['ESTAB'].isin(estabelecimentos))
            ]
            skus_ativos = set(df_ativos['item'].astype(int).tolist())
            antes = len(df_precos)
            df_precos = df_precos[df_precos['item'].isin(skus_ativos)]
            print(f"  Filtro SKUs ativos (ESTAB={estabelecimentos}): {antes} -> {len(df_precos):,}")

    # Preparar dados finais (apenas colunas necessárias para o modelo)
    df_precos_final = df_precos[['item', 'embalagem', 'preco']].copy()
    
    # Salvar em CSV e Excel
    output_path_csv = Path("inputs/precos_sku_embalagem.csv")
    output_path_excel = Path("inputs/precos_sku_embalagem.xlsx")
    output_path_csv.parent.mkdir(exist_ok=True)
    
    # Salvar CSV (mantém compatibilidade)
    df_precos_final.to_csv(output_path_csv, index=False, encoding='utf-8')
    
    # Salvar Excel
    try:
        df_precos_final.to_excel(output_path_excel, index=False, engine='openpyxl')
        print(f"\n[OK] Dataset salvo:")
        print(f"  CSV: {output_path_csv}")
        print(f"  Excel: {output_path_excel}")
    except ImportError:
        print(f"\n[OK] Dataset salvo: {output_path_csv}")
        print(f"  [AVISO] openpyxl não instalado. Excel não foi salvo.")
    except Exception as e:
        print(f"\n[OK] Dataset salvo: {output_path_csv}")
        print(f"  [AVISO] Erro ao salvar Excel: {e}")
    
    print(f"  Combinações únicas: {len(df_precos_final):,}")
    print(f"  SKUs únicos: {df_precos_final['item'].nunique():,}")
    print(f"  Embalagens únicas: {df_precos_final['embalagem'].nunique():,}")
    print(f"  Estabelecimentos únicos: {df_validos['Estab'].nunique():,}")
    
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
