#!/usr/bin/env python3
"""
Orquestrador do Modelo de Otimização de Mix.

Este script:
1. Carrega a configuração
2. Executa o ETL (carregamento e transformação de dados)
3. Executa a Otimização (criação e resolução do modelo)
4. Salva os resultados (com comparativo baseline vs otimizado)

A separação ETL/Otimização permite:
- Testar cada parte independentemente
- Reutilizar o ETL para diferentes modelos
- Manutenção mais fácil

Autor: Romulo Brito
Data: 2025-01-30
"""

import yaml
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional

from etl.pipeline import ETLPipeline
from modelo.otimizador import Otimizador


def carregar_config(config_path: str = 'config.yaml') -> dict:
    """Carrega configuração do arquivo YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def configurar_logging() -> logging.Logger:
    """Configura logging para o pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)


def calcular_comparativo_baseline(
    resultado: pd.DataFrame,
    df_base: pd.DataFrame,
    producao_por_classe: pd.Series,
    config: dict,
    logger: logging.Logger
) -> Dict[str, float]:
    """
    Calcula margem baseline vs otimizada.
    
    Baseline = distribuição uniforme entre SKUs da classe (sem otimização).
    Otimizado = distribuição otimizada pelo modelo.
    
    Returns:
        Dicionário com métricas comparativas
    """
    if resultado is None or len(resultado) == 0:
        return {}
    
    # Métricas otimizadas (do resultado)
    margem_otimizada = resultado['margem_total'].sum()
    custo_otimizado = resultado['custo_total'].sum() if 'custo_total' in resultado.columns else 0
    
    # Determinar se usa demanda histórica
    considerar_demanda = config.get('modelo', {}).get('considerar_demanda_historica', False)
    
    # Volume alocado por classe (da solução otimizada)
    qtd_alocada_por_classe = resultado.groupby('classe')['quantidade'].sum() if 'classe' in resultado.columns else None
    
    margem_baseline = 0.0
    custo_baseline = 0.0
    
    for classe in producao_por_classe.index:
        # Com demanda histórica: baseline = mesmo volume alocado (distribuição uniforme)
        # Sem demanda histórica: baseline = produção total da classe
        if qtd_alocada_por_classe is not None and classe in qtd_alocada_por_classe.index:
            qtd_producao = float(qtd_alocada_por_classe[classe])
        else:
            qtd_producao = float(producao_por_classe[classe])
        
        # Buscar item_ids desta classe
        item_ids_classe = df_base[df_base['classe'] == classe]
        
        if len(item_ids_classe) == 0:
            continue
        
        # Média das margens/custos como baseline (distribuição uniforme)
        margem_media = item_ids_classe['margem_unitaria'].mean()
        custo_medio = item_ids_classe['custo_ytd'].mean() if 'custo_ytd' in item_ids_classe.columns else 0
        
        # Converter ovos para caixas
        qtd_ovos_por_caixa_media = item_ids_classe['qtd_ovos_por_caixa'].mean()
        qtd_caixas = qtd_producao / qtd_ovos_por_caixa_media if qtd_ovos_por_caixa_media > 0 else 0
        
        margem_baseline += qtd_caixas * margem_media
        custo_baseline += qtd_caixas * custo_medio
    
    # Calcular ganhos
    ganho_margem = margem_otimizada - margem_baseline
    ganho_margem_pct = (ganho_margem / margem_baseline * 100) if margem_baseline > 0 else 0
    
    reducao_custo = custo_baseline - custo_otimizado
    reducao_custo_pct = (reducao_custo / custo_baseline * 100) if custo_baseline > 0 else 0
    
    # Log do comparativo
    logger.info("\n" + "="*80)
    logger.info("COMPARATIVO: BASELINE vs OTIMIZADO")
    logger.info("="*80)
    if considerar_demanda:
        logger.info("  (Baseline = mesmo volume alocado, distribuição uniforme por classe)")
    logger.info(f"  Margem Baseline (sem realocação): R$ {margem_baseline:,.2f}")
    logger.info(f"  Margem Otimizada (com realocação): R$ {margem_otimizada:,.2f}")
    logger.info(f"  GANHO MARGEM: R$ {ganho_margem:,.2f} ({ganho_margem_pct:.2f}%)")
    logger.info(f"  Custo Baseline (sem realocação): R$ {custo_baseline:,.2f}")
    logger.info(f"  Custo Otimizado (com realocação): R$ {custo_otimizado:,.2f}")
    logger.info(f"  Variação Custo: R$ {reducao_custo:,.2f} ({reducao_custo_pct:.2f}%)")
    
    return {
        'margem_baseline': margem_baseline,
        'margem_otimizada': margem_otimizada,
        'ganho_absoluto': ganho_margem,
        'ganho_percentual': ganho_margem_pct,
        'custo_baseline': custo_baseline,
        'custo_otimizado': custo_otimizado,
        'reducao_custo': reducao_custo,
        'reducao_custo_pct': reducao_custo_pct
    }


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
        # CSV principal
        csv_path = output_dir / f'resultado_realocacao_completo_{timestamp}.csv'
        resultado.resultado.to_csv(csv_path, index=False)
        
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
            resultado.resultado.to_excel(writer, sheet_name='Detalhado', index=False)
            
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


def main():
    """Função principal - orquestra ETL e Otimização."""
    
    # 1. Setup
    logger = configurar_logging()
    logger.info("="*80)
    logger.info("MODELO DE OTIMIZAÇÃO DE MIX - ARQUITETURA MODULAR")
    logger.info("="*80)
    
    config = carregar_config()
    
    # 2. ETL - Carregamento e transformação de dados
    logger.info("\n>>> FASE 1: ETL")
    etl = ETLPipeline(config, logger)
    resultado_etl = etl.executar()
    
    # O contrato ETL -> Otimização é o DataFrame base_otimizacao
    df_base = resultado_etl.base_otimizacao
    logger.info(f"\n  Contrato ETL -> Otimização:")
    logger.info(f"    Linhas: {len(df_base)}")
    logger.info(f"    Colunas: {list(df_base.columns)}")
    
    # 3. Otimização - Criação e resolução do modelo
    logger.info("\n>>> FASE 2: OTIMIZAÇÃO")
    otimizador = Otimizador(
        base_otimizacao=df_base,
        producao_por_classe=resultado_etl.producao_por_classe,
        producao_excedente_por_classe=resultado_etl.producao_excedente_por_classe,
        pedidos_garantidos_por_sku=resultado_etl.pedidos_garantidos_por_sku,
        config=config,
        logger=logger
    )
    
    resultado = otimizador.resolver()
    
    # 4. Comparativo Baseline vs Otimizado
    logger.info("\n>>> FASE 3: COMPARATIVO")
    comparativo = calcular_comparativo_baseline(
        resultado.resultado,
        df_base,
        resultado_etl.producao_por_classe,
        config,
        logger
    )
    
    # 5. Resultados
    logger.info("\n>>> FASE 4: RESULTADOS")
    logger.info("\n" + "="*80)
    logger.info("RESUMO FINAL")
    logger.info("="*80)
    logger.info(f"  Status: {resultado.status}")
    logger.info(f"  Quantidade total alocada: {resultado.quantidade_total:,.0f} unidades")
    logger.info(f"  Margem total: R$ {resultado.margem_total:,.2f}")
    
    if len(resultado.resumo_classe) > 0:
        logger.info("\n  Por classe:")
        for _, row in resultado.resumo_classe.iterrows():
            logger.info(f"    {row['classe']}: {row['quantidade_alocada']:,.0f} un, R$ {row['margem_total']:,.2f}")
    
    # 6. Salvar
    salvar_resultados(resultado, resultado_etl, df_base, comparativo, config, logger)
    
    logger.info("\n" + "="*80)
    logger.info("EXECUÇÃO CONCLUÍDA")
    logger.info("="*80)
    
    return resultado


if __name__ == '__main__':
    main()
