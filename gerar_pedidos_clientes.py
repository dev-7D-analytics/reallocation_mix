"""
Gera dataset de pedidos baseado na demanda histórica de SKUs GRANEL/Exportação/MARCA PROPRIA.

Este script cria "pedidos" para SKUs que não entram na otimização direta,
mas que precisam ter volume reservado baseado na demanda histórica.

A granularidade dos pedidos é controlada pelo parâmetro 'granularidade_demanda' no config.yaml:
  - D (diário): média diária por item
  - S (semanal): média semanal por item (padrão)
  - M (mensal): média mensal por item

Fluxo:
1. Carregar skus_restritos.xlsx e filtrar: ESTAB=100, STATUS=ATIVO, TIPO∈[GRANEL, Exportação, MARCA PROPRIA]
2. Carregar base de faturamento
3. Aplicar correção de estabelecimento (Estab_Corrigido)
4. Filtrar Estab_Corrigido = 100
5. Filtrar apenas SKUs da lista do passo 1
6. Converter quantidade para ovos (× 360)
7. Agregar por item + período (dia/semana/mês conforme granularidade)
8. Calcular média por período por item
9. Gerar arquivo com: Estabelecimento, item, quantidade
"""
import pandas as pd
import numpy as np
from pathlib import Path
import yaml


def carregar_config():
    """Carrega configuração do arquivo config.yaml."""
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def carregar_skus_granel_exportacao_marca_propria(config: dict) -> pd.DataFrame:
    """
    Carrega SKUs de GRANEL, Exportação e MARCA PROPRIA do arquivo skus_restritos.xlsx.
    
    Filtros aplicados:
    - ESTAB = 100
    - STATUS = ATIVO
    - TIPO ∈ ['GRANEL', 'Exportação', 'MARCA PROPRIA']
    """
    path = Path(config.get('paths', {}).get('skus_restritos', 'inputs/skus_restritos.xlsx'))
    
    if not path.exists():
        print(f"[ERRO] Arquivo não encontrado: {path}")
        return pd.DataFrame()
    
    df = pd.read_excel(path)
    total_original = len(df)
    print(f"  Total de SKUs no arquivo: {total_original}")
    
    # Detectar colunas
    col_item = None
    col_estab = None
    col_status = None
    col_tipo = None
    
    for col in df.columns:
        col_upper = col.upper()
        if col_upper == 'ITEM':
            col_item = col
        elif col_upper == 'ESTAB':
            col_estab = col
        elif col_upper == 'STATUS':
            col_status = col
        elif col_upper == 'TIPO':
            col_tipo = col
    
    if not all([col_item, col_estab, col_status, col_tipo]):
        print(f"[ERRO] Colunas necessárias não encontradas")
        print(f"  Colunas disponíveis: {list(df.columns)}")
        return pd.DataFrame()
    
    # Filtro 1: ESTAB = 100
    df = df[df[col_estab].astype(str) == '100']
    print(f"  Após filtro ESTAB=100: {len(df)}")
    
    # Filtro 2: STATUS = ATIVO
    df = df[df[col_status].str.upper() == 'ATIVO']
    print(f"  Após filtro STATUS=ATIVO: {len(df)}")
    
    # Filtro 3: TIPO ∈ ['GRANEL', 'Exportação', 'MARCA PROPRIA']
    tipos_permitidos = ['GRANEL', 'EXPORTAÇÃO', 'EXPORTACAO', 'MARCA PROPRIA']
    df = df[df[col_tipo].str.upper().isin(tipos_permitidos)]
    print(f"  Após filtro TIPO=[GRANEL, Exportação, MARCA PROPRIA]: {len(df)}")
    
    # Retornar apenas coluna item
    df_resultado = df[[col_item]].copy()
    df_resultado.columns = ['item']
    df_resultado['item'] = pd.to_numeric(df_resultado['item'], errors='coerce')
    df_resultado = df_resultado.dropna()
    df_resultado['item'] = df_resultado['item'].astype(int)
    
    return df_resultado


def aplicar_correcao_estabelecimento(df_fat: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Aplica correção de estabelecimento baseada no arquivo ESTAB CORRIGIDO.xlsx.
    
    Cria coluna Estab_Corrigido mapeando Cod.Emitente -> Estab Padrao.
    """
    path_correcao = Path(config.get('paths', {}).get('estab_corrigido', 'inputs/ESTAB CORRIGIDO.xlsx'))
    
    if not path_correcao.exists():
        print(f"  [AVISO] Arquivo de correção não encontrado: {path_correcao}")
        df_fat['Estab_Corrigido'] = df_fat['Estab']
        return df_fat
    
    # Detectar coluna de cliente
    col_cliente = 'Cod.Emitente' if 'Cod.Emitente' in df_fat.columns else None
    
    if col_cliente is None:
        print("  [AVISO] Coluna 'Cod.Emitente' não encontrada - correção não aplicada")
        df_fat['Estab_Corrigido'] = df_fat['Estab']
        return df_fat
    
    try:
        df_correcao = pd.read_excel(path_correcao)
        
        if 'Cliente' not in df_correcao.columns or 'Estab Padrao' not in df_correcao.columns:
            print("  [AVISO] Colunas 'Cliente' ou 'Estab Padrao' não encontradas")
            df_fat['Estab_Corrigido'] = df_fat['Estab']
            return df_fat
        
        # Criar mapeamento
        mapa_estab = dict(zip(df_correcao['Cliente'], df_correcao['Estab Padrao']))
        
        # Aplicar correção (vetorizado em vez de apply row-by-row)
        df_fat['Estab_Corrigido'] = (
            df_fat[col_cliente].map(mapa_estab).fillna(df_fat['Estab']).astype(int)
        )
        
        registros_corrigidos = (df_fat['Estab'] != df_fat['Estab_Corrigido']).sum()
        print(f"  Registros com Estab corrigido: {registros_corrigidos:,}")
        
    except Exception as e:
        print(f"  [AVISO] Erro ao aplicar correção: {e}")
        df_fat['Estab_Corrigido'] = df_fat['Estab']
    
    return df_fat


def calcular_media_por_periodo(df_fat: pd.DataFrame, lista_skus: list, config: dict) -> pd.DataFrame:
    """
    Calcula a média histórica por período por item para os SKUs especificados.
    
    A granularidade é definida por 'granularidade_demanda' no config.yaml:
      - D: média diária (to_period('D'))
      - S: média semanal (to_period('W'))  [padrão]
      - M: média mensal (to_period('M'))
    
    Passos:
    1. Filtrar Estab_Corrigido = 100
    2. Filtrar apenas SKUs da lista
    3. Converter quantidade para ovos (× 360)
    4. Agregar por item + período
    5. Calcular média por período por item
    """
    # Determinar granularidade
    granularidade = config.get('modelo', {}).get('granularidade_demanda', 'S').upper()
    mapa_periodo = {
        'D': ('D', 'dia', 'diária'),
        'S': ('W', 'semana', 'semanal'),
        'M': ('M', 'mês', 'mensal'),
    }
    if granularidade not in mapa_periodo:
        print(f"  [AVISO] Granularidade '{granularidade}' não reconhecida, usando S (semanal)")
        granularidade = 'S'
    
    freq_pandas, unidade, label_gran = mapa_periodo[granularidade]
    print(f"  Granularidade: {granularidade} ({label_gran})")
    
    # Detectar colunas
    col_item = 'item' if 'item' in df_fat.columns else None
    col_qtd = 'Quantidade' if 'Quantidade' in df_fat.columns else None
    col_data = 'Dt.Emissão' if 'Dt.Emissão' in df_fat.columns else None
    
    if not all([col_item, col_qtd, col_data]):
        print("[ERRO] Colunas necessárias não encontradas no faturamento")
        return pd.DataFrame()
    
    # Filtro 1: Estab_Corrigido = 100
    antes = len(df_fat)
    df = df_fat[df_fat['Estab_Corrigido'].astype(str) == '100'].copy()
    print(f"  Registros Estab_Corrigido=100: {len(df):,} de {antes:,}")
    
    # Filtro 2: Apenas SKUs da lista
    df[col_item] = pd.to_numeric(df[col_item], errors='coerce')
    df = df[df[col_item].isin(lista_skus)]
    print(f"  Registros após filtro de SKUs: {len(df):,}")
    
    if len(df) == 0:
        print("[AVISO] Nenhum registro encontrado para os SKUs especificados")
        return pd.DataFrame()
    
    # Converter quantidade para ovos (× 360)
    # A base está normalizada para caixas de 360 ovos
    df['quantidade_ovos'] = df[col_qtd] * 360
    
    # Converter data e criar período
    df[col_data] = pd.to_datetime(df[col_data], errors='coerce')
    df = df[df[col_data].notna()]
    
    # Filtrar período histórico (últimos 6 meses por padrão)
    periodo_meses = config.get('modelo', {}).get('periodo_historico_meses', 6)
    data_max = df[col_data].max()
    data_min = data_max - pd.DateOffset(months=periodo_meses)
    df = df[df[col_data] >= data_min]
    print(f"  Período considerado: {data_min.strftime('%Y-%m-%d')} a {data_max.strftime('%Y-%m-%d')}")
    
    # Criar coluna de período conforme granularidade
    df['periodo'] = df[col_data].dt.to_period(freq_pandas)
    
    # Calcular número TOTAL de períodos no intervalo (incluindo zeros)
    # Isso garante que a média reflete o volume esperado por período,
    # mesmo quando não há venda em todos os períodos
    periodo_min = data_min.to_period(freq_pandas) if hasattr(data_min, 'to_period') else pd.Timestamp(data_min).to_period(freq_pandas)
    periodo_max = data_max.to_period(freq_pandas) if hasattr(data_max, 'to_period') else pd.Timestamp(data_max).to_period(freq_pandas)
    todos_periodos = pd.period_range(start=periodo_min, end=periodo_max, freq=freq_pandas)
    num_total_periodos = len(todos_periodos)
    num_periodos_com_venda = df['periodo'].nunique()
    print(f"  Períodos no intervalo: {num_total_periodos} {unidade}(s) (com venda: {num_periodos_com_venda})")
    
    # Agregar volume total por item (soma de todo o período)
    df_total = df.groupby(col_item)['quantidade_ovos'].sum().reset_index()
    df_total.columns = ['item', 'quantidade_total']
    
    # Calcular média REAL: volume total / número total de períodos
    # Inclui períodos sem venda como zero, evitando inflar a média
    df_total['quantidade'] = df_total['quantidade_total'] / num_total_periodos
    
    # Arredondar para inteiro
    df_total['quantidade'] = df_total['quantidade'].round().astype(int)
    
    # Remover itens com quantidade muito baixa
    df_total = df_total[df_total['quantidade'] > 0]
    
    # Resultado final: item, quantidade
    df_media = df_total[['item', 'quantidade']].copy()
    
    print(f"  SKUs com demanda histórica: {len(df_media)}")
    print(f"  Média de quantidade por SKU: {df_media['quantidade'].mean():,.0f} ovos/{unidade}")
    
    return df_media, granularidade


def main():
    print("="*80)
    print("GERAÇÃO DE PEDIDOS - SKUs GRANEL/EXPORTAÇÃO/MARCA PRÓPRIA")
    print("="*80)
    
    # Carregar configuração
    config = carregar_config()
    
    # Exibir granularidade configurada
    granularidade_cfg = config.get('modelo', {}).get('granularidade_demanda', 'S').upper()
    labels = {'D': 'DIÁRIA', 'S': 'SEMANAL', 'M': 'MENSAL'}
    unidades = {'D': 'dia', 'S': 'semana', 'M': 'mês'}
    print(f"\n  Granularidade configurada: {granularidade_cfg} ({labels.get(granularidade_cfg, '?')})")
    
    # Caminhos
    path_fat = Path(config.get('paths', {}).get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
    output_path = Path("inputs/pedidos_clientes.csv")
    
    # =========================================================================
    # PASSO 1: Carregar lista de SKUs (GRANEL, Exportação, MARCA PROPRIA)
    # =========================================================================
    print("\n[1/4] Carregando lista de SKUs (GRANEL/Exportação/MARCA PROPRIA)...")
    df_skus = carregar_skus_granel_exportacao_marca_propria(config)
    
    if len(df_skus) == 0:
        print("[ERRO] Nenhum SKU encontrado para processar")
        return
    
    lista_skus = df_skus['item'].tolist()
    print(f"\n  SKUs a processar: {len(lista_skus)}")
    print(f"  Exemplos: {lista_skus[:5]}...")
    
    # =========================================================================
    # PASSO 2: Carregar base de faturamento
    # =========================================================================
    print("\n[2/4] Carregando base de faturamento...")
    
    if not path_fat.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_fat}")
        return
    
    colunas_necessarias = ['Estab', 'Cod.Emitente', 'item', 'Quantidade', 'Dt.Emissão']
    try:
        df_fat = pd.read_parquet(path_fat, columns=colunas_necessarias)
    except Exception:
        df_fat = pd.read_parquet(path_fat)
    print(f"  Registros: {len(df_fat):,}")
    
    # =========================================================================
    # PASSO 3: Aplicar correção de estabelecimento
    # =========================================================================
    print("\n[3/4] Aplicando correção de estabelecimento...")
    df_fat = aplicar_correcao_estabelecimento(df_fat, config)
    
    # =========================================================================
    # PASSO 4: Calcular média por período por item
    # =========================================================================
    label_gran = labels.get(granularidade_cfg, 'SEMANAL')
    print(f"\n[4/4] Calculando média {label_gran.lower()} histórica por SKU...")
    resultado = calcular_media_por_periodo(df_fat, lista_skus, config)
    
    if resultado is None:
        print("[AVISO] Nenhum pedido gerado")
        return
    
    df_pedidos, granularidade_usada = resultado
    
    if len(df_pedidos) == 0:
        print("[AVISO] Nenhum pedido gerado")
        return
    
    unidade = unidades.get(granularidade_usada, 'semana')
    
    # Adicionar coluna de estabelecimento
    df_pedidos.insert(0, 'Estabelecimento', 100)
    
    # Reordenar colunas: Estabelecimento, item, quantidade
    df_pedidos = df_pedidos[['Estabelecimento', 'item', 'quantidade']]
    
    # Ordenar por item
    df_pedidos = df_pedidos.sort_values('item').reset_index(drop=True)
    
    # Salvar
    output_path.parent.mkdir(exist_ok=True)
    df_pedidos.to_csv(output_path, index=False, encoding='utf-8')
    
    print("\n" + "="*80)
    print(f"RESULTADO (granularidade: {label_gran})")
    print("="*80)
    print(f"\n[OK] Arquivo gerado: {output_path}")
    print(f"  Total de SKUs com pedido: {len(df_pedidos)}")
    print(f"  Quantidade total: {df_pedidos['quantidade'].sum():,.0f} ovos/{unidade}")
    print(f"  Média por SKU: {df_pedidos['quantidade'].mean():,.0f} ovos/{unidade}")
    print(f"  Mínimo: {df_pedidos['quantidade'].min():,.0f} ovos/{unidade}")
    print(f"  Máximo: {df_pedidos['quantidade'].max():,.0f} ovos/{unidade}")
    
    # Mostrar os pedidos gerados
    print("\n" + "-"*80)
    print("PEDIDOS GERADOS")
    print("-"*80)
    print(df_pedidos.to_string(index=False))


if __name__ == "__main__":
    main()
