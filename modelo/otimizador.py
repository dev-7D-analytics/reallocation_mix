"""
Modelo de Otimização de Mix com Realocação.

Este módulo é responsável por:
1. Receber o DataFrame base_otimizacao do ETL
2. Criar o modelo de otimização (OR-Tools)
3. Definir variáveis, restrições e função objetivo
4. Resolver e extrair resultados

O modelo NÃO carrega dados - ele recebe tudo pronto do ETL.

Autor: Romulo Brito
Data: 2025-01-30
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, List
from dataclasses import dataclass
import logging
from ortools.linear_solver import pywraplp


@dataclass
class ResultadoOtimizacao:
    """Resultado da otimização."""
    resultado: pd.DataFrame
    resumo_classe: pd.DataFrame
    margem_total: float
    quantidade_total: float
    status: str  # 'OPTIMAL', 'INFEASIBLE', etc.


class Otimizador:
    """
    Modelo de otimização que permite realocação de volume entre SKUs da mesma classe.
    
    O modelo recebe o DataFrame `base_otimizacao` pronto do ETL e apenas
    configura e resolve o problema de otimização.
    
    Uso:
        resultado_etl = etl.executar()
        otimizador = Otimizador(resultado_etl, config)
        resultado = otimizador.resolver()
    """
    
    def __init__(
        self, 
        base_otimizacao: pd.DataFrame,
        producao_por_classe: pd.Series,
        producao_excedente_por_classe: Dict[str, float],
        pedidos_garantidos_por_sku: Dict[int, float],
        config: Dict,
        logger: Optional[logging.Logger] = None
    ):
        """
        Inicializa o otimizador.
        
        Args:
            base_otimizacao: DataFrame com dados preparados pelo ETL
            producao_por_classe: Série com produção total por classe
            producao_excedente_por_classe: Dicionário com produção excedente por classe
            pedidos_garantidos_por_sku: Dicionário com pedidos garantidos por SKU
            config: Dicionário de configuração
            logger: Logger opcional
        """
        self.df_base = base_otimizacao.copy()
        self.producao_por_classe = producao_por_classe
        self.producao_excedente = producao_excedente_por_classe
        self.pedidos_garantidos = pedidos_garantidos_por_sku
        self.config = config
        self.logger = logger or self._criar_logger()
        
        # Estado do solver
        self.solver: Optional[pywraplp.Solver] = None
        self.variaveis: Dict[str, pywraplp.Variable] = {}
        self.status: Optional[int] = None
    
    def _criar_logger(self) -> logging.Logger:
        """Cria logger padrão."""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    def criar_modelo(self) -> None:
        """Cria o modelo de otimização (variáveis, restrições, objetivo)."""
        self.logger.info("\n" + "="*80)
        self.logger.info("ETAPA 2: CRIAÇÃO DO MODELO DE OTIMIZAÇÃO")
        self.logger.info("="*80)
        
        # Criar solver
        solver_cfg = self.config.get('solver', {})
        solver_type = solver_cfg.get('solver_type', 'GLOP_LINEAR_PROGRAMMING')
        self.solver = pywraplp.Solver(
            'MixOtimizacao',
            getattr(pywraplp.Solver, solver_type)
        )
        
        # Configurar parâmetros do solver
        time_limit = solver_cfg.get('time_limit_ms', 600000)
        self.solver.SetTimeLimit(time_limit)
        self.logger.info(f"  Solver: {solver_type}")
        self.logger.info(f"  Time limit: {time_limit}ms")
        
        num_threads = solver_cfg.get('num_threads', 4)
        self.solver.SetNumThreads(num_threads)
        self.logger.info(f"  Threads: {num_threads}")
        
        if solver_cfg.get('verbose', False):
            self.solver.EnableOutput()
            self.logger.info(f"  Verbose: ativado")
        
        # Parâmetros específicos do solver (SCIP)
        scip_params = []
        gap = solver_cfg.get('gap_percentage', None)
        if gap is not None:
            scip_params.append(f"limits/gap = {gap}")
        dual_tol = solver_cfg.get('dual_tolerance', None)
        if dual_tol is not None:
            scip_params.append(f"numerics/dualfeastol = {dual_tol}")
        primal_tol = solver_cfg.get('primal_tolerance', None)
        if primal_tol is not None:
            scip_params.append(f"numerics/feastol = {primal_tol}")
        
        if scip_params and 'SCIP' in solver_type:
            params_str = "\n".join(scip_params)
            self.solver.SetSolverSpecificParametersAsString(params_str)
            self.logger.info(f"  Parâmetros SCIP: {scip_params}")
        
        # Criar variáveis
        self._criar_variaveis()
        
        # Adicionar restrições
        self._adicionar_restricoes()
        
        # Definir objetivo
        self._definir_objetivo()
        
        self.logger.info("\n[OK] Modelo criado com sucesso!")
    
    def _criar_variaveis(self) -> None:
        """Cria variáveis de decisão."""
        self.logger.info("\n[1/3] Criando variáveis de decisão...")
        
        self.variaveis = {}
        variaveis_continuas = self.config.get('modelo', {}).get('variaveis_continuas', True)
        tipo_var = "contínuas (NumVar)" if variaveis_continuas else "inteiras (IntVar)"
        self.logger.info(f"  Tipo de variáveis: {tipo_var}")
        
        usar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        
        for idx, row in self.df_base.iterrows():
            item_id = row['item_id']
            var_name = f"x_{item_id}"
            
            # Limite superior: min(excedente da classe, demanda histórica do item)
            upper_bound = row['producao_disponivel_otimizacao_classe']
            
            if usar_demanda:
                limite = row.get('limite_demanda_historica')
                if pd.notna(limite) and limite > 0:
                    upper_bound = min(upper_bound, limite)
            
            if upper_bound > 0:
                if variaveis_continuas:
                    self.variaveis[item_id] = self.solver.NumVar(
                        0,
                        upper_bound,
                        var_name
                    )
                else:
                    self.variaveis[item_id] = self.solver.IntVar(
                        0,
                        int(upper_bound),
                        var_name
                    )
        
        self.logger.info(f"  Variáveis criadas: {len(self.variaveis)}")
    
    def _adicionar_restricoes(self) -> None:
        """Adiciona restrições ao modelo."""
        self.logger.info("\n[2/3] Adicionando restrições...")
        
        num_restricoes = 0
        
        # 1. Restrição de produção por classe
        for classe in self.df_base['classe'].unique():
            item_ids_classe = self.df_base[self.df_base['classe'] == classe]['item_id'].unique()
            
            # Soma das alocações <= produção disponível da classe
            producao_classe = self.producao_excedente.get(classe, 0)
            
            if producao_classe > 0:
                tem_variaveis = any(iid in self.variaveis for iid in item_ids_classe)
                
                if tem_variaveis:
                    constraint = self.solver.Constraint(0, producao_classe, f"classe_{classe}")
                    
                    for item_id in item_ids_classe:
                        if item_id in self.variaveis:
                            constraint.SetCoefficient(self.variaveis[item_id], 1)
                    
                    num_restricoes += 1
        
        self.logger.info(f"  Restrições de classe: {num_restricoes}")
        
        # 2. Restrições de demanda histórica
        usar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        
        if usar_demanda:
            restricoes_demanda = 0
            
            for item in self.df_base['item'].unique():
                item_rows = self.df_base[self.df_base['item'] == item]
                
                if item_rows['tem_demanda_historica'].any():
                    limite = item_rows['limite_demanda_historica'].iloc[0]
                    
                    if pd.notna(limite) and limite > 0:
                        # Soma de todas as embalagens do SKU <= limite
                        item_ids = item_rows['item_id'].unique()
                        vars_item = [self.variaveis[iid] for iid in item_ids if iid in self.variaveis]
                        
                        if vars_item:
                            constraint = self.solver.Constraint(0, limite, f"demanda_{item}")
                            for iid in item_ids:
                                if iid in self.variaveis:
                                    constraint.SetCoefficient(self.variaveis[iid], 1)
                            restricoes_demanda += 1
            
            self.logger.info(f"  Restrições de demanda histórica: {restricoes_demanda}")
            num_restricoes += restricoes_demanda
        
        self.logger.info(f"  Total de restrições: {num_restricoes}")
    
    def _definir_objetivo(self) -> None:
        """Define função objetivo (maximizar margem)."""
        self.logger.info("\n[3/3] Definindo função objetivo...")
        
        objetivo = self.solver.Objective()
        
        tipo_objetivo = self.config.get('modelo', {}).get('tipo_objetivo', 'maximizar_margem')
        
        for idx, row in self.df_base.iterrows():
            item_id = row['item_id']
            
            if item_id in self.variaveis:
                # Margem por ovo = margem_unitaria / qtd_ovos_por_caixa
                margem_por_ovo = row['margem_unitaria'] / row['qtd_ovos_por_caixa']
                objetivo.SetCoefficient(self.variaveis[item_id], margem_por_ovo)
        
        if tipo_objetivo == 'maximizar_margem':
            objetivo.SetMaximization()
            self.logger.info("  Objetivo: MAXIMIZAR MARGEM")
        else:
            objetivo.SetMinimization()
            self.logger.info("  Objetivo: MINIMIZAR CUSTO")
    
    def resolver(self) -> ResultadoOtimizacao:
        """
        Resolve o modelo de otimização.
        
        Returns:
            ResultadoOtimizacao com DataFrame de resultados e métricas
        """
        if self.solver is None:
            self.criar_modelo()
        
        self.logger.info("\n" + "="*80)
        self.logger.info("ETAPA 3: RESOLUÇÃO DO MODELO")
        self.logger.info("="*80)
        
        # Resolver
        self.status = self.solver.Solve()
        
        if self.status == pywraplp.Solver.OPTIMAL:
            self.logger.info("\n[OK] Solução ótima encontrada!")
            resultado = self._extrair_resultado()
            return resultado
        elif self.status == pywraplp.Solver.INFEASIBLE:
            self.logger.error("\n[ERRO] Problema infactível!")
            return ResultadoOtimizacao(
                resultado=pd.DataFrame(),
                resumo_classe=pd.DataFrame(),
                margem_total=0,
                quantidade_total=0,
                status='INFEASIBLE'
            )
        else:
            self.logger.warning(f"\n[AVISO] Status do solver: {self.status}")
            return ResultadoOtimizacao(
                resultado=pd.DataFrame(),
                resumo_classe=pd.DataFrame(),
                margem_total=0,
                quantidade_total=0,
                status=f'STATUS_{self.status}'
            )
    
    def _extrair_resultado(self) -> ResultadoOtimizacao:
        """Extrai resultados da solução.
        
        Inclui TODOS os item_ids que entraram na otimização (df_base), não só os alocados.
        Para os não alocados: quantidade=0, totais=0; preço/custo/margem unitária vêm da base.
        Assim o output serve de referência para o comparador (preço/custo/margem por item_id).
        """
        self.logger.info("\nExtraindo resultados...")
        
        resultados = []
        for idx, row in self.df_base.iterrows():
            item_id = row['item_id']
            qtd_ovos = self.variaveis[item_id].solution_value() if item_id in self.variaveis else 0.0
            qtd_ovos = float(qtd_ovos) if qtd_ovos is not None else 0.0
            
            if qtd_ovos > 0.01:
                qtd_caixas = qtd_ovos / row['qtd_ovos_por_caixa']
                margem_total = qtd_caixas * row['margem_unitaria']
                receita_total = qtd_caixas * row['preco']
                custo_total = qtd_caixas * row['custo_ytd']
            else:
                qtd_caixas = 0.0
                margem_total = 0.0
                receita_total = 0.0
                custo_total = 0.0
            
            resultados.append({
                'item_id': item_id,
                'item': row['item'],
                'descricao': row.get('descricao', None),
                'embalagem': row['embalagem'],
                'classe': row['classe'],
                'quantidade': qtd_ovos,
                'quantidade_caixas': qtd_caixas,
                'preco': row['preco'],
                'custo_ytd': row['custo_ytd'],
                'margem_unitaria': row['margem_unitaria'],
                'margem_por_ovo': row['margem_unitaria'] / row['qtd_ovos_por_caixa'] if row['qtd_ovos_por_caixa'] and row['qtd_ovos_por_caixa'] > 0 else np.nan,
                'receita_total': receita_total,
                'custo_total': custo_total,
                'margem_total': margem_total,
                'limite_demanda_historica': row.get('limite_demanda_historica', np.nan),
                'producao_disponivel': row['producao_disponivel_otimizacao_classe'],
                'producao_total': row['producao_total'],
                'tem_demanda_historica': row.get('tem_demanda_historica', False),
                'usa_custo_medio_classe': row.get('usa_custo_medio_classe', False),
                # Rastreabilidade de insumos (sem alterar semântica do modelo).
                'origem_preco': row.get('origem_preco', None),
                'origem_custo': row.get('origem_custo', None),
                'origem_embalagem': row.get('origem_embalagem', None),
                'tipo': 'otimizacao',
            })
        
        df_resultado = pd.DataFrame(resultados)
        
        # Totais apenas dos alocados
        alocados = df_resultado[df_resultado['quantidade'] > 0.01]
        if len(alocados) > 0:
            margem_total = alocados['margem_total'].sum()
            quantidade_total = alocados['quantidade'].sum()
            self.logger.info(f"\n  Combinações alocadas: {len(alocados)} de {len(df_resultado)} na base")
            self.logger.info(f"  Quantidade total: {quantidade_total:,.0f} unidades")
            self.logger.info(f"  Margem total: R$ {margem_total:,.2f}")
        else:
            margem_total = 0.0
            quantidade_total = 0.0
        
        # Resumo por classe (apenas alocados)
        if len(alocados) > 0:
            resumo = alocados.groupby('classe').agg({
                'quantidade': 'sum',
                'margem_total': 'sum',
                'item': 'nunique'
            }).reset_index()
            resumo.columns = ['classe', 'quantidade_alocada', 'margem_total', 'skus_alocados']
        else:
            resumo = pd.DataFrame()
        
        return ResultadoOtimizacao(
            resultado=df_resultado,
            resumo_classe=resumo,
            margem_total=margem_total,
            quantidade_total=quantidade_total,
            status='OPTIMAL'
        )
