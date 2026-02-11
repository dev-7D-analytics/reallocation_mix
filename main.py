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
    
    # Volume alocado por classe: apenas volume otimizável (excluir reserva e OUTROS) para comparação justa
    # com a margem otimizada, que também não inclui margem de reserva nem de OUTROS
    if 'classe' in resultado.columns:
        if 'tipo' in resultado.columns:
            mask_otimizavel = (resultado['tipo'] != 'reserva') & (resultado['tipo'] != 'outros')
            df_otimizavel = resultado.loc[mask_otimizavel]
            qtd_alocada_por_classe = df_otimizavel.groupby('classe')['quantidade'].sum() if len(df_otimizavel) > 0 else None
        else:
            qtd_alocada_por_classe = resultado.groupby('classe')['quantidade'].sum()
    else:
        qtd_alocada_por_classe = None
    
    margem_baseline = 0.0
    custo_baseline = 0.0
    auditoria_classes = []  # Dados de auditoria por classe/SKU
    
    # Calcular volume reservado (pedidos) por classe e por SKU a partir do resultado
    reserva_por_classe = {}
    reserva_por_sku = {}
    if 'tipo' in resultado.columns and 'classe' in resultado.columns:
        df_reserva = resultado[resultado['tipo'] == 'reserva']
        if len(df_reserva) > 0:
            reserva_por_classe = df_reserva.groupby('classe')['quantidade'].sum().to_dict()
            reserva_por_sku = df_reserva.groupby('item_id')['quantidade'].sum().to_dict()
    
    for classe in producao_por_classe.index:
        # Com demanda histórica: baseline = mesmo volume alocado (distribuição uniforme)
        # Sem demanda histórica: baseline = produção total da classe
        if qtd_alocada_por_classe is not None and classe in qtd_alocada_por_classe.index:
            qtd_producao = float(qtd_alocada_por_classe[classe])
        else:
            qtd_producao = float(producao_por_classe[classe])
        
        # Buscar item_ids desta classe
        item_ids_classe = df_base[df_base['classe'] == classe].copy()
        
        if len(item_ids_classe) == 0:
            continue
        
        # Baseline usando PROPORÇÃO HISTÓRICA REAL (sem fator)
        # Fórmula: Σ (Volume_classe × proporção_sku × margem_por_ovo_sku)
        # Isso representa a margem se continuássemos vendendo na mesma proporção histórica
        
        # Calcular margem e custo por ovo para cada SKU
        item_ids_classe['margem_por_ovo'] = item_ids_classe['margem_unitaria'] / item_ids_classe['qtd_ovos_por_caixa']
        item_ids_classe['custo_por_ovo'] = item_ids_classe['custo_ytd'] / item_ids_classe['qtd_ovos_por_caixa'] if 'custo_ytd' in item_ids_classe.columns else 0
        
        # Calcular proporção histórica de cada SKU (baseada no volume total vendido, sem fator)
        if 'volume_historico_total' in item_ids_classe.columns:
            vol_hist = item_ids_classe['volume_historico_total'].fillna(0)
            total_vol_hist = vol_hist.sum()
            if total_vol_hist > 0:
                item_ids_classe['proporcao_historica'] = vol_hist / total_vol_hist
            else:
                item_ids_classe['proporcao_historica'] = 1.0 / len(item_ids_classe)
        else:
            item_ids_classe['proporcao_historica'] = 1.0 / len(item_ids_classe)
        
        # Calcular volume baseline por SKU (proporcional ao histórico)
        item_ids_classe['volume_baseline'] = qtd_producao * item_ids_classe['proporcao_historica']
        
        # Guardar volume antes do cap para auditoria
        item_ids_classe['volume_baseline_antes_cap'] = item_ids_classe['volume_baseline'].copy()
        
        # Cap do volume_baseline ao limite_demanda_historica (mesma restrição do otimizador)
        # O otimizador aplica: Σ embalagens_do_item ≤ limite_demanda_historica
        # O baseline deve respeitar a mesma restrição para comparação justa
        if 'limite_demanda_historica' in item_ids_classe.columns:
            volume_excedente_total = 0.0
            itens_capados = set()
            
            # 1) Identificar itens que excedem o limite e capar
            for item_val in item_ids_classe['item'].unique():
                mask_item = item_ids_classe['item'] == item_val
                rows_item = item_ids_classe.loc[mask_item]
                limite = rows_item['limite_demanda_historica'].iloc[0]
                
                if pd.notna(limite) and limite > 0:
                    vol_total_item = rows_item['volume_baseline'].sum()
                    if vol_total_item > limite:
                        # Escalar proporcionalmente para respeitar o limite
                        fator_reducao = limite / vol_total_item
                        excedente = vol_total_item - limite
                        volume_excedente_total += excedente
                        item_ids_classe.loc[mask_item, 'volume_baseline'] *= fator_reducao
                        itens_capados.add(item_val)
            
            # 2) Redistribuir excedente iterativamente entre itens com folga
            max_iteracoes = 10
            for _ in range(max_iteracoes):
                if volume_excedente_total < 0.01:
                    break
                
                # Calcular capacidade restante por item (não capados ou com folga)
                capacidade_restante = {}
                for item_val in item_ids_classe['item'].unique():
                    mask_item = item_ids_classe['item'] == item_val
                    rows_item = item_ids_classe.loc[mask_item]
                    limite = rows_item['limite_demanda_historica'].iloc[0]
                    
                    if pd.notna(limite) and limite > 0:
                        vol_atual = rows_item['volume_baseline'].sum()
                        folga = limite - vol_atual
                        if folga > 0.01:
                            capacidade_restante[item_val] = folga
                    # Itens sem limite de demanda podem absorver qualquer volume
                    elif pd.isna(limite) or limite == 0:
                        # Sem limite = capacidade "infinita", usa proporcao para distribuir
                        prop = rows_item['proporcao_historica'].sum()
                        if prop > 0:
                            capacidade_restante[item_val] = volume_excedente_total * prop
                
                if not capacidade_restante:
                    break  # Nenhum item pode absorver mais
                
                cap_total = sum(capacidade_restante.values())
                volume_a_distribuir = min(volume_excedente_total, cap_total)
                
                novo_excedente = 0.0
                for item_val, folga in capacidade_restante.items():
                    proporcao_folga = folga / cap_total
                    adicional = volume_a_distribuir * proporcao_folga
                    mask_item = item_ids_classe['item'] == item_val
                    rows_item = item_ids_classe.loc[mask_item]
                    vol_item = rows_item['volume_baseline'].sum()
                    
                    if vol_item > 0:
                        fator_aumento = (vol_item + adicional) / vol_item
                        item_ids_classe.loc[mask_item, 'volume_baseline'] *= fator_aumento
                    else:
                        # Distribuir proporcional às embalagens do item
                        n_embs = mask_item.sum()
                        if n_embs > 0:
                            item_ids_classe.loc[mask_item, 'volume_baseline'] += adicional / n_embs
                    
                    # Verificar se redistribuição causou novo excedente
                    limite_item = rows_item['limite_demanda_historica'].iloc[0]
                    if pd.notna(limite_item) and limite_item > 0:
                        vol_novo = item_ids_classe.loc[mask_item, 'volume_baseline'].sum()
                        if vol_novo > limite_item:
                            novo_exc = vol_novo - limite_item
                            fator_correcao = limite_item / vol_novo
                            item_ids_classe.loc[mask_item, 'volume_baseline'] *= fator_correcao
                            novo_excedente += novo_exc
                
                volume_excedente_total = novo_excedente + max(0, volume_excedente_total - volume_a_distribuir)
            
            if volume_excedente_total > 0.01:
                logger.warning(f"  Classe {classe}: {volume_excedente_total:.0f} ovos de excedente "
                             f"baseline não redistribuídos (todos os itens no limite)")
        
        # Calcular margem e custo baseline por SKU
        item_ids_classe['margem_baseline_sku'] = item_ids_classe['volume_baseline'] * item_ids_classe['margem_por_ovo']
        item_ids_classe['custo_baseline_sku'] = item_ids_classe['volume_baseline'] * item_ids_classe['custo_por_ovo']
        
        # Somar para o total da classe
        margem_baseline += item_ids_classe['margem_baseline_sku'].sum()
        custo_baseline += item_ids_classe['custo_baseline_sku'].sum()
        
        # Coletar dados de auditoria por SKU
        # Dados otimizados: buscar volume alocado por SKU no resultado
        for _, row_base in item_ids_classe.iterrows():
            item_id = row_base['item_id']
            
            # Buscar alocação otimizada para este item_id
            mask_otim = (resultado['item_id'] == item_id)
            if 'tipo' in resultado.columns:
                mask_otim = mask_otim & (resultado['tipo'] != 'reserva') & (resultado['tipo'] != 'outros')
            row_otim = resultado.loc[mask_otim]
            vol_otim = float(row_otim['quantidade'].sum()) if len(row_otim) > 0 else 0.0
            margem_otim_sku = float(row_otim['margem_total'].sum()) if len(row_otim) > 0 else 0.0
            receita_otim_sku = float(row_otim['receita_total'].sum()) if len(row_otim) > 0 and 'receita_total' in row_otim.columns else 0.0
            custo_otim_sku = float(row_otim['custo_total'].sum()) if len(row_otim) > 0 and 'custo_total' in row_otim.columns else 0.0
            
            # Buscar volume reservado (pedido) para este SKU específico
            reserva_sku = reserva_por_sku.get(item_id, 0)
            
            # Intermediários para reprodução
            preco_por_ovo = row_base['preco'] / row_base['qtd_ovos_por_caixa']
            custo_por_ovo = row_base['custo_ytd'] / row_base['qtd_ovos_por_caixa'] if 'custo_ytd' in row_base and row_base['qtd_ovos_por_caixa'] > 0 else 0
            soma_vol_hist = float(total_vol_hist) if total_vol_hist > 0 else 0
            
            auditoria_classes.append({
                'classe': classe,
                'item_id': item_id,
                'item': int(row_base['item']),
                'descricao': row_base.get('descricao', ''),
                'embalagem': row_base.get('embalagem', ''),
                'qtd_ovos_por_caixa': row_base['qtd_ovos_por_caixa'],
                'preco': row_base['preco'],
                'custo_ytd': row_base['custo_ytd'],
                'margem_unitaria': row_base['margem_unitaria'],
                # Intermediários por ovo (reprodução: margem_unitaria / qtd_ovos_por_caixa)
                'preco_por_ovo': preco_por_ovo,
                'custo_por_ovo': custo_por_ovo,
                'margem_por_ovo': row_base['margem_por_ovo'],
                # Pedidos: reserva por SKU e por classe
                'reserva_pedido_sku': reserva_sku,
                'quantidade_pedida_sku': 0,
                'deficit_pedido_sku': 0,
                'ordem_prioridade': None,
                'prod_disponivel_antes': None,
                'prod_disponivel_depois': None,
                'reserva_pedidos_classe': reserva_por_classe.get(classe, 0),
                # Classe: produção e volume otimizável
                'producao_total_classe': float(producao_por_classe[classe]),
                'volume_classe': qtd_producao,
                # Demanda histórica
                'limite_demanda_historica': row_base.get('limite_demanda_historica', None),
                'volume_historico_total': row_base.get('volume_historico_total', 0),
                'soma_vol_hist_classe': soma_vol_hist,
                # Proporção (reprodução: volume_historico_total / soma_vol_hist_classe)
                'proporcao_historica': row_base['proporcao_historica'],
                # Baseline antes do cap (volume_classe × proporcao_historica, sem restrição)
                'volume_baseline_antes_cap': row_base['volume_baseline_antes_cap'],
                # Baseline (após cap ao limite_demanda_historica + redistribuição)
                'volume_baseline': row_base['volume_baseline'],
                'receita_baseline': row_base['volume_baseline'] * preco_por_ovo,
                'custo_baseline': row_base['custo_baseline_sku'],
                'margem_baseline': row_base['margem_baseline_sku'],
                # Otimizado (do resultado do solver)
                'volume_otimizado': vol_otim,
                'receita_otimizada': receita_otim_sku,
                'custo_otimizado': custo_otim_sku,
                'margem_otimizada': margem_otim_sku,
                'tipo_sku': 'otimizacao',
            })
    
    # Incluir SKUs de reserva (pedidos) na auditoria
    if 'tipo' in resultado.columns:
        df_reserva_audit = resultado[resultado['tipo'] == 'reserva'].copy()
        for _, row_res in df_reserva_audit.iterrows():
            classe_res = row_res.get('classe', '')
            vol_reserva = float(row_res['quantidade']) if pd.notna(row_res['quantidade']) else 0
            qtd_pedida = float(row_res['quantidade_pedida']) if pd.notna(row_res.get('quantidade_pedida')) else vol_reserva
            deficit = float(row_res['deficit_pedido']) if pd.notna(row_res.get('deficit_pedido')) else 0
            preco_res = float(row_res['preco']) if pd.notna(row_res.get('preco')) else 0
            custo_res = float(row_res['custo_ytd']) if pd.notna(row_res.get('custo_ytd')) else 0
            qtd_ovos = float(row_res.get('qtd_ovos_por_caixa', 360)) if pd.notna(row_res.get('qtd_ovos_por_caixa')) else 360
            margem_unit = float(row_res.get('margem_unitaria', 0)) if pd.notna(row_res.get('margem_unitaria')) else 0
            preco_ovo = preco_res / qtd_ovos if qtd_ovos > 0 else 0
            custo_ovo = custo_res / qtd_ovos if qtd_ovos > 0 else 0
            margem_ovo = preco_ovo - custo_ovo
            
            # Rastreabilidade da heurística de priorização
            ordem_prior = int(row_res['ordem_prioridade']) if pd.notna(row_res.get('ordem_prioridade')) else None
            prod_antes = float(row_res['prod_disponivel_antes']) if pd.notna(row_res.get('prod_disponivel_antes')) else None
            prod_depois = float(row_res['prod_disponivel_depois']) if pd.notna(row_res.get('prod_disponivel_depois')) else None
            
            auditoria_classes.append({
                'classe': classe_res,
                'item_id': str(row_res.get('item', '')),
                'item': int(row_res['item']) if pd.notna(row_res.get('item')) else 0,
                'descricao': row_res.get('descricao', ''),
                'embalagem': row_res.get('embalagem', ''),
                'qtd_ovos_por_caixa': qtd_ovos,
                'preco': preco_res,
                'custo_ytd': custo_res,
                'margem_unitaria': margem_unit,
                'preco_por_ovo': preco_ovo,
                'custo_por_ovo': custo_ovo,
                'margem_por_ovo': margem_ovo,
                'reserva_pedido_sku': vol_reserva,
                'quantidade_pedida_sku': qtd_pedida,
                'deficit_pedido_sku': deficit,
                'ordem_prioridade': ordem_prior,
                'prod_disponivel_antes': prod_antes,
                'prod_disponivel_depois': prod_depois,
                'reserva_pedidos_classe': reserva_por_classe.get(classe_res, 0),
                'producao_total_classe': float(producao_por_classe[classe_res]) if classe_res in producao_por_classe.index else 0,
                'volume_classe': 0,  # não participa da otimização
                'limite_demanda_historica': None,
                'volume_historico_total': 0,
                'soma_vol_hist_classe': 0,
                'proporcao_historica': 0,
                'volume_baseline_antes_cap': 0,
                'volume_baseline': 0,
                'receita_baseline': 0,
                'custo_baseline': 0,
                'margem_baseline': 0,
                'volume_otimizado': 0,
                'receita_otimizada': 0,
                'custo_otimizado': 0,
                'margem_otimizada': 0,
                'tipo_sku': 'reserva',
            })
    
    # Calcular ganhos
    ganho_margem = margem_otimizada - margem_baseline
    ganho_margem_pct = (ganho_margem / margem_baseline * 100) if margem_baseline > 0 else 0
    
    reducao_custo = custo_baseline - custo_otimizado
    reducao_custo_pct = (reducao_custo / custo_baseline * 100) if custo_baseline > 0 else 0
    
    # Log do comparativo
    logger.info("\n" + "="*80)
    logger.info("COMPARATIVO: BASELINE vs OTIMIZADO")
    logger.info("="*80)
    logger.info("  (Comparação justa: mesmo volume otimizável em ambos; reserva e OUTROS excluídos)")
    logger.info("  (Baseline = distribuição proporcional ao histórico de vendas por SKU)")
    logger.info(f"  Margem Baseline (sem realocação): R$ {margem_baseline:,.2f}")
    logger.info(f"  Margem Otimizada (com realocação): R$ {margem_otimizada:,.2f}")
    logger.info(f"  GANHO MARGEM: R$ {ganho_margem:,.2f} ({ganho_margem_pct:.2f}%)")
    logger.info(f"  Custo Baseline (sem realocação): R$ {custo_baseline:,.2f}")
    logger.info(f"  Custo Otimizado (com realocação): R$ {custo_otimizado:,.2f}")
    logger.info(f"  Variação Custo: R$ {reducao_custo:,.2f} ({reducao_custo_pct:.2f}%)")
    
    # Gerar arquivo de auditoria
    if auditoria_classes:
        _gerar_auditoria_baseline(auditoria_classes, config, logger)
    
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


def _gerar_auditoria_baseline(
    auditoria_classes: list,
    config: dict,
    logger: logging.Logger
) -> None:
    """
    Gera arquivo Excel de auditoria do cálculo baseline vs otimizado.
    
    Abas:
    - Detalhe por SKU: dados completos por SKU com baseline e otimizado
    - Resumo por Classe: totais por classe com verificação de volumes
    - Parâmetros: configuração usada no cálculo
    """
    from datetime import datetime
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path('resultados')
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f'auditoria_baseline_{timestamp}.xlsx'
    
    df_audit = pd.DataFrame(auditoria_classes)
    
    # === ABA 1: Detalhe por SKU ===
    df_detalhe = df_audit[[
        # Identificação
        'classe', 'item_id', 'item', 'descricao', 'embalagem', 'tipo_sku',
        # Dados unitários por caixa
        'qtd_ovos_por_caixa', 'preco', 'custo_ytd', 'margem_unitaria',
        # Intermediários por ovo (preco/qtd, custo/qtd, margem/qtd)
        'preco_por_ovo', 'custo_por_ovo', 'margem_por_ovo',
        # Pedidos: reserva por SKU, pedido original, déficit, priorização
        'reserva_pedido_sku', 'quantidade_pedida_sku', 'deficit_pedido_sku',
        'ordem_prioridade', 'prod_disponivel_antes', 'prod_disponivel_depois',
        'reserva_pedidos_classe',
        # Classe: produção e volume otimizável
        'producao_total_classe', 'volume_classe',
        # Demanda e proporção histórica
        'limite_demanda_historica', 'volume_historico_total', 'soma_vol_hist_classe',
        'proporcao_historica',
        # Baseline: volume antes do cap, volume final, receita, custo, margem
        'volume_baseline_antes_cap', 'volume_baseline', 'receita_baseline', 'custo_baseline', 'margem_baseline',
        # Otimizado: volume, receita, custo, margem
        'volume_otimizado', 'receita_otimizada', 'custo_otimizado', 'margem_otimizada',
    ]].copy()
    
    # Coluna de redistribuição do cap (volume_baseline - volume_baseline_antes_cap)
    df_detalhe['volume_redistribuido'] = df_detalhe['volume_baseline'] - df_detalhe['volume_baseline_antes_cap']
    
    # Status do cap baseline
    df_detalhe['status_cap_baseline'] = 'SEM_ALTERACAO'
    df_detalhe.loc[df_detalhe['volume_redistribuido'] < -0.5, 'status_cap_baseline'] = 'CAPADO'
    df_detalhe.loc[df_detalhe['volume_redistribuido'] > 0.5, 'status_cap_baseline'] = 'RECEBEU_REDISTRIB'
    # Reservas não participam do cap
    df_detalhe.loc[df_detalhe['tipo_sku'] == 'reserva', 'status_cap_baseline'] = ''
    
    # Colunas de diferença (otimizado - baseline)
    df_detalhe['diferenca_volume'] = df_detalhe['volume_otimizado'] - df_detalhe['volume_baseline']
    df_detalhe['diferenca_receita'] = df_detalhe['receita_otimizada'] - df_detalhe['receita_baseline']
    df_detalhe['diferenca_custo'] = df_detalhe['custo_otimizado'] - df_detalhe['custo_baseline']
    df_detalhe['diferenca_margem'] = df_detalhe['margem_otimizada'] - df_detalhe['margem_baseline']
    
    # Ordenar por classe e margem_por_ovo (descendente)
    df_detalhe = df_detalhe.sort_values(['classe', 'margem_por_ovo'], ascending=[True, False])
    
    # === ABA 2: Resumo por Classe ===
    resumo = df_audit.groupby('classe').agg(
        producao_total_classe=('producao_total_classe', 'first'),
        reserva_pedidos_classe=('reserva_pedidos_classe', 'first'),
        volume_classe=('volume_classe', 'first'),
        num_skus=('item_id', 'nunique'),
        # Baseline
        soma_volume_baseline=('volume_baseline', 'sum'),
        receita_baseline_classe=('receita_baseline', 'sum'),
        custo_baseline_classe=('custo_baseline', 'sum'),
        margem_baseline_classe=('margem_baseline', 'sum'),
        # Otimizado
        soma_volume_otimizado=('volume_otimizado', 'sum'),
        receita_otimizada_classe=('receita_otimizada', 'sum'),
        custo_otimizado_classe=('custo_otimizado', 'sum'),
        margem_otimizada_classe=('margem_otimizada', 'sum'),
    ).reset_index()
    
    # Verificações de volume
    resumo['volume_baseline_bate'] = (
        (resumo['soma_volume_baseline'] - resumo['volume_classe']).abs() < 1.0
    ).map({True: 'OK', False: 'ERRO'})
    resumo['volume_otimizado_bate'] = (
        (resumo['soma_volume_otimizado'] - resumo['volume_classe']).abs() < 1.0
    ).map({True: 'OK', False: 'ERRO'})
    # Verificação margem = receita - custo
    resumo['margem_base_bate'] = (
        (resumo['margem_baseline_classe'] - (resumo['receita_baseline_classe'] - resumo['custo_baseline_classe'])).abs() < 1.0
    ).map({True: 'OK', False: 'ERRO'})
    resumo['margem_otim_bate'] = (
        (resumo['margem_otimizada_classe'] - (resumo['receita_otimizada_classe'] - resumo['custo_otimizado_classe'])).abs() < 1.0
    ).map({True: 'OK', False: 'ERRO'})
    # Ganho
    resumo['ganho_absoluto'] = resumo['margem_otimizada_classe'] - resumo['margem_baseline_classe']
    resumo['ganho_percentual'] = resumo.apply(
        lambda r: (r['ganho_absoluto'] / r['margem_baseline_classe'] * 100) if r['margem_baseline_classe'] > 0 else 0,
        axis=1
    )
    
    # Linha de totais
    cols_soma = [
        'producao_total_classe', 'reserva_pedidos_classe', 'volume_classe', 'num_skus',
        'soma_volume_baseline', 'receita_baseline_classe', 'custo_baseline_classe', 'margem_baseline_classe',
        'soma_volume_otimizado', 'receita_otimizada_classe', 'custo_otimizado_classe', 'margem_otimizada_classe',
        'ganho_absoluto',
    ]
    totais_dict = {'classe': 'TOTAL'}
    for c in cols_soma:
        totais_dict[c] = resumo[c].sum()
    totais_dict['volume_baseline_bate'] = ''
    totais_dict['volume_otimizado_bate'] = ''
    totais_dict['margem_base_bate'] = ''
    totais_dict['margem_otim_bate'] = ''
    totais_dict['ganho_percentual'] = (
        (totais_dict['ganho_absoluto'] / totais_dict['margem_baseline_classe'] * 100)
        if totais_dict['margem_baseline_classe'] > 0 else 0
    )
    totais = pd.DataFrame([totais_dict])
    resumo = pd.concat([resumo, totais], ignore_index=True)
    
    # === ABA 3: Parâmetros ===
    modelo_cfg = config.get('modelo', {})
    dados_cfg = config.get('dados', {})
    params = pd.DataFrame([
        {'parametro': 'tipo_calculo_demanda', 'valor': modelo_cfg.get('tipo_calculo_demanda', 'maximo')},
        {'parametro': 'fator_demanda_maxima', 'valor': modelo_cfg.get('fator_demanda_maxima', 1.2)},
        {'parametro': 'considerar_demanda_historica', 'valor': modelo_cfg.get('considerar_demanda_historica', False)},
        {'parametro': 'granularidade_demanda', 'valor': modelo_cfg.get('granularidade_demanda', 'S')},
        {'parametro': 'percentil_demanda', 'valor': modelo_cfg.get('percentil_demanda', 95)},
        {'parametro': 'variaveis_continuas', 'valor': modelo_cfg.get('variaveis_continuas', True)},
        {'parametro': 'mes_referencia', 'valor': dados_cfg.get('mes_custo', 11)},
        {'parametro': 'ano_referencia', 'valor': dados_cfg.get('ano_custo', 2025)},
        {'parametro': 'meses_janela', 'valor': dados_cfg.get('meses_janela_custo', 6)},
        {'parametro': 'metodo_baseline', 'valor': 'Proporção histórica real (volume vendido no período, sem fator)'},
        {'parametro': 'data_geracao', 'valor': timestamp},
    ])
    
    # === Salvar Excel ===
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df_detalhe.to_excel(writer, sheet_name='Detalhe por SKU', index=False)
        resumo.to_excel(writer, sheet_name='Resumo por Classe', index=False)
        params.to_excel(writer, sheet_name='Parametros', index=False)
    
    logger.info(f"  Auditoria baseline salva em: {path}")


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
        
        # Gerar linhas de reserva
        colunas_base = list(resultado.resultado.columns)
        linhas_reserva = []
        for p in pedidos_info:
            linhas_reserva.append({
                'item_id': f"{p['item']}_RESERVA",
                'item': p['item'],
                'descricao': desc_map.get(p['item'], None),
                'embalagem': 'RESERVA',
                'classe': p['classe'],
                'quantidade': p['quantidade_reservada'],
                'quantidade_pedida': p['quantidade_pedida'],
                'deficit_pedido': p['deficit_pedido'],
                'ordem_prioridade': p.get('ordem_prioridade'),
                'prod_disponivel_antes': p.get('prod_disponivel_antes'),
                'prod_disponivel_depois': p.get('prod_disponivel_depois'),
                'quantidade_caixas': 0.0,
                'preco': p['preco'],
                'custo_ytd': p['custo_ytd'],
                'margem_unitaria': p['margem_unitaria'],
                'margem_por_ovo': None,
                'receita_total': 0.0,
                'custo_total': 0.0,
                'margem_total': 0.0,
                'limite_demanda_historica': None,
                'producao_disponivel': 0.0,
                'producao_total': 0.0,
                'tem_demanda_historica': False,
                'custo_medio_classe': False,
                'tipo': 'reserva',
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
                'custo_medio_classe': row.get('custo_medio_classe', False),
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
