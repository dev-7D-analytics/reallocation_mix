"""
Pacote de output do modelo de otimização de mix.

Exporta as funções de geração de resultados e comparativo baseline.
"""

from output.comparativo import calcular_comparativo_baseline
from output.resultados import criar_aba_estatisticas, salvar_resultados, salvar_demanda_historica
from output.distribuicao_historica_dow import gerar_distribuicao_historica_dow
from output.gerar_consolidado_pbi import gerar_consolidado_pbi

__all__ = [
    'calcular_comparativo_baseline',
    'criar_aba_estatisticas',
    'salvar_resultados',
    'salvar_demanda_historica',
    'gerar_distribuicao_historica_dow',
    'gerar_consolidado_pbi',
]
