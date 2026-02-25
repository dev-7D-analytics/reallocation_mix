"""
Gera custos médios por SKU a partir da base bruta de custos (Parquet PRIC).

Calcula custo como: sum(Custo Médio) / sum(Quantidade)  (média ponderada por volume)

O output (inputs/custos_sku.csv) pode ser editado manualmente pelo usuário
antes de rodar o modelo de otimização. Basta ter as colunas 'item' e 'custo_ytd'.

Uso:
    python gerar_custos_sku.py                       # usa parâmetros do config.yaml
    python gerar_custos_sku.py --mes 11 --ano 2025   # sobrescreve período
    python gerar_custos_sku.py --meses_janela 6      # janela de 6 meses
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import argparse
import yaml
from typing import Dict

# Importar função de extração de embalagem
sys.path.append(str(Path(__file__).parent))
from extrair_compatibilidade_embalagem import extrair_embalagem_descricao


def carregar_config(config_path: str = 'config.yaml') -> Dict:
    """Carrega configurações do YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gera custos médios por SKU a partir da base bruta de custos."
    )
    parser.add_argument(
        "--estab", type=int,
        help="Estabelecimento para filtro (sobrescreve config.yaml).",
    )
    parser.add_argument(
        "--mes", type=int,
        help="Mês final da janela (sobrescreve config.yaml).",
    )
    parser.add_argument(
        "--ano", type=int,
        help="Ano de referência (sobrescreve config.yaml).",
    )
    parser.add_argument(
        "--meses_janela", type=int,
        help="Janela em meses (sobrescreve config.yaml).",
    )
    parser.add_argument(
        "--arquivo", type=str,
        help="Caminho do arquivo de custos (sobrescreve config.yaml).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config = carregar_config()
    dados_config = config.get('dados', {})

    # Parâmetros: argumento > config.yaml > default
    estab_custo = args.estab or dados_config.get('estab_custo', 100)
    mes_custo = args.mes or dados_config.get('mes_custo', 11)
    ano_custo = args.ano or dados_config.get('ano_custo', 2025)
    meses_janela = args.meses_janela or dados_config.get('meses_janela_custo', 6)

    print("=" * 80)
    print("GERAÇÃO DE CUSTOS POR SKU")
    print("=" * 80)

    # ── 1. Carregar base bruta ──────────────────────────────────────────────
    print("\n[1/3] Carregando base bruta de custos...")

    if args.arquivo:
        path_custo = Path(args.arquivo)
    else:
        path_custo = Path(config['paths'].get('custos', 'inputs/custos.parquet'))

    if not path_custo.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_custo}")
        return

    print(f"  Arquivo: {path_custo}")

    colunas_necessarias = [
        'Estab', 'item', 'MÊS', 'ano',
        'Custo Médio', 'Quantidade', 'UF', 'Descrição do item'
    ]
    df = pd.read_parquet(path_custo, columns=colunas_necessarias, engine='pyarrow')
    print(f"  Registros totais: {len(df):,}")

    # Converter tipos
    df['Custo Médio'] = pd.to_numeric(df['Custo Médio'], errors='coerce')
    df['Quantidade'] = pd.to_numeric(df['Quantidade'], errors='coerce')
    df['item'] = pd.to_numeric(df['item'], errors='coerce')
    df['MÊS'] = pd.to_numeric(df['MÊS'], errors='coerce')
    df['ano'] = pd.to_numeric(df['ano'], errors='coerce')
    df['Estab'] = pd.to_numeric(df['Estab'], errors='coerce')

    # ── 2. Filtros ──────────────────────────────────────────────────────────
    print("\n[2/3] Aplicando filtros...")

    # Filtro: Estabelecimento
    antes = len(df)
    df = df[df['Estab'] == estab_custo].copy()
    print(f"  Estab={estab_custo}: {len(df):,} registros (removidos {antes - len(df):,})")

    # Filtro: Período (janela de meses)
    periodos = []
    for i in range(meses_janela):
        m = mes_custo - i
        a = ano_custo
        while m <= 0:
            m += 12
            a -= 1
        periodos.append((a, m))

    periodos_str = ', '.join([f'{a}-{m:02d}' for a, m in periodos])
    print(f"  Janela: {meses_janela} meses ({periodos_str})")

    df['_periodo'] = list(zip(df['ano'], df['MÊS']))
    antes = len(df)
    df = df[df['_periodo'].isin(periodos)].copy()
    df = df.drop(columns=['_periodo'])
    print(f"  Após filtro de período: {len(df):,} registros (removidos {antes - len(df):,})")

    # Filtro: Exportação
    antes = len(df)
    df = df[df['UF'] != 'EX'].copy()
    removidos_exp = antes - len(df)
    if removidos_exp > 0:
        print(f"  Exportação (UF='EX') removida: {removidos_exp:,} registros")

    # Filtro: Registros válidos
    antes = len(df)
    df = df[
        df['item'].notna() &
        (df['Quantidade'] > 0) &
        df['Custo Médio'].notna()
    ].copy()
    print(f"  Registros válidos (Qtd>0, Custo notna): {len(df):,} (removidos {antes - len(df):,})")

    if len(df) == 0:
        print("[ERRO] Nenhum registro após filtros. Verifique os parâmetros.")
        return

    # ── 3. Calcular custo por SKU ───────────────────────────────────────────
    print("\n[3/3] Calculando custo médio ponderado por SKU...")

    # Extrair embalagem da descrição
    df['embalagem'] = df['Descrição do item'].apply(extrair_embalagem_descricao)
    df = df[df['embalagem'].notna()].copy()
    df['item'] = df['item'].astype(int)

    # Criar ano_mes para agregação em 2 passos (mesma lógica do ETL original)
    df['ano_mes'] = df['ano'].astype(int).astype(str) + '-' + df['MÊS'].astype(int).astype(str).str.zfill(2)

    # 1ª agregação: por (item, embalagem, ano_mes)
    custos_mes = df.groupby(['item', 'embalagem', 'ano_mes']).agg({
        'Quantidade': 'sum',
        'Custo Médio': 'sum'
    }).reset_index()

    # 2ª agregação: por (item, embalagem) - soma total
    custos_agg = custos_mes.groupby(['item', 'embalagem']).agg({
        'Quantidade': 'sum',
        'Custo Médio': 'sum'
    }).reset_index()

    # Custo final = sum(Custo Médio) / sum(Quantidade) (média ponderada)
    custos_agg['custo_ytd'] = custos_agg['Custo Médio'] / custos_agg['Quantidade']

    # Filtrar custos válidos
    custos_agg = custos_agg[
        custos_agg['custo_ytd'].notna() &
        (custos_agg['custo_ytd'] > 0)
    ].copy()

    # Output a nível item+embalagem (preserva granularidade original)
    custos_agg = custos_agg.sort_values('Quantidade', ascending=False)
    custos_agg['item'] = custos_agg['item'].astype(int)

    print(f"  Combinações (item+embalagem) antes filtro ativos: {len(custos_agg):,}")

    # Filtrar apenas SKUs ativos no estabelecimento
    path_skus = Path(config.get('paths', {}).get('skus_restritos', 'inputs/skus_restritos.xlsx'))
    estabelecimentos = config.get('dados', {}).get('estabelecimentos', [100])
    if path_skus.exists():
        df_skus = pd.read_excel(path_skus)
        if 'STATUS' in df_skus.columns and 'ESTAB' in df_skus.columns:
            df_ativos = df_skus[
                (df_skus['STATUS'] == 'ATIVO') &
                (df_skus['ESTAB'].isin(estabelecimentos))
            ]
            skus_ativos = set(df_ativos['item'].astype(int).tolist())
            antes = len(custos_agg)
            custos_agg = custos_agg[custos_agg['item'].isin(skus_ativos)]
            print(f"  Filtro SKUs ativos (ESTAB={estabelecimentos}): {antes} -> {len(custos_agg):,}")

    # ── Salvar ──────────────────────────────────────────────────────────────
    output_csv = Path("inputs/custos_sku.csv")
    output_xlsx = Path("inputs/custos_sku.xlsx")
    output_csv.parent.mkdir(exist_ok=True)

    # CSV: item + custo_ytd (editável pelo usuário)
    # Inclui embalagem para rastreabilidade, mas o ETL aceita sem ela
    df_final = custos_agg[['item', 'embalagem', 'custo_ytd']].copy()
    df_final.to_csv(output_csv, index=False, encoding='utf-8')

    # Excel: com volume para referência
    try:
        custos_agg[['item', 'embalagem', 'custo_ytd', 'Quantidade']].rename(
            columns={'Quantidade': 'volume_total'}
        ).to_excel(output_xlsx, index=False, engine='openpyxl')
        print(f"\n[OK] Dataset salvo:")
        print(f"  CSV: {output_csv}  (editável: basta item + custo_ytd)")
        print(f"  Excel: {output_xlsx}  (com embalagem e volume para referência)")
    except Exception as e:
        print(f"\n[OK] Dataset salvo: {output_csv}")
        print(f"  [AVISO] Excel não salvo: {e}")

    print(f"  Combinações (item+embalagem): {len(df_final):,}")
    print(f"  SKUs únicos: {df_final['item'].nunique():,}")

    # ── Estatísticas ────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("ESTATÍSTICAS")
    print("=" * 80)
    print(f"\n  Custo médio geral: R$ {df_final['custo_ytd'].mean():.2f}")
    print(f"  Custo mediano geral: R$ {df_final['custo_ytd'].median():.2f}")
    print(f"  Faixa: R$ {df_final['custo_ytd'].min():.2f} - R$ {df_final['custo_ytd'].max():.2f}")
    print(f"\n  Parâmetros utilizados:")
    print(f"    Estab: {estab_custo}")
    print(f"    Período: {periodos_str}")
    print(f"    Arquivo: {path_custo}")

    print(f"\nTop 10 combinações por volume:")
    top10 = custos_agg.head(10)[['item', 'embalagem', 'custo_ytd', 'Quantidade']].copy()
    top10['Quantidade'] = top10['Quantidade'].map('{:,.0f}'.format)
    top10['custo_ytd'] = top10['custo_ytd'].map('R$ {:.2f}'.format)
    print(top10.to_string(index=False))


if __name__ == "__main__":
    main()
