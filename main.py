#!/usr/bin/env python3
"""
Orquestrador do Modelo de Otimização de Mix.

Este script:
1. Carrega a configuração
2. Executa o ETL (carregamento e transformação de dados)
3. Executa a Otimização (criação e resolução do modelo)
4. Salva os resultados

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


def salvar_resultados(resultado, config: dict, logger: logging.Logger) -> None:
    """Salva resultados em CSV e Excel."""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path('resultados')
    output_dir.mkdir(exist_ok=True)
    
    if len(resultado.resultado) > 0:
        # CSV
        csv_path = output_dir / f'resultado_otimizacao_{timestamp}.csv'
        resultado.resultado.to_csv(csv_path, index=False)
        logger.info(f"\n[OK] Resultados salvos:")
        logger.info(f"  CSV: {csv_path}")
        
        # Excel
        xlsx_path = output_dir / f'resultado_otimizacao_{timestamp}.xlsx'
        with pd.ExcelWriter(xlsx_path, engine='openpyxl') as writer:
            resultado.resultado.to_excel(writer, sheet_name='Detalhado', index=False)
            if len(resultado.resumo_classe) > 0:
                resultado.resumo_classe.to_excel(writer, sheet_name='Resumo', index=False)
        logger.info(f"  Excel: {xlsx_path}")
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
    
    # 4. Resultados
    logger.info("\n>>> FASE 3: RESULTADOS")
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
    
    # 5. Salvar
    salvar_resultados(resultado, config, logger)
    
    logger.info("\n" + "="*80)
    logger.info("EXECUÇÃO CONCLUÍDA")
    logger.info("="*80)
    
    return resultado


if __name__ == '__main__':
    main()
