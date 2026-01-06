"""Analisa o potencial de ganho usando o modelo de realocacao canonico."""

import logging
from pathlib import Path
from typing import Tuple

import pandas as pd
import yaml

from modelo_otimizacao_com_realocacao import ModeloOtimizacaoComRealocacao


logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def _resumo_resultado(resultado: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Constroi resumos por classe e por item_id a partir do resultado."""
    if resultado is None or len(resultado) == 0:
        return pd.DataFrame(), pd.DataFrame()

    colunas_agregacao = {
        'item': 'nunique',
        'quantidade': 'sum',
        'margem_total': 'sum',
        'receita_total': 'sum',
        'custo_total': 'sum',
    }

    resumo_classe = resultado.groupby('classe').agg(colunas_agregacao).reset_index()
    resumo_classe = resumo_classe.rename(columns={
        'item': 'num_skus',
        'quantidade': 'quantidade_alocada',
        'margem_total': 'margem_total_classe',
        'receita_total': 'receita_total_classe',
        'custo_total': 'custo_total_classe',
    })

    top_item_id = resultado[['item_id', 'item', 'embalagem', 'classe', 'quantidade', 'margem_total']]
    top_item_id = top_item_id.sort_values('margem_total', ascending=False)

    return resumo_classe, top_item_id


def main():
    logger.info("=" * 80)
    logger.info("ANALISE DE POTENCIAL DE GANHO - MODELO COM REALOCACAO")
    logger.info("=" * 80)

    config_path = Path('config.yaml')
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de configuracao nao encontrado: {config_path}")

    # Executa o modelo canonico
    modelo = ModeloOtimizacaoComRealocacao(config_path=str(config_path))
    modelo.carregar_dados()
    modelo.criar_modelo()

    if not modelo.resolver():
        logger.error("Modelo nao encontrou solucao. Abortando analise.")
        return

    comparativo = modelo.calcular_comparativo()
    resultado = modelo.resultado

    resumo_classe, top_item_id = _resumo_resultado(resultado)

    # Logs rapidos
    logger.info("\nRESULTADO RESUMIDO")
    logger.info(f"  Combinacoes escolhidas: {len(resultado) if resultado is not None else 0}")
    if comparativo:
        logger.info(f"  Margem baseline: R$ {comparativo['margem_baseline']:,.2f}")
        logger.info(f"  Margem otimizada: R$ {comparativo['margem_otimizada']:,.2f}")
        logger.info(f"  Ganho absoluto: R$ {comparativo['ganho_absoluto']:,.2f}")
        logger.info(f"  Ganho percentual: {comparativo['ganho_percentual']:.2f}%")

    if len(resumo_classe) > 0:
        logger.info("\nTOP 5 CLASSES POR MARGEM")
        for _, row in resumo_classe.sort_values('margem_total_classe', ascending=False).head(5).iterrows():
            logger.info(
                f"  {row['classe']}: margem R$ {row['margem_total_classe']:,.2f} | "
                f"quantidade {row['quantidade_alocada']:,.0f} | SKUs {int(row['num_skus'])}"
            )

    if len(top_item_id) > 0:
        logger.info("\nTOP 5 ITEM_ID POR MARGEM TOTAL")
        for _, row in top_item_id.head(5).iterrows():
            logger.info(
                f"  {row['item_id']} ({row['classe']}): {row['quantidade']:,.0f} un | "
                f"margem R$ {row['margem_total']:,.2f}"
            )

    # Persistir analise
    output_dir = Path('resultados')
    output_dir.mkdir(exist_ok=True)

    resumo_classe_path = output_dir / 'analise_potencial_classe.csv'
    top_item_path = output_dir / 'analise_potencial_item_id.csv'

    if len(resumo_classe) > 0:
        resumo_classe.to_csv(resumo_classe_path, index=False, encoding='utf-8')
    if len(top_item_id) > 0:
        top_item_id.to_csv(top_item_path, index=False, encoding='utf-8')

    logger.info("\n[OK] Analise concluida")
    logger.info(f"  Resumo por classe: {resumo_classe_path}")
    logger.info(f"  Top item_id: {top_item_path}")


if __name__ == '__main__':
    main()
