"""
Módulo de salvamento de resultados.

Funções:
- criar_aba_estatisticas: Cria DataFrame com estatísticas e resumo executivo
- salvar_resultados: Salva resultados em CSV e Excel com múltiplas abas
- salvar_demanda_historica: Salva o cálculo da demanda histórica em Excel

Autor: Romulo Brito
"""

import logging
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, Any


def criar_aba_estatisticas(
    resultado: pd.DataFrame,
    resumo_classe: pd.DataFrame,
    comparativo: Dict[str, float],
    resultado_etl: Any,
    config: dict
) -> pd.DataFrame:
    """Cria DataFrame com estatísticas e resumo executivo."""
    estatisticas = []
    
    # 1. PRODUÇÃO vs PEDIDOS
    producao_total = resultado_etl.producao_por_classe.sum()
    pedidos_total = sum(resultado_etl.pedidos_garantidos_por_sku.values()) if resultado_etl.pedidos_garantidos_por_sku else 0
    excedente_total = sum(resultado_etl.producao_excedente_por_classe.values()) if resultado_etl.producao_excedente_por_classe else producao_total
    
    estatisticas.append({'Categoria': 'PRODUÇÃO vs PEDIDOS', 'Métrica': 'Produção Total', 'Valor': f'{producao_total:,.0f}', 'Unidade': 'ovos'})
    estatisticas.append({'Categoria': 'PRODUÇÃO vs PEDIDOS', 'Métrica': 'Pedidos/Reservas Totais', 'Valor': f'{pedidos_total:,.0f}', 'Unidade': 'ovos'})
    estatisticas.append({'Categoria': 'PRODUÇÃO vs PEDIDOS', 'Métrica': 'Produção Excedente', 'Valor': f'{excedente_total:,.0f}', 'Unidade': 'ovos'})
    if producao_total > 0:
        estatisticas.append({'Categoria': 'PRODUÇÃO vs PEDIDOS', 'Métrica': 'Percentual Excedente', 'Valor': f'{excedente_total/producao_total*100:.1f}', 'Unidade': '%'})
    
    # 2. TOTAIS DA OTIMIZAÇÃO
    qtd_total = resultado['quantidade'].sum()
    receita_total = resultado['receita_total'].sum() if 'receita_total' in resultado.columns else 0
    custo_total = resultado['custo_total'].sum() if 'custo_total' in resultado.columns else 0
    margem_total = resultado['margem_total'].sum() if 'margem_total' in resultado.columns else 0
    margem_pct = (margem_total / receita_total * 100) if receita_total > 0 else 0
    
    estatisticas.append({'Categoria': 'TOTAIS', 'Métrica': 'Quantidade Total Alocada', 'Valor': f'{qtd_total:,.0f}', 'Unidade': 'ovos'})
    estatisticas.append({'Categoria': 'TOTAIS', 'Métrica': 'Receita Total', 'Valor': f'R$ {receita_total:,.2f}', 'Unidade': 'R$'})
    estatisticas.append({'Categoria': 'TOTAIS', 'Métrica': 'Custo Total', 'Valor': f'R$ {custo_total:,.2f}', 'Unidade': 'R$'})
    estatisticas.append({'Categoria': 'TOTAIS', 'Métrica': 'Margem Total', 'Valor': f'R$ {margem_total:,.2f}', 'Unidade': 'R$'})
    estatisticas.append({'Categoria': 'TOTAIS', 'Métrica': 'Margem Percentual', 'Valor': f'{margem_pct:.2f}', 'Unidade': '%'})
    
    # 3. COMPARATIVO BASELINE vs OTIMIZADO
    if comparativo:
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Margem Baseline', 'Valor': f'R$ {comparativo["margem_baseline"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Margem Otimizada', 'Valor': f'R$ {comparativo["margem_otimizada"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Ganho Margem (R$)', 'Valor': f'R$ {comparativo["ganho_absoluto"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Ganho Margem (%)', 'Valor': f'{comparativo["ganho_percentual"]:.2f}', 'Unidade': '%'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Custo Baseline', 'Valor': f'R$ {comparativo["custo_baseline"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Custo Otimizado', 'Valor': f'R$ {comparativo["custo_otimizado"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Variação Custo (R$)', 'Valor': f'R$ {comparativo["reducao_custo"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Variação Custo (%)', 'Valor': f'{comparativo["reducao_custo_pct"]:.2f}', 'Unidade': '%'})

        # 3b. PEDIDOS RESERVADOS
        if comparativo.get('reserva_n_skus', 0) > 0:
            cat = 'PEDIDOS RESERVADOS'
            rv = comparativo
            taxa = (rv['reserva_volume_atendido'] / rv['reserva_volume_pedido'] * 100) if rv['reserva_volume_pedido'] > 0 else 0
            estatisticas.append({'Categoria': cat, 'Métrica': 'SKUs com Pedidos', 'Valor': f'{rv["reserva_n_skus"]}', 'Unidade': 'SKUs'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Volume Total Pedido', 'Valor': f'{rv["reserva_volume_pedido"]:,.0f}', 'Unidade': 'ovos'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Volume Atendido', 'Valor': f'{rv["reserva_volume_atendido"]:,.0f}', 'Unidade': 'ovos'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Deficit (Não Atendido)', 'Valor': f'{rv["reserva_deficit"]:,.0f}', 'Unidade': 'ovos'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Taxa de Atendimento', 'Valor': f'{taxa:.1f}', 'Unidade': '%'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Margem COM Priorização', 'Valor': f'R$ {rv["reserva_margem"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Margem SEM Priorização', 'Valor': f'R$ {rv["reserva_margem_sem_priorizacao"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Ganho da Priorização', 'Valor': f'R$ {rv["ganho_priorizacao"]:,.2f}', 'Unidade': 'R$'})

        # 3c. RESULTADO CONSOLIDADO
        if 'margem_total_modelo' in comparativo:
            cat = 'CONSOLIDADO'
            estatisticas.append({'Categoria': cat, 'Métrica': 'Margem Total do Modelo', 'Valor': f'R$ {comparativo["margem_total_modelo"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': cat, 'Métrica': '  Margem Otimização (solver)', 'Valor': f'R$ {comparativo["margem_otimizada"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': cat, 'Métrica': '  Margem Reservas (pedidos)', 'Valor': f'R$ {comparativo.get("reserva_margem", 0):,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': cat, 'Métrica': 'Custo Total do Modelo', 'Valor': f'R$ {comparativo["custo_total_modelo"]:,.2f}', 'Unidade': 'R$'})

    # 4. TOP CLASSES
    if len(resumo_classe) > 0:
        top_classes = resumo_classe.nlargest(5, 'margem_total')
        estatisticas.append({'Categoria': 'TOP CLASSES', 'Métrica': 'Nº de Classes Analisadas', 'Valor': f'{len(resumo_classe)}', 'Unidade': 'classes'})
        for _, row in top_classes.iterrows():
            # Coluna pode ser 'skus_alocados' ou 'num_skus' dependendo da versão
            num_skus = row.get('skus_alocados', row.get('num_skus', 0))
            estatisticas.append({
                'Categoria': 'TOP CLASSES',
                'Métrica': row['classe'],
                'Valor': f'Margem: R$ {row["margem_total"]:,.2f} | {int(num_skus)} SKUs | {row["quantidade_alocada"]:,.0f} ovos',
                'Unidade': ''
            })
    
    return pd.DataFrame(estatisticas)


def salvar_resultados(
    resultado, 
    resultado_etl,
    df_base: pd.DataFrame,
    comparativo: Dict[str, float],
    config: dict, 
    logger: logging.Logger
) -> None:
    """
    Salva resultados em CSV e Excel com múltiplas abas.
    
    Abas do Excel:
    - Detalhado: Resultado completo por item_id
    - Resumo por Classe: Agregação por classe
    - Estatísticas: Métricas e comparativo baseline vs otimizado
    - Pedidos Ignorados (se houver)
    
    Também salva demanda histórica em Excel (histórico completo).
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path('resultados')
    output_dir.mkdir(exist_ok=True)
    
    # Salvar demanda histórica em Excel (histórico completo)
    path_demanda = salvar_demanda_historica(resultado_etl, config, logger, timestamp)
    
    if len(resultado.resultado) > 0:
        resultado_export = resultado.resultado.copy()
        if 'margem_unitaria' in resultado_export.columns and 'margem_unitaria_cx360' not in resultado_export.columns:
            resultado_export['margem_unitaria_cx360'] = resultado_export['margem_unitaria']

        # CSV principal
        csv_path = output_dir / f'resultado_realocacao_completo_{timestamp}.csv'
        resultado_export.to_csv(csv_path, index=False)
        
        # CSV resumo por classe
        resumo_csv_path = output_dir / f'resumo_por_classe_{timestamp}.csv'
        if len(resultado.resumo_classe) > 0:
            resultado.resumo_classe.to_csv(resumo_csv_path, index=False)
        
        # Excel com múltiplas abas
        xlsx_path = output_dir / f'resultado_realocacao_completo_{timestamp}.xlsx'
        
        # Criar aba de estatísticas
        df_estatisticas = criar_aba_estatisticas(
            resultado.resultado,
            resultado.resumo_classe,
            comparativo,
            resultado_etl,
            config
        )
        
        # Verificar pedidos ignorados
        pedidos_ignorados = getattr(resultado_etl, 'pedidos_ignorados', [])
        
        num_abas = 3
        with pd.ExcelWriter(xlsx_path, engine='openpyxl') as writer:
            # Aba 1: Resultado Detalhado
            resultado_export.to_excel(writer, sheet_name='Detalhado', index=False)
            
            # Aba 2: Resumo por Classe
            if len(resultado.resumo_classe) > 0:
                resultado.resumo_classe.to_excel(writer, sheet_name='Resumo por Classe', index=False)
            
            # Aba 3: Estatísticas
            df_estatisticas.to_excel(writer, sheet_name='Estatísticas', index=False)
            
            # Aba 4: Pedidos Ignorados (se houver)
            if len(pedidos_ignorados) > 0:
                num_abas = 4
                df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
                if 'quantidade_pedida' in df_pedidos_ignorados.columns:
                    df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_pedida', ascending=False)
                df_pedidos_ignorados.to_excel(writer, sheet_name='Pedidos Ignorados', index=False)
        
        # Salvar pedidos ignorados separadamente (se houver)
        if len(pedidos_ignorados) > 0:
            pedidos_ign_csv = output_dir / f'pedidos_ignorados_{timestamp}.csv'
            df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
            df_pedidos_ignorados.to_csv(pedidos_ign_csv, index=False)
        
        # Log
        logger.info(f"\n[OK] Resultados salvos em {output_dir}/")
        if path_demanda is not None:
            logger.info(f"  Demanda histórica:")
            logger.info(f"    - {path_demanda.name}")
        logger.info(f"  CSV:")
        logger.info(f"    - {csv_path.name}")
        logger.info(f"    - {resumo_csv_path.name}")
        if len(pedidos_ignorados) > 0:
            logger.info(f"    - pedidos_ignorados_{timestamp}.csv")
        logger.info(f"  Excel:")
        abas = "Detalhado, Resumo por Classe, Estatísticas"
        if len(pedidos_ignorados) > 0:
            abas += ", Pedidos Ignorados"
        logger.info(f"    - {xlsx_path.name} (com {num_abas} abas: {abas})")
    else:
        if path_demanda is not None:
            logger.info(f"\n[OK] Demanda histórica salva: {path_demanda}")
        logger.warning("\n[AVISO] Nenhum resultado para salvar.")


def salvar_demanda_historica(resultado_etl, config: dict, logger: logging.Logger, timestamp: str):
    """
    Salva o cálculo da demanda histórica em Excel para histórico completo.
    
    Gera: resultados/demanda_historica_YYYYMMDD_HHMMSS.xlsx
    - Aba 'Demanda': item, descricao, classe, demanda_max (limite em ovos)
    - Aba 'Parametros': tipo_calculo, fator, periodo_meses, etc.
    
    Returns:
        Path do arquivo salvo ou None se não houver demanda.
    """
    df_demanda = getattr(
        getattr(resultado_etl, 'dados_brutos', None),
        'demanda_historica',
        None
    )
    if df_demanda is None or len(df_demanda) == 0:
        return None
    
    df_demanda = df_demanda.copy()
    
    # Incluir classe (base_skus_classes)
    path_classes = Path(config.get('paths', {}).get('classes', 'inputs/base_skus_classes.xlsx'))
    if path_classes.exists():
        try:
            df_classes = pd.read_excel(path_classes)
            col_mapping = {}
            for col in df_classes.columns:
                col_lower = col.lower()
                if col_lower == 'item' or 'sku' in col_lower or 'codigo' in col_lower:
                    col_mapping[col] = 'item'
                elif 'classe' in col_lower:
                    col_mapping[col] = 'classe'
            df_classes = df_classes.rename(columns=col_mapping)
            if 'item' in df_classes.columns and 'classe' in df_classes.columns:
                df_classes['item'] = pd.to_numeric(df_classes['item'], errors='coerce')
                df_classes = df_classes[df_classes['item'].notna()].drop_duplicates('item')
                df_classes['item'] = df_classes['item'].astype(int)
                df_demanda['item'] = df_demanda['item'].astype(int)
                df_demanda = df_demanda.merge(df_classes[['item', 'classe']], on='item', how='left')
        except Exception:
            df_demanda['classe'] = None
    else:
        df_demanda['classe'] = None
    
    # Incluir descrição (faturamento)
    path_fat = Path(config.get('paths', {}).get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
    if path_fat.exists():
        try:
            df_desc = pd.read_parquet(path_fat, columns=['item', 'Descrição do item'])
            df_desc = df_desc.drop_duplicates(subset=['item'])
            df_desc = df_desc.rename(columns={'Descrição do item': 'descricao'})
            df_desc['item'] = pd.to_numeric(df_desc['item'], errors='coerce')
            df_desc = df_desc[df_desc['item'].notna()]
            df_desc['item'] = df_desc['item'].astype(int)
            if 'item' not in df_demanda.columns or df_demanda['item'].dtype != 'int64':
                df_demanda['item'] = df_demanda['item'].astype(int)
            df_demanda = df_demanda.merge(df_desc[['item', 'descricao']], on='item', how='left')
        except Exception:
            df_demanda['descricao'] = None
    else:
        df_demanda['descricao'] = None
    
    # Ordem das colunas: item, descricao, classe, demanda_max
    col_order = ['item', 'descricao', 'classe', 'demanda_max']
    df_demanda = df_demanda[[c for c in col_order if c in df_demanda.columns]]
    
    output_dir = Path('resultados')
    output_dir.mkdir(exist_ok=True)
    path_xlsx = output_dir / f'demanda_historica_{timestamp}.xlsx'
    
    modelo_cfg = config.get('modelo', {})
    dados_cfg = config.get('dados', {})
    df_param = pd.DataFrame([
        {'parametro': 'considerar_demanda_historica', 'valor': modelo_cfg.get('considerar_demanda_historica', False)},
        {'parametro': 'tipo_calculo_demanda', 'valor': modelo_cfg.get('tipo_calculo_demanda', 'maximo')},
        {'parametro': 'fator_demanda_maxima', 'valor': modelo_cfg.get('fator_demanda_maxima', 1.2)},
        {'parametro': 'semana_ref', 'valor': dados_cfg.get('semana_ref', 'N/A')},
        {'parametro': 'data_ref', 'valor': dados_cfg.get('data_ref', 'N/A')},
        {'parametro': 'periodo_demanda_mes_ref', 'valor': dados_cfg.get('mes_custo', 11)},
        {'parametro': 'periodo_demanda_ano_ref', 'valor': dados_cfg.get('ano_custo', 2025)},
        {'parametro': 'periodo_demanda_janela_meses', 'valor': dados_cfg.get('meses_janela_custo', 6)},
        {'parametro': 'granularidade_demanda', 'valor': modelo_cfg.get('granularidade_demanda', 'S')},
        {'parametro': 'skus_com_limite', 'valor': len(df_demanda)},
        {'parametro': 'data_geracao', 'valor': timestamp},
    ])
    
    with pd.ExcelWriter(path_xlsx, engine='openpyxl') as writer:
        df_demanda.to_excel(writer, sheet_name='Demanda', index=False)
        df_param.to_excel(writer, sheet_name='Parametros', index=False)
    
    return path_xlsx
