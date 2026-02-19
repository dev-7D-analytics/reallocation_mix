"""
Gera limites de demanda histórica por SKU a partir do faturamento bruto.

O output (inputs/demanda_historica.csv) pode ser editado manualmente pelo
usuário antes de rodar o modelo de otimização. Basta ter as colunas
'item' e 'demanda_max'.

O CSV já sai com o fator multiplicativo aplicado (ex: media × 1.2).

Uso:
    python gerar_demanda_historica.py                  # usa config.yaml
    python gerar_demanda_historica.py --fator 1.5      # sobrescreve fator
    python gerar_demanda_historica.py --tipo maximo    # tipo de cálculo
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import argparse
import yaml
from typing import Dict

sys.path.append(str(Path(__file__).parent))


def carregar_config(config_path: str = 'config.yaml') -> Dict:
    """Carrega configurações do YAML."""
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _aplicar_correcao_estabelecimento(df: pd.DataFrame, config: Dict) -> pd.DataFrame:
    """Aplica correção de estabelecimento (mesma lógica do ETL).
    
    Replica exatamente a lógica de ETLPipeline._aplicar_correcao_estabelecimento():
    - Coluna de cliente: 'Cod.Emitente'
    - Arquivo de correção: colunas 'Cliente' e 'Estab Padrao'
    - Mapeamento: se cliente está no mapa, usa Estab Padrao; senão, mantém Estab original
    """
    path_estab = Path(config.get('paths', {}).get('estab_corrigido', 'inputs/ESTAB CORRIGIDO.xlsx'))
    
    if not path_estab.exists():
        print(f"  [AVISO] Arquivo de correção não encontrado: {path_estab}")
        df['Estab_Corrigido'] = df.get('Estab', pd.Series(dtype='object'))
        return df
    
    col_cliente = 'Cod.Emitente' if 'Cod.Emitente' in df.columns else None
    
    if col_cliente is None:
        print("  [AVISO] Coluna 'Cod.Emitente' não encontrada - correção não aplicada")
        df['Estab_Corrigido'] = df.get('Estab', pd.Series(dtype='object'))
        return df

    try:
        df_correcao = pd.read_excel(path_estab, engine='openpyxl')
        
        if 'Cliente' not in df_correcao.columns or 'Estab Padrao' not in df_correcao.columns:
            print("  [AVISO] Colunas 'Cliente' ou 'Estab Padrao' não encontradas")
            df['Estab_Corrigido'] = df['Estab']
            return df
        
        # Mapeamento Cliente -> Estab Padrao (mesma lógica do ETL)
        mapa_estab = dict(zip(df_correcao['Cliente'], df_correcao['Estab Padrao']))
        
        # Se o cliente está no mapeamento, usa Estab Padrao; senão, mantém original (vetorizado)
        df['Estab_Corrigido'] = (
            df[col_cliente].map(mapa_estab).fillna(df['Estab'])
        )
        
        n_corrigidos = (df['Estab'] != df['Estab_Corrigido']).sum()
        print(f"  Correção de estabelecimento: {n_corrigidos:,} de {len(df):,} registros corrigidos")
        print(f"  Mapeamento: {len(mapa_estab):,} clientes")
        
    except Exception as e:
        print(f"  [AVISO] Erro ao carregar correção de estabelecimento: {e}")
        df['Estab_Corrigido'] = df.get('Estab', pd.Series(dtype='object'))

    return df


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gera limites de demanda histórica por SKU."
    )
    parser.add_argument("--tipo", type=str, choices=['media', 'maximo', 'percentil'],
                        help="Tipo de cálculo (sobrescreve config.yaml).")
    parser.add_argument("--fator", type=float,
                        help="Fator multiplicativo (sobrescreve config.yaml).")
    parser.add_argument("--granularidade", type=str, choices=['S', 'M', 'D'],
                        help="Granularidade: S(emanal), M(ensal), D(iário).")
    parser.add_argument("--percentil", type=int,
                        help="Percentil (só quando tipo=percentil).")
    parser.add_argument("--meses_janela", type=int,
                        help="Janela em meses (sobrescreve config.yaml).")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config = carregar_config()
    dados_config = config.get('dados', {})
    modelo_config = config.get('modelo', {})

    # Parâmetros: argumento > config.yaml > default
    tipo_calculo = (args.tipo or modelo_config.get('tipo_calculo_demanda', 'media')).lower()
    fator = args.fator or modelo_config.get('fator_demanda_maxima', 1.2)
    granularidade = (args.granularidade or modelo_config.get('granularidade_demanda', 'S')).upper()
    percentil = args.percentil or modelo_config.get('percentil_demanda', 95)
    meses_janela = args.meses_janela or dados_config.get('meses_janela_custo', 6)
    mes_ref = dados_config.get('mes_custo', 11)
    ano_ref = dados_config.get('ano_custo', 2025)

    gran_desc = {'S': 'SEMANAL', 'M': 'MENSAL', 'D': 'DIÁRIO'}.get(granularidade, 'SEMANAL')

    print("=" * 80)
    print("GERAÇÃO DE DEMANDA HISTÓRICA POR SKU")
    print("=" * 80)

    # ── 1. Carregar faturamento ─────────────────────────────────────────────
    print("\n[1/4] Carregando faturamento...")

    path_fat = Path(config['paths'].get('faturamento', 'inputs/faturamento.parquet'))
    if not path_fat.exists():
        print(f"[ERRO] Arquivo não encontrado: {path_fat}")
        return

    print(f"  Arquivo: {path_fat}")
    df_fat = pd.read_parquet(path_fat)
    print(f"  Registros totais: {len(df_fat):,}")

    # Detectar colunas
    col_item = 'item' if 'item' in df_fat.columns else None
    col_qtd = 'Quantidade' if 'Quantidade' in df_fat.columns else None
    col_data = 'Dt.Emissão' if 'Dt.Emissão' in df_fat.columns else None

    if not all([col_item, col_qtd, col_data]):
        print("[ERRO] Colunas necessárias não encontradas (item, Quantidade, Dt.Emissão)")
        return

    # ── 2. Filtros ──────────────────────────────────────────────────────────
    print("\n[2/4] Aplicando filtros...")

    # Correção de estabelecimento
    df_fat = _aplicar_correcao_estabelecimento(df_fat, config)

    # Filtrar estabelecimentos
    estabs_manter = config.get('negocio', {}).get('filtrar_granjas', [])
    if estabs_manter and 'Estab_Corrigido' in df_fat.columns:
        antes = len(df_fat)
        df_fat = df_fat[df_fat['Estab_Corrigido'].astype(str).isin([str(e) for e in estabs_manter])].copy()
        print(f"  Estab={estabs_manter}: {len(df_fat):,} registros (removidos {antes - len(df_fat):,})")

    # Converter quantidade para ovos (base normalizada para cx360)
    df_fat[col_qtd] = pd.to_numeric(df_fat[col_qtd], errors='coerce')
    df_fat[col_qtd] = df_fat[col_qtd] * 360
    print(f"  Quantidade convertida para ovos (× 360)")

    # Filtrar período
    df_fat[col_data] = pd.to_datetime(df_fat[col_data], errors='coerce')
    df_fat = df_fat[df_fat[col_data].notna()].copy()

    periodos = []
    for i in range(meses_janela):
        m = mes_ref - i
        a = ano_ref
        while m <= 0:
            m += 12
            a -= 1
        periodos.append((a, m))

    periodos_str = ', '.join([f'{a}-{m:02d}' for a, m in periodos])
    df_fat['_periodo'] = list(zip(df_fat[col_data].dt.year, df_fat[col_data].dt.month))
    antes = len(df_fat)
    df_fat = df_fat[df_fat['_periodo'].isin(set(periodos))].copy()
    df_fat = df_fat.drop(columns=['_periodo'])
    print(f"  Período: {periodos_str}")
    print(f"  Registros no período: {len(df_fat):,} (removidos {antes - len(df_fat):,})")

    if len(df_fat) == 0:
        print("[ERRO] Nenhum registro após filtros.")
        return

    # ── 3. Calcular demanda por SKU ─────────────────────────────────────────
    print(f"\n[3/4] Calculando demanda ({gran_desc}, tipo={tipo_calculo}, fator={fator})...")

    # Agregar por período
    if granularidade == 'S':
        df_fat['periodo'] = df_fat[col_data].dt.to_period('W')
    elif granularidade == 'M':
        df_fat['periodo'] = df_fat[col_data].dt.to_period('M')
    else:
        df_fat['periodo'] = df_fat[col_data].dt.date

    df_agregado = df_fat.groupby([col_item, 'periodo'])[col_qtd].sum().reset_index()
    df_agregado.columns = ['item', 'periodo', 'demanda_periodo']

    n_periodos = df_agregado['periodo'].nunique()
    print(f"  Períodos com dados: {n_periodos}")

    # Calcular limite
    if tipo_calculo == 'media':
        df_demanda = df_agregado.groupby('item')['demanda_periodo'].mean().reset_index()
        df_demanda.columns = ['item', 'demanda_base']
        desc_calculo = f"MÉDIA × {fator}"
    elif tipo_calculo == 'percentil':
        df_demanda = df_agregado.groupby('item')['demanda_periodo'].quantile(percentil / 100).reset_index()
        df_demanda.columns = ['item', 'demanda_base']
        desc_calculo = f"P{percentil} × {fator}"
    else:
        df_demanda = df_agregado.groupby('item')['demanda_periodo'].max().reset_index()
        df_demanda.columns = ['item', 'demanda_base']
        desc_calculo = f"MÁXIMO × {fator}"

    # Aplicar fator e garantir que não haja valores negativos
    df_demanda['demanda_max'] = (df_demanda['demanda_base'] * fator).clip(lower=0)

    # Volume histórico total (sem fator, soma de todos os períodos)
    df_volume = df_agregado.groupby('item')['demanda_periodo'].sum().reset_index()
    df_volume.columns = ['item', 'volume_historico_total']
    df_demanda = df_demanda.merge(df_volume, on='item', how='left')

    df_demanda['item'] = pd.to_numeric(df_demanda['item'], errors='coerce')
    df_demanda = df_demanda[df_demanda['item'].notna()].copy()
    df_demanda['item'] = df_demanda['item'].astype(int)
    df_demanda = df_demanda.sort_values('demanda_max', ascending=False)

    print(f"  SKUs com demanda: {len(df_demanda):,}")
    print(f"  Limite médio: {df_demanda['demanda_max'].mean():,.0f} ovos/{gran_desc.lower()}")

    # ── 4. Salvar ───────────────────────────────────────────────────────────
    output_csv = Path("inputs/demanda_historica.csv")
    output_xlsx = Path("inputs/demanda_historica.xlsx")
    output_csv.parent.mkdir(exist_ok=True)

    # CSV: item + demanda_max + volume_historico_total (editável)
    df_final = df_demanda[['item', 'demanda_max', 'volume_historico_total']].copy()
    df_final.to_csv(output_csv, index=False, encoding='utf-8')

    # Excel: com colunas extras para rastreabilidade
    try:
        df_ref = df_demanda[['item', 'demanda_base', 'demanda_max', 'volume_historico_total']].copy()
        df_ref.rename(columns={'demanda_base': f'demanda_{tipo_calculo}'}, inplace=True)

        # Aba de parâmetros
        df_params = pd.DataFrame([
            {'parametro': 'tipo_calculo', 'valor': tipo_calculo},
            {'parametro': 'fator_multiplicativo', 'valor': fator},
            {'parametro': 'granularidade', 'valor': granularidade},
            {'parametro': 'periodos', 'valor': periodos_str},
            {'parametro': 'n_periodos_com_dados', 'valor': n_periodos},
            {'parametro': 'arquivo_faturamento', 'valor': str(path_fat)},
        ])

        with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
            df_ref.to_excel(writer, sheet_name='Demanda', index=False)
            df_params.to_excel(writer, sheet_name='Parametros', index=False)

        print(f"\n[OK] Dataset salvo:")
        print(f"  CSV: {output_csv}  (editável: item + demanda_max)")
        print(f"  Excel: {output_xlsx}  (com {desc_calculo} e parâmetros)")
    except Exception as e:
        print(f"\n[OK] Dataset salvo: {output_csv}")
        print(f"  [AVISO] Excel não salvo: {e}")

    # ── Estatísticas ────────────────────────────────────────────────────────
    print(f"\n" + "=" * 80)
    print("ESTATÍSTICAS")
    print("=" * 80)
    print(f"\n  Cálculo: {desc_calculo}")
    print(f"  Granularidade: {gran_desc}")
    print(f"  Período: {periodos_str}")
    print(f"  SKUs: {len(df_final):,}")
    print(f"  Limite médio: {df_final['demanda_max'].mean():,.0f} ovos")
    print(f"  Limite mediano: {df_final['demanda_max'].median():,.0f} ovos")
    print(f"  Faixa: {df_final['demanda_max'].min():,.0f} - {df_final['demanda_max'].max():,.0f} ovos")

    print(f"\nTop 10 SKUs por demanda:")
    top10 = df_demanda.head(10)[['item', 'demanda_max', 'volume_historico_total']].copy()
    top10['demanda_max'] = top10['demanda_max'].map('{:,.0f}'.format)
    top10['volume_historico_total'] = top10['volume_historico_total'].map('{:,.0f}'.format)
    print(top10.to_string(index=False))


if __name__ == "__main__":
    main()
