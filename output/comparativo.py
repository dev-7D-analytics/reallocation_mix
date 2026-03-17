"""
Módulo de comparativo baseline vs otimizado.

Funções:
- calcular_comparativo_baseline: Calcula margem baseline vs otimizada
- _gerar_auditoria_baseline: Gera Excel de auditoria detalhada

Autor: Romulo Brito
"""

import logging
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict

from output.metadados_output import aplicar_colunas_estabelecimento
from output.producao_rastreabilidade import obter_producao_classe_rastreabilidade


def _resolver_output_dir(config: dict) -> Path:
    """Resolve diretório de saída a partir do config com fallback legado."""
    output_dir = Path(config.get("paths", {}).get("output_dir", "resultados"))
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


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
    
    # Separar resultado por tipo: otimizável vs reserva
    if 'tipo' in resultado.columns:
        mask_otimizavel = (resultado['tipo'] != 'reserva') & (resultado['tipo'] != 'outros')
        df_otimizavel = resultado.loc[mask_otimizavel]
        df_reserva_resultado = resultado[resultado['tipo'] == 'reserva']
    else:
        df_otimizavel = resultado
        df_reserva_resultado = pd.DataFrame()

    # Métricas otimizadas: APENAS volume otimizável (excluir reserva e OUTROS)
    margem_otimizada = df_otimizavel['margem_total'].sum() if len(df_otimizavel) > 0 else 0
    custo_otimizado = df_otimizavel['custo_total'].sum() if len(df_otimizavel) > 0 and 'custo_total' in df_otimizavel.columns else 0
    volume_otimizado_total = df_otimizavel['quantidade'].sum() if len(df_otimizavel) > 0 and 'quantidade' in df_otimizavel.columns else 0
    producao_liquida_total = float(producao_por_classe.sum()) if len(producao_por_classe) > 0 else 0.0
    taxa_alocacao_sobre_producao = (
        (volume_otimizado_total / producao_liquida_total) * 100
        if producao_liquida_total > 0
        else None
    )

    # Métricas de reserva (pedidos garantidos) - reportadas separadamente
    reserva_volume = df_reserva_resultado['quantidade'].sum() if len(df_reserva_resultado) > 0 else 0
    reserva_margem = df_reserva_resultado['margem_total'].sum() if len(df_reserva_resultado) > 0 else 0
    reserva_receita = df_reserva_resultado['receita_total'].sum() if len(df_reserva_resultado) > 0 and 'receita_total' in df_reserva_resultado.columns else 0
    reserva_custo = df_reserva_resultado['custo_total'].sum() if len(df_reserva_resultado) > 0 and 'custo_total' in df_reserva_resultado.columns else 0
    reserva_n_skus = len(df_reserva_resultado)
    reserva_deficit = df_reserva_resultado['deficit_pedido'].sum() if len(df_reserva_resultado) > 0 and 'deficit_pedido' in df_reserva_resultado.columns else 0
    reserva_volume_pedido = reserva_volume + reserva_deficit

    # Margem reserva SEM priorização: todos os pedidos com mesma taxa de atendimento
    reserva_margem_sem_priorizacao = 0.0
    if reserva_volume_pedido > 0 and len(df_reserva_resultado) > 0 and 'margem_por_ovo' in df_reserva_resultado.columns:
        taxa_atendimento = reserva_volume / reserva_volume_pedido if reserva_volume_pedido > 0 else 0
        qtd_pedida = df_reserva_resultado['quantidade'] + df_reserva_resultado.get('deficit_pedido', pd.Series(0, index=df_reserva_resultado.index)).fillna(0)
        reserva_margem_sem_priorizacao = (qtd_pedida * taxa_atendimento * df_reserva_resultado['margem_por_ovo']).sum()
    ganho_priorizacao = reserva_margem - reserva_margem_sem_priorizacao

    # Determinar se usa demanda histórica
    considerar_demanda = config.get('modelo', {}).get('considerar_demanda_historica', False)
    prod_trace = obter_producao_classe_rastreabilidade(config)
    prod_trace_map = {}
    if len(prod_trace) > 0:
        prod_trace_map = prod_trace.set_index("classe").to_dict(orient="index")

    # Volume alocado por classe (apenas otimizável)
    if 'classe' in resultado.columns:
        if len(df_otimizavel) > 0:
            qtd_alocada_por_classe = df_otimizavel.groupby('classe')['quantidade'].sum()
        else:
            qtd_alocada_por_classe = None
    else:
        qtd_alocada_por_classe = None
    
    margem_baseline = 0.0
    custo_baseline = 0.0
    auditoria_classes = []  # Dados de auditoria por classe/SKU
    
    # Calcular volume reservado (pedidos) por classe e por SKU a partir do resultado
    reserva_por_classe = {}
    reserva_por_sku = {}
    if len(df_reserva_resultado) > 0 and 'classe' in df_reserva_resultado.columns:
        reserva_por_classe = df_reserva_resultado.groupby('classe')['quantidade'].sum().to_dict()
        reserva_por_sku = df_reserva_resultado.groupby('item_id')['quantidade'].sum().to_dict()
    
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
                'margem_unitaria_cx360': row_base['margem_unitaria'],
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
                'producao_bruta_classe': float(prod_trace_map.get(classe, {}).get('producao_bruta_classe', 0.0)),
                'producao_estorno_eac_classe': float(prod_trace_map.get(classe, {}).get('producao_estorno_eac_classe', 0.0)),
                'producao_liquida_classe': float(prod_trace_map.get(classe, {}).get('producao_liquida_classe', float(producao_por_classe[classe]))),
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
                'margem_unitaria_cx360': margem_unit,
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
                'producao_bruta_classe': float(prod_trace_map.get(classe_res, {}).get('producao_bruta_classe', 0.0)),
                'producao_estorno_eac_classe': float(prod_trace_map.get(classe_res, {}).get('producao_estorno_eac_classe', 0.0)),
                'producao_liquida_classe': float(prod_trace_map.get(classe_res, {}).get('producao_liquida_classe', float(producao_por_classe[classe_res]) if classe_res in producao_por_classe.index else 0.0)),
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
    
    # Totais consolidados (otimização + reserva)
    margem_total_modelo = margem_otimizada + reserva_margem
    custo_total_modelo = custo_otimizado + reserva_custo

    # Log do comparativo
    logger.info("\n" + "="*80)
    logger.info("COMPARATIVO: BASELINE vs OTIMIZADO (apenas volume otimizável)")
    logger.info("="*80)
    logger.info("  (Comparação justa: mesmo volume em ambos; reserva e OUTROS excluídos)")
    logger.info("  (Baseline = distribuição proporcional ao histórico de vendas por SKU)")
    logger.info(f"  Margem Baseline (proporcional histórico): R$ {margem_baseline:,.2f}")
    logger.info(f"  Margem Otimizada (solver):               R$ {margem_otimizada:,.2f}")
    logger.info(f"  GANHO MARGEM OTIMIZAÇÃO: R$ {ganho_margem:,.2f} ({ganho_margem_pct:.2f}%)")
    logger.info(f"  Custo Baseline:   R$ {custo_baseline:,.2f}")
    logger.info(f"  Custo Otimizado:  R$ {custo_otimizado:,.2f}")
    logger.info(f"  Variação Custo: R$ {reducao_custo:,.2f} ({reducao_custo_pct:.2f}%)")
    taxa_aloc_str = f"{taxa_alocacao_sobre_producao:.2f}%" if taxa_alocacao_sobre_producao is not None else "N/A"
    logger.info(
        f"  Taxa alocação sobre produção líquida: {taxa_aloc_str} "
        f"({volume_otimizado_total:,.0f} / {producao_liquida_total:,.0f} ovos)"
    )

    logger.info("")
    logger.info("-"*80)
    logger.info("PEDIDOS RESERVADOS (garantidos antes da otimização)")
    logger.info("-"*80)
    logger.info(f"  SKUs com pedidos reservados: {reserva_n_skus}")
    logger.info(f"  Volume total pedido:   {reserva_volume_pedido:>14,.0f} ovos")
    logger.info(f"  Volume atendido:       {reserva_volume:>14,.0f} ovos")
    logger.info(f"  Deficit (não atendido): {reserva_deficit:>14,.0f} ovos")
    taxa_str = f"{(reserva_volume / reserva_volume_pedido * 100):.1f}%" if reserva_volume_pedido > 0 else "N/A"
    logger.info(f"  Taxa de atendimento: {taxa_str}")
    logger.info(f"  Receita reservas:  R$ {reserva_receita:>14,.2f}")
    logger.info(f"  Custo reservas:    R$ {reserva_custo:>14,.2f}")
    logger.info(f"  Margem reservas (COM priorização por margem):  R$ {reserva_margem:>14,.2f}")
    logger.info(f"  Margem reservas (SEM priorização, proporcional): R$ {reserva_margem_sem_priorizacao:>14,.2f}")
    logger.info(f"  GANHO DA PRIORIZAÇÃO: R$ {ganho_priorizacao:,.2f}")

    logger.info("")
    logger.info("-"*80)
    logger.info("RESULTADO CONSOLIDADO (otimização + reservas)")
    logger.info("-"*80)
    logger.info(f"  Margem total do modelo: R$ {margem_total_modelo:,.2f}")
    logger.info(f"    Margem otimização:  R$ {margem_otimizada:,.2f}")
    logger.info(f"    Margem reservas:    R$ {reserva_margem:,.2f}")
    logger.info(f"  Custo total do modelo:  R$ {custo_total_modelo:,.2f}")
    logger.info("="*80)

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
        'reducao_custo_pct': reducao_custo_pct,
        'volume_otimizado_total': volume_otimizado_total,
        'producao_liquida_total': producao_liquida_total,
        'taxa_alocacao_sobre_producao_pct': taxa_alocacao_sobre_producao,
        'reserva_volume_pedido': reserva_volume_pedido,
        'reserva_volume_atendido': reserva_volume,
        'reserva_deficit': reserva_deficit,
        'reserva_n_skus': reserva_n_skus,
        'reserva_margem': reserva_margem,
        'reserva_margem_sem_priorizacao': reserva_margem_sem_priorizacao,
        'ganho_priorizacao': ganho_priorizacao,
        'margem_total_modelo': margem_total_modelo,
        'custo_total_modelo': custo_total_modelo,
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
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = _resolver_output_dir(config)
    path = output_dir / f'auditoria_baseline_{timestamp}.xlsx'
    
    df_audit = pd.DataFrame(auditoria_classes)
    
    # === ABA 1: Detalhe por SKU ===
    df_detalhe = df_audit[[
        # Identificação
        'classe', 'item_id', 'item', 'descricao', 'embalagem', 'tipo_sku',
        # Dados unitários por caixa
        'qtd_ovos_por_caixa', 'preco', 'custo_ytd', 'margem_unitaria', 'margem_unitaria_cx360',
        # Intermediários por ovo (preco/qtd, custo/qtd, margem/qtd)
        'preco_por_ovo', 'custo_por_ovo', 'margem_por_ovo',
        # Pedidos: reserva por SKU, pedido original, déficit, priorização
        'reserva_pedido_sku', 'quantidade_pedida_sku', 'deficit_pedido_sku',
        'ordem_prioridade', 'prod_disponivel_antes', 'prod_disponivel_depois',
        'reserva_pedidos_classe',
        # Classe: produção e volume otimizável
        'producao_total_classe', 'producao_bruta_classe', 'producao_estorno_eac_classe', 'producao_liquida_classe', 'volume_classe',
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
    df_detalhe = aplicar_colunas_estabelecimento(df_detalhe, config)
    
    # === ABA 2: Resumo por Classe ===
    resumo = df_audit.groupby('classe').agg(
        producao_total_classe=('producao_total_classe', 'first'),
        producao_bruta_classe=('producao_bruta_classe', 'first'),
        producao_estorno_eac_classe=('producao_estorno_eac_classe', 'first'),
        producao_liquida_classe=('producao_liquida_classe', 'first'),
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
        'producao_total_classe', 'producao_bruta_classe', 'producao_estorno_eac_classe', 'producao_liquida_classe',
        'reserva_pedidos_classe', 'volume_classe', 'num_skus',
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
    resumo = aplicar_colunas_estabelecimento(resumo, config)
    
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
        {'parametro': 'semana_ref', 'valor': dados_cfg.get('semana_ref', 'N/A')},
        {'parametro': 'data_ref', 'valor': dados_cfg.get('data_ref', 'N/A')},
        {'parametro': 'mes_referencia', 'valor': dados_cfg.get('mes_custo', 11)},
        {'parametro': 'ano_referencia', 'valor': dados_cfg.get('ano_custo', 2025)},
        {'parametro': 'meses_janela', 'valor': dados_cfg.get('meses_janela_custo', 6)},
        {'parametro': 'metodo_baseline', 'valor': 'Proporção histórica real (volume vendido no período, sem fator)'},
        {'parametro': 'data_geracao', 'valor': timestamp},
    ])
    params = aplicar_colunas_estabelecimento(params, config)
    
    # === Salvar Excel ===
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df_detalhe.to_excel(writer, sheet_name='Detalhe por SKU', index=False)
        resumo.to_excel(writer, sheet_name='Resumo por Classe', index=False)
        params.to_excel(writer, sheet_name='Parametros', index=False)
    
    logger.info(f"  Auditoria baseline salva em: {path}")
