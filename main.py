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
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Ganho Absoluto', 'Valor': f'R$ {comparativo["ganho_absoluto"]:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'COMPARATIVO', 'Métrica': 'Ganho Percentual', 'Valor': f'{comparativo["ganho_percentual"]:.2f}', 'Unidade': '%'})
    
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
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path('resultados')
    output_dir.mkdir(exist_ok=True)
    
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
        logger.warning("\n[AVISO] Nenhum resultado para salvar.")


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
