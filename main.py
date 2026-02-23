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
from output import calcular_comparativo_baseline, salvar_resultados
from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem


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

    # Acrescentar ao resultado as linhas de SKUs com volume reservado (pedido garantido) que não estão na base de otimização
    if resultado_etl.pedidos_garantidos_por_sku and len(resultado.resultado) > 0:
        itens_na_base = set(resultado.resultado['item'].astype(int))
        df_pedidos_garantidos = getattr(resultado_etl, 'pedidos_garantidos', None)
        tem_df_pg = (
            isinstance(df_pedidos_garantidos, pd.DataFrame)
            and len(df_pedidos_garantidos) > 0
            and 'item' in df_pedidos_garantidos.columns
            and 'classe' in df_pedidos_garantidos.columns
        )
        capar_reserva = config.get('modelo', {}).get('capar_reserva_na_producao', False)
        
        # Carregar descrições e preços/custos dos itens para enriquecer reservas
        desc_map = {}
        preco_map = {}
        custo_map = {}
        path_fat = Path(config.get('paths', {}).get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
        if path_fat.exists():
            try:
                df_enrich = pd.read_parquet(path_fat, columns=['item', 'Descrição do item', 'Quantidade', 'Receita Liquida'])
                df_enrich['item'] = pd.to_numeric(df_enrich['item'], errors='coerce')
                df_enrich = df_enrich[df_enrich['item'].notna()]
                # Descrições
                df_desc_reserva = df_enrich.drop_duplicates(subset=['item'])
                desc_map = dict(zip(df_desc_reserva['item'].astype(int), df_desc_reserva['Descrição do item']))
                # Preços (média ponderada)
                df_enrich['Quantidade'] = pd.to_numeric(df_enrich['Quantidade'], errors='coerce')
                df_enrich['Receita Liquida'] = pd.to_numeric(df_enrich['Receita Liquida'], errors='coerce')
                df_preco = df_enrich[(df_enrich['Quantidade'] > 0) & (df_enrich['Receita Liquida'] > 0)]
                preco_agg = df_preco.groupby('item').agg(rec=('Receita Liquida', 'sum'), qtd=('Quantidade', 'sum'))
                preco_agg['preco'] = preco_agg['rec'] / preco_agg['qtd']
                preco_map = preco_agg['preco'].to_dict()
                preco_map = {int(k): v for k, v in preco_map.items()}
            except Exception:
                pass
        # Custos (PRIC)
        path_custos = Path(config.get('paths', {}).get('custos', 'inputs/MANTI-PRIC_Custos_12012026.parquet'))
        if path_custos.exists():
            try:
                df_custo_res = pd.read_parquet(path_custos, columns=['item', 'Estab', 'Custo Médio', 'Quantidade'])
                df_custo_res['item'] = pd.to_numeric(df_custo_res['item'], errors='coerce')
                df_custo_res['Custo Médio'] = pd.to_numeric(df_custo_res['Custo Médio'], errors='coerce')
                df_custo_res['Quantidade'] = pd.to_numeric(df_custo_res['Quantidade'], errors='coerce')
                estab_custo = str(config.get('dados', {}).get('estab_custo', 100))
                df_custo_res = df_custo_res[
                    (df_custo_res['Estab'].astype(str) == estab_custo) &
                    (df_custo_res['Quantidade'] > 0) &
                    (df_custo_res['Custo Médio'].notna())
                ]
                custo_agg = df_custo_res.groupby('item').agg(ct=('Custo Médio', 'sum'), qt=('Quantidade', 'sum'))
                custo_agg['custo'] = custo_agg['ct'] / custo_agg['qt']
                custo_map = custo_agg['custo'].to_dict()
                custo_map = {int(k): v for k, v in custo_map.items()}
            except Exception:
                pass
        
        # Carregar campos extras de pedidos (data_entrega, preco_pedido) se disponíveis
        data_entrega_map = {}
        preco_pedido_map = {}
        pedidos_path = Path(config.get('paths', {}).get('pedidos', 'inputs/pedidos_clientes.csv'))
        if pedidos_path.exists():
            try:
                df_ped_extra = pd.read_csv(pedidos_path)
                df_ped_extra['item'] = pd.to_numeric(df_ped_extra['item'], errors='coerce').astype('Int64')
                if 'data_entrega_min' in df_ped_extra.columns:
                    data_entrega_map = dict(zip(df_ped_extra['item'], df_ped_extra['data_entrega_min']))
                if 'preco_pedido' in df_ped_extra.columns:
                    preco_pedido_map = dict(zip(df_ped_extra['item'], df_ped_extra['preco_pedido']))
            except Exception:
                pass
        
        # Montar lista de pedidos por SKU (com classe e margem)
        pedidos_info = []
        for item, qtd in resultado_etl.pedidos_garantidos_por_sku.items():
            if int(item) in itens_na_base or qtd <= 0:
                continue
            if tem_df_pg:
                row_pg = df_pedidos_garantidos[df_pedidos_garantidos['item'] == item]
                classe = row_pg['classe'].iloc[0] if len(row_pg) > 0 else 'OUTROS'
            else:
                classe = 'OUTROS'
            preco = preco_map.get(int(item), None)
            custo = custo_map.get(int(item), None)
            margem = (preco - custo) if (preco is not None and custo is not None) else None
            pedidos_info.append({
                'item': int(item),
                'classe': classe,
                'quantidade_pedida': float(qtd),
                'preco': preco,
                'custo_ytd': custo,
                'margem_unitaria': margem,
                'data_entrega': data_entrega_map.get(int(item), None),
                'preco_pedido': preco_pedido_map.get(int(item), None),
            })
        
        # Aplicar heurística de priorização por margem quando pedidos > produção
        if capar_reserva and pedidos_info:
            # Agrupar pedidos por classe
            pedidos_por_classe = {}
            for p in pedidos_info:
                pedidos_por_classe.setdefault(p['classe'], []).append(p)
            
            producao_classes = resultado_etl.producao_por_classe
            
            for classe, skus_classe in pedidos_por_classe.items():
                producao = float(producao_classes.get(classe, 0)) if classe in producao_classes.index else 0
                total_pedidos = sum(s['quantidade_pedida'] for s in skus_classe)
                
                if total_pedidos <= producao:
                    # Caso normal: pedidos cabem na produção → reserva total
                    # Ordenar por margem mesmo sem deficit, para manter rastreabilidade da ordem
                    skus_classe.sort(
                        key=lambda x: x['margem_unitaria'] if x['margem_unitaria'] is not None else -float('inf'),
                        reverse=True
                    )
                    restante = producao
                    for idx, s in enumerate(skus_classe, 1):
                        s['quantidade_reservada'] = s['quantidade_pedida']
                        s['deficit_pedido'] = 0.0
                        s['ordem_prioridade'] = idx
                        s['prod_disponivel_antes'] = restante
                        restante -= s['quantidade_pedida']
                        s['prod_disponivel_depois'] = restante
                else:
                    # Pedidos excedem produção → priorizar por margem
                    # Ordenar por margem (desc); SKUs sem margem ficam por último
                    skus_classe.sort(
                        key=lambda x: x['margem_unitaria'] if x['margem_unitaria'] is not None else -float('inf'),
                        reverse=True
                    )
                    restante = producao
                    for idx, s in enumerate(skus_classe, 1):
                        s['ordem_prioridade'] = idx
                        s['prod_disponivel_antes'] = restante
                        if restante >= s['quantidade_pedida']:
                            s['quantidade_reservada'] = s['quantidade_pedida']
                            s['deficit_pedido'] = 0.0
                            restante -= s['quantidade_pedida']
                        elif restante > 0:
                            s['quantidade_reservada'] = restante
                            s['deficit_pedido'] = s['quantidade_pedida'] - restante
                            restante = 0
                        else:
                            s['quantidade_reservada'] = 0.0
                            s['deficit_pedido'] = s['quantidade_pedida']
                        s['prod_disponivel_depois'] = restante
                    
                    deficit_classe = total_pedidos - producao
                    logger.info(f"  [DEFICIT] Classe {classe}: pedidos={total_pedidos:,.0f} > produção={producao:,.0f} (déficit={deficit_classe:,.0f})")
                    for s in skus_classe:
                        if s['deficit_pedido'] > 0:
                            logger.info(f"    SKU {s['item']}: pedido={s['quantidade_pedida']:,.0f}, reservado={s['quantidade_reservada']:,.0f}, déficit={s['deficit_pedido']:,.0f}")
        else:
            # Sem cap: comportamento original (reserva = pedido total)
            for p in pedidos_info:
                p['quantidade_reservada'] = p['quantidade_pedida']
                p['deficit_pedido'] = 0.0
                p['ordem_prioridade'] = None
                p['prod_disponivel_antes'] = None
                p['prod_disponivel_depois'] = None
        
        # Mapa item → qtd_ovos_por_caixa (extraído da descrição do faturamento)
        qtd_ovos_map = {}
        for item_int, desc in desc_map.items():
            if desc:
                emb = extrair_embalagem_descricao(str(desc))
                if emb:
                    qtd = calcular_qtd_embalagem(emb)
                    if qtd and qtd > 0:
                        qtd_ovos_map[item_int] = qtd
        
        # Gerar linhas de reserva com margens reais
        colunas_base = list(resultado.resultado.columns)
        linhas_reserva = []
        for p in pedidos_info:
            qtd_ovos = qtd_ovos_map.get(p['item'], 360)
            qty_reservada = p['quantidade_reservada']
            preco_unit = p['preco']
            custo_unit = p['custo_ytd']
            
            if preco_unit is not None and custo_unit is not None and qtd_ovos > 0:
                margem_ovo = (preco_unit - custo_unit) / qtd_ovos
                receita_total = (preco_unit / qtd_ovos) * qty_reservada
                custo_total = (custo_unit / qtd_ovos) * qty_reservada
                margem_total = receita_total - custo_total
                quantidade_caixas = qty_reservada / qtd_ovos
            else:
                margem_ovo = None
                receita_total = 0.0
                custo_total = 0.0
                margem_total = 0.0
                quantidade_caixas = 0.0
            
            linhas_reserva.append({
                'item_id': f"{p['item']}_RESERVA",
                'item': p['item'],
                'descricao': desc_map.get(p['item'], None),
                'embalagem': 'RESERVA',
                'classe': p['classe'],
                'quantidade': qty_reservada,
                'quantidade_pedida': p['quantidade_pedida'],
                'deficit_pedido': p['deficit_pedido'],
                'ordem_prioridade': p.get('ordem_prioridade'),
                'prod_disponivel_antes': p.get('prod_disponivel_antes'),
                'prod_disponivel_depois': p.get('prod_disponivel_depois'),
                'quantidade_caixas': quantidade_caixas,
                'preco': preco_unit,
                'custo_ytd': custo_unit,
                'margem_unitaria': p['margem_unitaria'],
                'margem_por_ovo': margem_ovo,
                'receita_total': receita_total,
                'custo_total': custo_total,
                'margem_total': margem_total,
                'limite_demanda_historica': None,
                'producao_disponivel': 0.0,
                'producao_total': 0.0,
                'tem_demanda_historica': False,
                'usa_custo_medio_classe': False,
                'tipo': 'reserva',
                'data_entrega': p.get('data_entrega'),
                'preco_pedido': p.get('preco_pedido'),
            })
        if linhas_reserva:
            df_reserva = pd.DataFrame(linhas_reserva)
            # Garantir que colunas do resultado base existam no df_reserva
            for c in colunas_base:
                if c not in df_reserva.columns:
                    df_reserva[c] = None
            # Preservar colunas extras que não existem no resultado base
            colunas_extras_reserva = ['quantidade_pedida', 'deficit_pedido', 'ordem_prioridade', 'prod_disponivel_antes', 'prod_disponivel_depois']
            colunas_finais = colunas_base + [c for c in colunas_extras_reserva if c in df_reserva.columns and c not in colunas_base]
            df_reserva = df_reserva[[c for c in colunas_finais if c in df_reserva.columns]]
            resultado.resultado = pd.concat([resultado.resultado, df_reserva], ignore_index=True)
            n_deficit = sum(1 for p in pedidos_info if p['deficit_pedido'] > 0)
            total_deficit = sum(p['deficit_pedido'] for p in pedidos_info)
            logger.info(f"  Incluídas {len(df_reserva)} linhas de volume reservado (SKUs com pedido fora da base de otimização)")
            if n_deficit > 0:
                logger.info(f"  [ALERTA] {n_deficit} SKUs com déficit de atendimento (total: {total_deficit:,.0f} ovos)")
    
    # Acrescentar linhas da classe OUTROS (não otimizada): aparecem no output com alocação 0
    base_outros = getattr(resultado_etl, 'base_outros', None)
    if base_outros is not None and len(base_outros) > 0 and len(resultado.resultado) > 0:
        colunas_base = list(resultado.resultado.columns)
        linhas_outros = []
        for _, row in base_outros.iterrows():
            qtd_ovos = row.get('qtd_ovos_por_caixa') or 0
            linhas_outros.append({
                'item_id': row['item_id'],
                'item': int(row['item']),
                'descricao': row.get('descricao', None),
                'embalagem': row['embalagem'],
                'classe': 'OUTROS',
                'quantidade': 0.0,
                'quantidade_caixas': 0.0,
                'preco': row.get('preco'),
                'custo_ytd': row.get('custo_ytd'),
                'margem_unitaria': row.get('margem_unitaria'),
                'margem_por_ovo': (row['margem_unitaria'] / qtd_ovos) if qtd_ovos and row.get('margem_unitaria') is not None else None,
                'receita_total': 0.0,
                'custo_total': 0.0,
                'margem_total': 0.0,
                'limite_demanda_historica': row.get('limite_demanda_historica'),
                'producao_disponivel': row.get('producao_disponivel_otimizacao_classe', 0.0),
                'producao_total': row.get('producao_total', 0.0),
                'tem_demanda_historica': row.get('tem_demanda_historica', False),
                'usa_custo_medio_classe': row.get('usa_custo_medio_classe', False),
                'tipo': 'outros',
            })
        df_outros = pd.DataFrame(linhas_outros)
        for c in colunas_base:
            if c not in df_outros.columns:
                df_outros[c] = None
        df_outros = df_outros[colunas_base]
        resultado.resultado = pd.concat([resultado.resultado, df_outros], ignore_index=True)
        logger.info(f"  Incluídas {len(df_outros)} linhas da classe OUTROS (alocação = 0, não otimizada)")
    # Incluir OUTROS no resumo por classe com alocação 0 (sempre, para a classe aparecer mesmo sem itens na base)
    if len(resultado.resumo_classe.columns) > 0 and 'classe' in resultado.resumo_classe.columns:
        if 'OUTROS' not in resultado.resumo_classe['classe'].values:
            skus_outros = base_outros['item'].nunique() if base_outros is not None and len(base_outros) > 0 else 0
            linha_outros = pd.DataFrame([{
                'classe': 'OUTROS',
                'quantidade_alocada': 0.0,
                'margem_total': 0.0,
                'skus_alocados': skus_outros,
            }])
            for c in resultado.resumo_classe.columns:
                if c not in linha_outros.columns:
                    linha_outros[c] = 0.0
            linha_outros = linha_outros[resultado.resumo_classe.columns]
            resultado.resumo_classe = pd.concat([resultado.resumo_classe, linha_outros], ignore_index=True)
    
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
