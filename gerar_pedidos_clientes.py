"""
Gera dataset de pedidos a partir da CARTEIRA_VENDIDA.xlsx.

Lê a carteira de pedidos vendidos (TOTVS), filtra por estabelecimento e SKUs ativos,
converte quantidades de caixas para ovos e gera o arquivo pedidos_clientes.csv
usado como input do modelo de otimização.

Campos de saída:
- Estabelecimento, item, descricao, quantidade (ovos), preco_pedido (R$/cx), data_entrega

Autor: Romulo Brito
"""
import pandas as pd
import numpy as np
from pathlib import Path
import yaml

from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem


def carregar_config():
    """Carrega configuração do arquivo config.yaml."""
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}


def carregar_skus_ativos(config: dict) -> set:
    """Retorna conjunto de SKUs ativos no estabelecimento configurado."""
    path = Path(config.get('paths', {}).get('skus_restritos', 'inputs/skus_restritos.xlsx'))
    if not path.exists():
        return set()

    df = pd.read_excel(path)
    if 'STATUS' not in df.columns or 'ESTAB' not in df.columns:
        return set()

    estabelecimentos = config.get('dados', {}).get('estabelecimentos', [100])
    df_ativos = df[
        (df['STATUS'] == 'ATIVO') &
        (df['ESTAB'].isin(estabelecimentos))
    ]
    return set(df_ativos['item'].astype(int).tolist())


def main():
    print("=" * 80)
    print("GERAÇÃO DE PEDIDOS - CARTEIRA VENDIDA (TOTVS)")
    print("=" * 80)

    config = carregar_config()
    dados_cfg = config.get('dados', {})
    avisos = []

    def registrar_aviso(msg: str):
        avisos.append(msg)
        print(f"  [AVISO] {msg}")

    # Caminhos
    path_carteira = Path(config.get('paths', {}).get('carteira_vendida', 'inputs/CARTEIRA_VENDIDA.xlsx'))
    output_path = Path(config.get('paths', {}).get('pedidos', 'inputs/pedidos_clientes.csv'))

    estabelecimentos = config.get('dados', {}).get('estabelecimentos', [100])

    if not path_carteira.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_carteira}")
        return

    # 1. Carregar carteira
    print(f"\n[1/4] Carregando carteira vendida: {path_carteira}")
    df = pd.read_excel(path_carteira, sheet_name='RELATORIO_TOTVS')
    print(f"  Registros totais: {len(df):,}")

    # 2. Aplicar filtros
    print("\n[2/4] Aplicando filtros...")

    # Filtro: Estabelecimento
    df = df[df['Estab.'].isin(estabelecimentos)].copy()
    print(f"  Após filtro Est={estabelecimentos}: {len(df):,}")

    # Filtro: Situação (Aberto + Atendido Parcial)
    situacoes_validas = ['Aberto', 'Atendido Parcial']
    df = df[df['Situação'].isin(situacoes_validas)].copy()
    print(f"  Após filtro situação {situacoes_validas}: {len(df):,}")

    # Filtro: SKUs ativos
    skus_ativos = carregar_skus_ativos(config)
    if skus_ativos:
        df['Item'] = pd.to_numeric(df['Item'], errors='coerce')
        antes = len(df)
        df = df[df['Item'].isin(skus_ativos)].copy()
        print(f"  Após filtro SKUs ativos: {antes} -> {len(df):,}")
    else:
        print("  [AVISO] Lista de SKUs ativos não encontrada, usando todos")

    # Filtro: Período de entrega conforme granularidade do modelo.
    # Referências de pedidos são independentes das referências de produção:
    # - semanal: dados.pedidos_semana_ref (fallback: dados.semana_ref)
    # - diário/mensal: dados.pedidos_data_ref (fallback: dados.data_ref)
    df['Dt.Entrega'] = pd.to_datetime(df['Dt.Entrega'], errors='coerce')
    granularidade = config.get('modelo', {}).get('granularidade_demanda', 'S').upper()
    antes_periodo = len(df)
    pedidos_semana_ref = dados_cfg.get('pedidos_semana_ref')
    pedidos_data_ref = dados_cfg.get('pedidos_data_ref')
    semana_ref_modelo = dados_cfg.get('semana_ref')
    data_ref_modelo = dados_cfg.get('data_ref')

    # Warnings de combinação inválida (chave informada mas incompatível com granularidade ativa)
    if granularidade == 'S':
        if pedidos_data_ref not in [None, ""]:
            registrar_aviso(
                "dados.pedidos_data_ref foi informado, mas será ignorado porque "
                "granularidade_demanda='S' (usa referência semanal)."
            )
    elif granularidade in ['D', 'M']:
        if pedidos_semana_ref not in [None, ""]:
            registrar_aviso(
                "dados.pedidos_semana_ref foi informado, mas será ignorado porque "
                f"granularidade_demanda='{granularidade}' (usa referência por data)."
            )

    # Warnings de fallback para referências gerais
    if granularidade == 'S' and pedidos_semana_ref in [None, ""]:
        registrar_aviso(
            "dados.pedidos_semana_ref não informado; usando fallback dados.semana_ref."
        )
    if granularidade in ['D', 'M'] and pedidos_data_ref in [None, ""]:
        registrar_aviso(
            "dados.pedidos_data_ref não informado; usando fallback dados.data_ref."
        )

    if granularidade == 'D':
        data_ref_raw = pedidos_data_ref if pedidos_data_ref not in [None, ""] else data_ref_modelo
        data_ref = pd.to_datetime(data_ref_raw)
        df = df[df['Dt.Entrega'].dt.date == data_ref.date()].copy()
        periodo_desc = f"dia {data_ref.strftime('%Y-%m-%d')}"
    elif granularidade == 'M':
        data_ref_raw = pedidos_data_ref if pedidos_data_ref not in [None, ""] else data_ref_modelo
        data_ref = pd.to_datetime(data_ref_raw)
        df = df[
            (df['Dt.Entrega'].dt.year == data_ref.year) &
            (df['Dt.Entrega'].dt.month == data_ref.month)
        ].copy()
        periodo_desc = f"mês {data_ref.strftime('%Y-%m')}"
    else:
        semana_ref = pedidos_semana_ref if pedidos_semana_ref not in [None, ""] else semana_ref_modelo
        df['year_week'] = (
            df['Dt.Entrega'].dt.isocalendar().year.astype(str) + '-' +
            df['Dt.Entrega'].dt.isocalendar().week.astype(str).str.zfill(2)
        )
        df = df[df['year_week'] == semana_ref].copy()
        periodo_desc = f"semana {semana_ref}"

    print(f"  Filtro período entrega ({periodo_desc}): {antes_periodo} -> {len(df):,}")

    if len(df) == 0:
        print("[AVISO] Nenhum registro após filtros")
        if avisos:
            print("\n" + "-" * 80)
            print("RESUMO DE AVISOS")
            print("-" * 80)
            for i, msg in enumerate(avisos, 1):
                print(f"  {i}. {msg}")
        return

    # 3. Converter quantidades
    print("\n[3/4] Convertendo quantidades de caixas para ovos...")

    df['embalagem'] = df['Descrição Item'].apply(extrair_embalagem_descricao)
    df['qtd_embalagem'] = df['embalagem'].apply(calcular_qtd_embalagem)
    df['quantidade_ovos'] = df['Qt.Pedida'] * df['qtd_embalagem']

    sem_embalagem = (df['qtd_embalagem'] == 0).sum()
    if sem_embalagem > 0:
        print(f"  [AVISO] {sem_embalagem} registros sem embalagem parseável (qtd_embalagem=0), removidos")
        df = df[df['qtd_embalagem'] > 0].copy()

    # 4. Agregar por item
    print("\n[4/4] Agregando por SKU...")

    pedidos = df.groupby('Item').agg(
        descricao=('Descrição Item', 'first'),
        quantidade=('quantidade_ovos', 'sum'),
        preco_pedido=('Preço', 'mean'),
        data_entrega_min=('Dt.Entrega', 'min'),
        data_entrega_max=('Dt.Entrega', 'max'),
        n_pedidos=('Pedido', 'nunique'),
        n_clientes=('Cliente', 'nunique'),
        qt_pedida_caixas=('Qt.Pedida', 'sum'),
    ).reset_index()

    pedidos = pedidos.rename(columns={'Item': 'item'})
    pedidos['item'] = pedidos['item'].astype(int)
    pedidos['quantidade'] = pedidos['quantidade'].round().astype(int)
    pedidos['preco_pedido'] = pedidos['preco_pedido'].round(2)

    # Formatar data_entrega como string
    pedidos['data_entrega_min'] = pedidos['data_entrega_min'].dt.strftime('%Y-%m-%d')
    pedidos['data_entrega_max'] = pedidos['data_entrega_max'].dt.strftime('%Y-%m-%d')

    pedidos.insert(0, 'Estabelecimento', estabelecimentos[0])

    pedidos = pedidos[pedidos['quantidade'] > 0].copy()
    pedidos = pedidos.sort_values('quantidade', ascending=False).reset_index(drop=True)

    # Salvar
    colunas_output = [
        'Estabelecimento', 'item', 'descricao', 'quantidade',
        'preco_pedido', 'data_entrega_min', 'data_entrega_max',
        'n_pedidos', 'n_clientes', 'qt_pedida_caixas'
    ]
    pedidos[colunas_output].to_csv(output_path, index=False, encoding='utf-8')

    # Resumo
    print("\n" + "=" * 80)
    print("RESULTADO")
    print("=" * 80)
    print(f"\n[OK] Arquivo gerado: {output_path}")
    print(f"  SKUs com pedido: {len(pedidos)}")
    print(f"  Pedidos únicos: {df['Pedido'].nunique()}")
    print(f"  Clientes únicos: {df['Cliente'].nunique()}")
    print(f"  Qt. total (caixas): {pedidos['qt_pedida_caixas'].sum():,.0f}")
    print(f"  Qt. total (ovos): {pedidos['quantidade'].sum():,.0f}")
    print(f"  Preço médio/cx: R$ {pedidos['preco_pedido'].mean():,.2f}")
    print(f"  Entrega mais próxima: {pedidos['data_entrega_min'].min()}")
    print(f"  Entrega mais distante: {pedidos['data_entrega_max'].max()}")

    print("\n" + "-" * 80)
    print("TOP 15 SKUs POR VOLUME (ovos)")
    print("-" * 80)
    for _, r in pedidos.head(15).iterrows():
        desc = str(r['descricao'])[:45]
        print(f"  {r['item']:>8} {desc:45s} {r['quantidade']:>12,} ovos  R${r['preco_pedido']:>8.2f}/cx  {r['n_pedidos']:>3} ped")

    if avisos:
        print("\n" + "-" * 80)
        print("RESUMO DE AVISOS")
        print("-" * 80)
        for i, msg in enumerate(avisos, 1):
            print(f"  {i}. {msg}")

    return pedidos


if __name__ == "__main__":
    main()
