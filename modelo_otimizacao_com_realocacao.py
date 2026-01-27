"""
MODELO DE OTIMIZACAO DE MIX DIARIO COM REALOCACAO ENTRE SKUs

Este modelo combina:
1. A estrutura do modelo_otimizacao_mix_diario.py (OR-Tools, estoque diario)
2. A logica do modelo_realocacao_completo.ipynb (realocacao entre SKUs da mesma classe)

DIFERENCA FUNDAMENTAL:
- Modelo antigo: cada SKU usa no maximo seu estoque
- Este modelo: SKUs da mesma classe COMPARTILHAM o estoque total da classe

Isso permite mover volume de SKUs com menor margem para SKUs com maior margem,
gerando ganho significativo.

Autor: Romulo Brito
Data: 2025-12-09
"""

import pandas as pd
import numpy as np
from pathlib import Path
import yaml
import logging
from typing import Dict, Optional
from ortools.linear_solver import pywraplp


class ModeloOtimizacaoComRealocacao:
    """
    Modelo de otimizacao que permite realocacao de volume entre SKUs da mesma classe.
    
    Exemplo:
    - Classe BRANCO_GRANDE_MTQ tem 3 SKUs no estoque: A (1000 un), B (500 un), C (300 un)
    - Estoque total da classe: 1800 unidades
    - Se SKU A tem margem maior, o modelo pode alocar mais que 1000 un para A
    - Desde que o total alocado nao ultrapasse 1800 unidades
    """
    
    def __init__(self, config_path: str = 'config.yaml'):
        """Inicializa o modelo."""
        self.config = self._carregar_config(config_path)
        self._setup_logging()
        self.dados: Dict = {}
        self.solver: Optional[pywraplp.Solver] = None
        self.variaveis: Dict = {}
        self.resultado: Optional[pd.DataFrame] = None
        
        self.logger.info("="*80)
        self.logger.info("MODELO DE OTIMIZACAO COM REALOCACAO ENTRE SKUs")
        self.logger.info("="*80)
    
    def _carregar_config(self, config_path: str) -> Dict:
        """Carrega configuracoes do YAML."""
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def _setup_logging(self):
        """Configura logging."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
    
    def carregar_dados(self):
        """Carrega todos os dados necessarios."""
        self.logger.info("\n" + "="*80)
        self.logger.info("ETAPA 1: CARREGAMENTO DE DADOS")
        self.logger.info("="*80)
        
        self._carregar_producao()
        self._carregar_classes()
        self._carregar_pedidos()
        # REMOVIDO: _carregar_compatibilidade() - nao e mais necessario (item ja vem com embalagem)
        self._carregar_precos()
        self._carregar_custos()
        self._carregar_demanda_historica()
        self._preparar_dados_otimizacao()
        
        self.logger.info("\n[OK] Dados carregados com sucesso!")
    
    def _carregar_producao(self):
        """Carrega producao por classe de produtos."""
        self.logger.info("\n[1/7] Carregando producao por classe...")
        
        path = Path(self.config['paths'].get('producao', 'inputs/producao_classe.csv'))
        
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de producao nao encontrado: {path}")
        
        df_producao = pd.read_csv(path)
        
        # Validar colunas
        if 'Classe_Produto' not in df_producao.columns or 'quantidade' not in df_producao.columns:
            raise ValueError("Arquivo de producao deve conter 'Classe_Produto' e 'quantidade'")
        
        # Agregar por classe (soma se houver duplicatas)
        df_producao_agg = df_producao.groupby('Classe_Produto')['quantidade'].sum().reset_index()
        df_producao_agg.columns = ['classe', 'producao_total']
        df_producao_agg = df_producao_agg[df_producao_agg['producao_total'] > 0]
        
        self.logger.info(f"  Classes com producao: {len(df_producao_agg)}")
        self.logger.info(f"  Producao total: {df_producao_agg['producao_total'].sum():,.0f} unidades")
        
        # Mostrar distribuicao
        print("\n  Distribuicao por classe (top 10):")
        for _, row in df_producao_agg.head(10).iterrows():
            self.logger.info(f"    {row['classe']}: {row['producao_total']:,.0f} unidades")
        
        self.dados['producao'] = df_producao_agg
    
    def _carregar_classes(self):
        """Carrega classificacao de SKUs por classe."""
        self.logger.info("\n[2/7] Carregando classificacao de SKUs...")
        
        path = Path(self.config['paths'].get('classes', 'inputs/base_skus_classes.xlsx'))
        df_classes = pd.read_excel(path)
        
        # Detectar coluna de classe
        col_classe = None
        for col in df_classes.columns:
            if 'classe' in col.lower() and 'produto' in col.lower():
                col_classe = col
                break
        
        if col_classe is None:
            raise ValueError("Coluna de classe nao encontrada em base_skus_classes.xlsx")
        
        df_classes = df_classes[['item', col_classe]].copy()
        df_classes.columns = ['item', 'classe']
        df_classes['classe'] = df_classes['classe'].fillna('OUTROS')
        
        # Merge com producao para ver quantos SKUs tem producao
        df_producao = self.dados.get('producao', pd.DataFrame(columns=['classe', 'producao_total']))
        df_classes_com_producao = df_classes.merge(df_producao, on='classe', how='inner')
        
        self.logger.info(f"  SKUs com classe: {len(df_classes)}")
        self.logger.info(f"  SKUs em classes com producao: {len(df_classes_com_producao)}")
        self.logger.info(f"  Classes unicas: {df_classes['classe'].nunique()}")
        
        # Mostrar distribuicao por classe (usando producao)
        if len(df_producao) > 0:
            self.logger.info(f"\n  Distribuicao por classe (top 10):")
            for _, row in df_producao.head(10).iterrows():
                num_skus = len(df_classes[df_classes['classe'] == row['classe']])
                self.logger.info(f"    {row['classe']}: {num_skus} SKUs, {row['producao_total']:,.0f} un")
        
        self.dados['classes'] = df_classes
    
    def _carregar_pedidos(self):
        """Carrega pedidos de clientes."""
        self.logger.info("\n[3/7] Carregando pedidos de clientes...")
        
        path = Path(self.config['paths'].get('pedidos', 'inputs/pedidos_clientes.csv'))
        
        if not path.exists():
            self.logger.warning("  Arquivo de pedidos nao encontrado! Continuando sem pedidos.")
            self.dados['pedidos'] = pd.DataFrame(columns=['cod_cliente', 'item', 'quantidade_pedida'])
            return
        
        df_pedidos = pd.read_csv(path)
        
        # Validar colunas
        if 'item' not in df_pedidos.columns or 'quantidade_pedida' not in df_pedidos.columns:
            raise ValueError("Arquivo de pedidos deve conter colunas 'item' e 'quantidade_pedida'")
        
        df_pedidos['item'] = pd.to_numeric(df_pedidos['item'], errors='coerce')
        df_pedidos = df_pedidos[df_pedidos['item'].notna()]
        df_pedidos['item'] = df_pedidos['item'].astype(int)
        df_pedidos = df_pedidos[df_pedidos['quantidade_pedida'] > 0]
        
        # Agregar pedidos por SKU (soma de todos os clientes)
        pedidos_por_sku = df_pedidos.groupby('item')['quantidade_pedida'].sum().reset_index()
        pedidos_por_sku.columns = ['item', 'quantidade_total_pedida']
        
        self.logger.info(f"  Pedidos carregados: {len(df_pedidos):,}")
        self.logger.info(f"  Clientes unicos: {df_pedidos['cod_cliente'].nunique() if 'cod_cliente' in df_pedidos.columns else 'N/A'}")
        self.logger.info(f"  SKUs com pedidos: {len(pedidos_por_sku)}")
        self.logger.info(f"  Quantidade total pedida: {pedidos_por_sku['quantidade_total_pedida'].sum():,.0f} unidades")
        
        self.dados['pedidos'] = df_pedidos
        self.dados['pedidos_por_sku'] = pedidos_por_sku
    
    # REMOVIDO: _carregar_compatibilidade()
    # Nao e mais necessario porque cada item no CUSTO ITEM.csv ja vem com embalagem
    # O item_id sera criado como (codigo_item + embalagem) no _carregar_custos()
    
    def _carregar_precos(self):
        """Carrega precos - deve ter mesmo formato que custos (item_id unico)."""
        self.logger.info("\n[4/7] Carregando precos...")
        
        path = Path(self.config['paths'].get('precos', 'inputs/precos_sku_embalagem.csv'))
        
        if not path.exists():
            self.logger.warning("  Arquivo de precos nao encontrado!")
            self.dados['precos'] = pd.DataFrame(columns=['item_id', 'preco'])
            return
        
        # Tentar ler com separador de virgula primeiro, depois ponto e virgula
        try:
            df_precos = pd.read_csv(path, sep=",", decimal=".")
        except:
            df_precos = pd.read_csv(path, sep=";", decimal=",")

        # Detectar coluna de preco
        if 'preco' not in df_precos.columns:
            if 'preco_ponderado' in df_precos.columns:
                df_precos['preco'] = df_precos['preco_ponderado']
            elif 'preco_medio' in df_precos.columns:
                df_precos['preco'] = df_precos['preco_medio']
        
        # Se precos vem no formato (item, embalagem), criar item_id
        if 'item' in df_precos.columns and 'embalagem' in df_precos.columns:
            df_precos['item'] = df_precos['item'].astype(int)
            df_precos['item_id'] = df_precos['item'].astype(str) + '_' + df_precos['embalagem']
        # Se ja vem com item_id, usar diretamente
        elif 'item_id' not in df_precos.columns:
            raise ValueError("Arquivo de precos deve conter 'item_id' ou ('item' e 'embalagem')")
        
        df_precos = df_precos[['item_id', 'preco']].copy()
        df_precos = df_precos[df_precos['preco'] > 0]
        
        # Remover duplicatas por item_id
        df_precos = df_precos.drop_duplicates(['item_id'])
        
        self.logger.info(f"  Itens unicos (item_id) com preco: {len(df_precos)}")
        self.logger.info(f"  Preco medio: R$ {df_precos['preco'].mean():.2f}")
        
        self.dados['precos'] = df_precos
    
    def _carregar_custos(self):
        """Carrega custos - suporta CSV ou Parquet com filtros."""
        self.logger.info("\n[5/7] Carregando custos...")
        
        # Importar funcao de extrair embalagem
        import re
        
        def extrair_embalagem_descricao(descricao: str) -> str:
            """Extrai padrao de embalagem da descricao do item."""
            if pd.isna(descricao):
                return None
            
            desc_upper = str(descricao).upper()
            
            # Padrao 1: CX COM [numero] BJ DE [numero] UN
            padrao1 = r'CX\s+COM\s+(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
            match = re.search(padrao1, desc_upper)
            if match:
                num_bj = match.group(1)
                num_un = match.group(2)
                return f"CX {num_bj} BJ {num_un} UN"
            
            # Padrao 2: CX [numero] BJ [numero] UN (sem COM/DE)
            padrao2 = r'CX\s+(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
            match = re.search(padrao2, desc_upper)
            if match:
                num_bj = match.group(1)
                num_un = match.group(2)
                return f"CX {num_bj} BJ {num_un} UN"
            
            # Padrao 3: CX [numero] BJ DE [numero] UN
            padrao3 = r'CX\s+(\d+)\s+BJ\s+DE\s+(\d+)(?:\s+UN)?'
            match = re.search(padrao3, desc_upper)
            if match:
                num_bj = match.group(1)
                num_un = match.group(2)
                return f"CX {num_bj} BJ {num_un} UN"
            
            # Padrao 4: CX COM [numero] BJ [numero] UN (sem DE)
            padrao4 = r'CX\s+COM\s+(\d+)\s+BJ\s+(\d+)(?:\s+UN)?'
            match = re.search(padrao4, desc_upper)
            if match:
                num_bj = match.group(1)
                num_un = match.group(2)
                return f"CX {num_bj} BJ {num_un} UN"
            
            return None
        
        path = Path(self.config['paths']['custos'])
        
        # Detectar se e parquet ou CSV
        is_parquet = path.suffix.lower() == '.parquet'
        
        if is_parquet:
            # Ler apenas colunas necessarias para economizar memoria
            colunas_necessarias = ['Estab', 'item', 'MÊS', 'ano', 'Custo Médio', 'Quantidade', 'UF', 'Descrição do item']
            self.logger.info(f"  Lendo arquivo Parquet (apenas colunas necessarias)...")
            
            # Ler parquet com filtros aplicados durante a leitura (eficiente)
            df_custo = pd.read_parquet(path, columns=colunas_necessarias, engine='pyarrow')
            
            # Aplicar filtros: Estab e janela de tempo do config
            dados_config = self.config.get('dados', {})
            mes_custo = dados_config.get('mes_custo', 11)  # Mês de referência
            ano_custo = dados_config.get('ano_custo', 2025)  # Ano de referência
            estab_custo = dados_config.get('estab_custo', 100)  # Default: 100
            meses_janela = dados_config.get('meses_janela_custo', 1)  # Janela de meses (últimos N meses)
            
            # Calcular range de meses
            if meses_janela > 1:
                # Criar lista de (ano, mês) para a janela
                periodos = []
                for i in range(meses_janela):
                    mes = mes_custo - i
                    ano = ano_custo
                    while mes <= 0:
                        mes += 12
                        ano -= 1
                    periodos.append((ano, mes))
                
                self.logger.info(f"  Aplicando filtros: Estab={estab_custo}, janela de {meses_janela} meses a partir de {ano_custo}-{mes_custo:02d}...")
                self.logger.info(f"  Períodos incluídos: {', '.join([f'{a}-{m:02d}' for a, m in periodos])}")
                
                # Filtrar por estabelecimento e período
                mask_estab = df_custo['Estab'] == estab_custo
                mask_periodo = df_custo.apply(lambda row: (row['ano'], row['MÊS']) in periodos, axis=1)
                df_custo = df_custo[mask_estab & mask_periodo].copy()
            else:
                # Comportamento original: apenas 1 mês
                self.logger.info(f"  Aplicando filtros: Estab={estab_custo}, MÊS={mes_custo}, ano={ano_custo}...")
                df_custo = df_custo[
                    (df_custo['Estab'] == estab_custo) & 
                    (df_custo['MÊS'] == mes_custo) & 
                    (df_custo['ano'] == ano_custo)
                ].copy()
            
            self.logger.info(f"  Registros apos filtros de periodo: {len(df_custo):,}")
            
            # Filtrar exportacao (UF != 'EX')
            antes_filtro_uf = len(df_custo)
            df_custo = df_custo.loc[df_custo['UF'] != 'EX'].copy()
            removidos_exportacao = antes_filtro_uf - len(df_custo)
            if removidos_exportacao > 0:
                self.logger.info(f"  Registros de exportacao (UF='EX') removidos: {removidos_exportacao:,}")
            
            # Converter tipos para numérico
            df_custo['Custo Médio'] = pd.to_numeric(df_custo['Custo Médio'], errors='coerce')
            df_custo['Quantidade'] = pd.to_numeric(df_custo['Quantidade'], errors='coerce')
            
            # Filtrar registros com quantidade > 0 e custo válido
            df_custo = df_custo[df_custo['Quantidade'] > 0].copy()
            df_custo = df_custo[df_custo['Custo Médio'].notna()].copy()
            
            # Calcular custo por caixa: custos_cx360 = Custo Médio / Quantidade
            df_custo['custos_cx360'] = df_custo['Custo Médio'] / df_custo['Quantidade']
            
            # Custo Médio já é o custo total da transação (para agregação ponderada)
            df_custo['custo_total'] = df_custo['Custo Médio']
            
            # Criar coluna ano_mes para agregacao
            df_custo['ano_mes'] = df_custo['ano'].astype(str) + '-' + df_custo['MÊS'].astype(str).str.zfill(2)
            
            # Extrair embalagem e criar item_id antes de agregar
            col_item_desc = 'Descrição do item'
            df_custo['embalagem'] = df_custo[col_item_desc].apply(extrair_embalagem_descricao)
            df_custo['item'] = pd.to_numeric(df_custo['item'], errors='coerce')
            df_custo = df_custo[df_custo['item'].notna() & df_custo['embalagem'].notna()].copy()
            df_custo['item_id'] = df_custo['item'].astype(str) + '_' + df_custo['embalagem']
            
            # Primeira agregação: por (item, embalagem, ano_mes) - somar custo total e quantidade
            # (mesmo racional de preços)
            self.logger.info(f"  Agregando por (item, embalagem, ano_mes)...")
            custos_por_ano_mes = df_custo.groupby(['item', 'embalagem', 'ano_mes']).agg({
                'Quantidade': 'sum',
                'custo_total': 'sum',
                col_item_desc: 'first'
            }).reset_index()
            
            # Calcular custo ponderado por ano_mes
            custos_por_ano_mes['custo_ponderado_ano_mes'] = custos_por_ano_mes['custo_total'] / custos_por_ano_mes['Quantidade']
            custos_por_ano_mes.columns = ['item', 'embalagem', 'ano_mes', 'volume_ano_mes', 'custo_total_ano_mes', 'descricao_item', 'custo_ponderado_ano_mes']
            
            # Segunda agregação: por (item, embalagem) - somar custos e volumes totais
            self.logger.info(f"  Agregando por (item, embalagem) usando média ponderada dos valores de ano_mes...")
            df_custo_agg = custos_por_ano_mes.groupby(['item', 'embalagem']).agg({
                'volume_ano_mes': 'sum',
                'custo_total_ano_mes': 'sum',
                'descricao_item': 'first',
                'ano_mes': 'count'  # Número de meses com dados
            }).reset_index()
            df_custo_agg.columns = ['item', 'embalagem', 'volume_total', 'custo_total', 'descricao_item', 'num_meses']
            
            # Calcular custo final ponderado por volume total
            df_custo_agg['custo_ytd'] = df_custo_agg['custo_total'] / df_custo_agg['volume_total']
            df_custo_agg['item_id'] = df_custo_agg['item'].astype(str) + '_' + df_custo_agg['embalagem']
            
            antes_agregacao = len(df_custo)
            df_custo = df_custo_agg[['item_id', 'item', 'embalagem', 'custo_ytd']]
            removidos_duplicatas = antes_agregacao - len(df_custo)
            if removidos_duplicatas > 0:
                self.logger.info(f"  Registros duplicados agregados: {removidos_duplicatas:,} (usando média ponderada por volume)")
                if meses_janela > 1:
                    self.logger.info(f"  Agregação inclui dados de {meses_janela} meses (média ponderada por ano_mes, depois média ponderada por item_id)")
            
        else:
            # Leitura de CSV (comportamento original)
            df_custo = pd.read_csv(path)
            
            # Extrair codigo do item
            col_item_desc = None
            for col in df_custo.columns:
                if 'item' in col.lower() and 'descri' in col.lower():
                    col_item_desc = col
                    break
            
            if col_item_desc is None:
                col_item_desc = df_custo.columns[0]
            
            df_custo['item'] = pd.to_numeric(
                df_custo[col_item_desc].str.extract(r'^(\d+)')[0],
                errors='coerce'
            )
            
            # Converter custo (formato CSV)
            def parse_currency(valor):
                if pd.isna(valor):
                    return np.nan
                limpo = str(valor).replace('R$', '').replace('.', '').replace(',', '.').strip()
                try:
                    return float(limpo) if limpo else np.nan
                except:
                    return np.nan
            
            col_custo = None
            for col in df_custo.columns:
                if 'custo' in col.lower() and 'ytd' in col.lower():
                    col_custo = col
                    break
            
            if col_custo:
                df_custo['custo_ytd'] = df_custo[col_custo].apply(parse_currency)
            else:
                # Se nao encontrar custo_ytd, tentar "Custo Médio"
                if 'Custo Médio' in df_custo.columns:
                    df_custo['custo_ytd'] = df_custo['Custo Médio']
                else:
                    raise ValueError("Coluna de custo nao encontrada no arquivo CSV")
        
        # Para CSV, extrair embalagem da descricao (Parquet ja foi processado acima)
        if not is_parquet:
            df_custo['embalagem'] = df_custo[col_item_desc].apply(extrair_embalagem_descricao)
        
        # Filtrar valores negativos de custo
        antes_filtro_negativo = len(df_custo)
        df_custo = df_custo[df_custo['custo_ytd'] > 0].copy()
        removidos_negativos = antes_filtro_negativo - len(df_custo)
        if removidos_negativos > 0:
            self.logger.info(f"  Registros com custo negativo removidos: {removidos_negativos:,}")
        
        # Filtrar apenas registros com item, embalagem e custo validos
        df_custo = df_custo[
            df_custo['item'].notna() & 
            df_custo['custo_ytd'].notna() & 
            df_custo['embalagem'].notna()
        ]
        df_custo['item'] = df_custo['item'].astype(int)
        
        # Criar item_id unico (codigo_item + embalagem) - apenas para CSV (Parquet ja tem)
        if not is_parquet:
            df_custo['item_id'] = df_custo['item'].astype(str) + '_' + df_custo['embalagem']
            
            # Agregar duplicatas por item_id usando media do custo (CSV)
            antes_agregacao = len(df_custo)
            df_custo_agg = df_custo.groupby('item_id').agg({
                'item': 'first',
                'embalagem': 'first',
                'custo_ytd': 'mean'
            }).reset_index()
            removidos_duplicatas = antes_agregacao - len(df_custo_agg)
            if removidos_duplicatas > 0:
                self.logger.info(f"  Registros duplicados agregados: {removidos_duplicatas:,} (usando media do custo)")
            
            df_custo = df_custo_agg
        
        self.logger.info(f"  Itens unicos (item_id) com custo: {len(df_custo)}")
        self.logger.info(f"  SKUs unicos (codigo) com custo: {df_custo['item'].nunique()}")
        self.logger.info(f"  Custo medio: R$ {df_custo['custo_ytd'].mean():.2f}")
        
        # Estatisticas de embalagens
        embalagens_unicas = df_custo['embalagem'].nunique()
        self.logger.info(f"  Embalagens unicas: {embalagens_unicas}")
        
        # Mostrar exemplos de combinacoes sem embalagem extraida (para debug)
        if 'embalagem' in df_custo.columns and df_custo['embalagem'].isna().any():
            sem_embalagem = df_custo[df_custo['embalagem'].isna()]
            self.logger.warning(f"  [AVISO] {len(sem_embalagem)} registros sem embalagem extraida (serao removidos)")
            if len(sem_embalagem) > 0 and col_item_desc in df_custo.columns:
                self.logger.warning(f"  Exemplos de descricoes sem embalagem:")
                for desc in sem_embalagem[col_item_desc].head(3):
                    self.logger.warning(f"    - {desc}")
        
        # Preparar dados finais para salvar
        df_custo_final = df_custo[['item_id', 'item', 'embalagem', 'custo_ytd']].copy()
        
        # Salvar custos processados em Excel (similar ao arquivo de preços)
        output_path_custos = Path("inputs/custos_sku_embalagem.xlsx")
        output_path_custos.parent.mkdir(exist_ok=True)
        
        try:
            df_custo_final.to_excel(output_path_custos, index=False, engine='openpyxl')
            self.logger.info(f"  Custos processados salvos: {output_path_custos}")
        except ImportError:
            self.logger.warning(f"  [AVISO] openpyxl nao instalado. Nao foi possivel salvar Excel.")
            # Salvar como CSV como fallback
            output_path_custos_csv = Path("inputs/custos_sku_embalagem.csv")
            df_custo_final.to_csv(output_path_custos_csv, index=False, encoding='utf-8')
            self.logger.info(f"  Custos processados salvos como CSV: {output_path_custos_csv}")
        except Exception as e:
            self.logger.warning(f"  [AVISO] Erro ao salvar custos em Excel: {e}")
            # Salvar como CSV como fallback
            output_path_custos_csv = Path("inputs/custos_sku_embalagem.csv")
            df_custo_final.to_csv(output_path_custos_csv, index=False, encoding='utf-8')
            self.logger.info(f"  Custos processados salvos como CSV: {output_path_custos_csv}")
        
        self.dados['custos'] = df_custo_final
    
    def _carregar_demanda_historica(self):
        """Carrega demanda historica por SKU para restricoes de viabilidade."""
        considerar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        
        if not considerar_demanda:
            self.logger.info("\n[6/7] Demanda historica: Desabilitada")
            self.dados['demanda_historica'] = pd.DataFrame(columns=['item', 'demanda_max'])
            return
        
        self.logger.info("\n[6/7] Carregando demanda historica...")
        
        # Tentar carregar faturamento historico
        path_fat = Path(self.config['paths'].get('faturamento', '../manti_fat_2024.parquet'))
        
        if not path_fat.exists():
            self.logger.warning("  Arquivo de faturamento historico nao encontrado!")
            self.logger.warning("  Continuando sem restricoes de demanda historica.")
            self.dados['demanda_historica'] = pd.DataFrame(columns=['item', 'demanda_max'])
            return
        
        try:
            df_fat = pd.read_parquet(path_fat)
            
            # Detectar colunas
            col_item = None
            col_qtd = None
            col_data = None
            
            for col in df_fat.columns:
                if 'item' in col.lower() and col_item is None:
                    col_item = col
                if 'quantidade' in col.lower() and col_qtd is None:
                    col_qtd = col
                if 'emiss' in col.lower() or 'data' in col.lower():
                    if col_data is None:
                        col_data = col
            
            if col_item is None or col_qtd is None or col_data is None:
                raise ValueError("Colunas necessarias nao encontradas no faturamento")
            
            # Filtrar granjas se configurado
            if self.config.get('negocio', {}).get('filtrar_granjas', []):
                granjas_filtrar = self.config['negocio']['filtrar_granjas']
                if 'Estab' in df_fat.columns:
                    df_fat = df_fat[~df_fat['Estab'].astype(str).isin(granjas_filtrar)].copy()
                    self.logger.info(f"  Filtrando granjas: {granjas_filtrar}")
                    self.logger.info(f"  Registros apos filtro de granjas: {len(df_fat):,}")
                else:
                    self.logger.warning("  Coluna 'Estab' nao encontrada para filtro de granjas.")
            
            # Corrigir unidades da quantidade de items (de caixas de 360 ovos para unidades)
            # TODO: CORRIGIR - Esta multiplicacao por 360 assume que todas as caixas tem 360 ovos,
            # mas existem outros tipos de embalagem (ex: CX 12 BJ 30 UN = 360, CX 24 BJ 10 UN = 240).
            # Deve usar a coluna 'CONV. P OVO' do faturamento ou calcular baseado na embalagem do item.
            df_fat[col_qtd] = df_fat[col_qtd] * 360
            
            # Converter data
            df_fat[col_data] = pd.to_datetime(df_fat[col_data], errors='coerce')
            df_fat = df_fat[df_fat[col_data].notna()]
            
            # Filtrar periodo historico
            periodo_meses = self.config.get('modelo', {}).get('periodo_historico_meses', 6)
            data_limite = df_fat[col_data].max() - pd.DateOffset(months=periodo_meses)
            df_fat = df_fat[df_fat[col_data] >= data_limite]
            
            # Ler granularidade (M=Mensal, S=Semanal, D=Diaria)
            granularidade = self.config.get('modelo', {}).get('granularidade_demanda', 'M').upper()
            if granularidade not in ['M', 'S', 'D']:
                self.logger.warning(f"  Granularidade invalida '{granularidade}', usando 'M' (Mensal)")
                granularidade = 'M'
            
            # Agregar por periodo baseado na granularidade
            if granularidade == 'M':
                # Agregar por mes (ano-mes)
                df_fat['periodo'] = df_fat[col_data].dt.to_period('M')
                periodo_desc = 'mensal'
            elif granularidade == 'S':
                # Agregar por semana (ano-semana)
                df_fat['periodo'] = df_fat[col_data].dt.to_period('W')
                periodo_desc = 'semanal'
            else:  # granularidade == 'D'
                # Agregar por dia
                df_fat['periodo'] = df_fat[col_data].dt.date
                periodo_desc = 'diaria'
            
            # Agregar quantidade por SKU e periodo
            df_agregado = df_fat.groupby([col_item, 'periodo'])[col_qtd].sum().reset_index()
            df_agregado.columns = ['item', 'periodo', 'demanda_periodo']
            
            # Calcular estatisticas por SKU sobre os periodos agregados
            df_demanda = df_agregado.groupby('item')['demanda_periodo'].agg([
                ('demanda_total', 'sum'),
                ('demanda_media', 'mean'),
                ('demanda_mediana', 'median'),
                ('demanda_maxima', 'max'),  # Maximo historico
                ('demanda_p50', lambda x: x.quantile(0.50)),
                ('demanda_p75', lambda x: x.quantile(0.75)),
                ('demanda_p90', lambda x: x.quantile(0.90)),
                ('num_periodos', 'count')
            ]).reset_index()
            
            # Ler tipo de calculo (percentil ou maximo)
            tipo_calculo = self.config.get('modelo', {}).get('tipo_calculo_demanda', 'percentil').lower()
            
            # Inicializar variaveis para logs
            fator_percentual = None
            percentil = None
            fator_expansao = None
            
            if tipo_calculo == 'maximo':
                # Usar maximo historico com fator percentual
                fator_percentual = self.config.get('modelo', {}).get('fator_percentual_maximo', 1.2)
                df_demanda['demanda_max'] = df_demanda['demanda_maxima'] * fator_percentual
                df_demanda['demanda_base'] = df_demanda['demanda_maxima']  # Para logs
                metodo_desc = f"MAXIMO HISTORICO × {fator_percentual}"
            else:
                # Usar percentil com fator de expansao (logica original)
                percentil = self.config.get('modelo', {}).get('percentil_demanda', 75)
                fator_expansao = self.config.get('modelo', {}).get('fator_expansao_demanda', 1.5)
                
                # Selecionar coluna de percentil baseada na configuracao
                if percentil == 50:
                    df_demanda['demanda_percentil'] = df_demanda['demanda_p50']
                elif percentil == 75:
                    df_demanda['demanda_percentil'] = df_demanda['demanda_p75']
                elif percentil == 90:
                    df_demanda['demanda_percentil'] = df_demanda['demanda_p90']
                else:
                    # Calcular percentil customizado diretamente dos periodos agregados
                    demanda_custom = df_agregado.groupby('item')['demanda_periodo'].quantile(percentil / 100.0).reset_index()
                    demanda_custom.columns = ['item', 'demanda_percentil']
                    df_demanda = df_demanda.merge(demanda_custom, on='item', how='left')
                    # Preencher com mediana se nao tiver valor
                    df_demanda['demanda_percentil'] = df_demanda['demanda_percentil'].fillna(df_demanda['demanda_mediana'])
                
                df_demanda['demanda_max'] = df_demanda['demanda_percentil'] * fator_expansao
                df_demanda['demanda_base'] = df_demanda['demanda_percentil']  # Para logs
                metodo_desc = f"PERCENTIL {percentil}% × {fator_expansao}"
            
            df_demanda['item'] = df_demanda['item'].astype(int)
            
            # Calcular demanda media mensal para comparacao
            df_demanda['demanda_media_mensal'] = df_demanda['demanda_total'] / periodo_meses
            
            # Filtrar apenas SKUs com demanda valida
            df_demanda = df_demanda[df_demanda['demanda_max'] > 0]
            
            self.logger.info(f"  SKUs com demanda historica: {len(df_demanda)}")
            self.logger.info(f"  Periodo analisado: {periodo_meses} meses")
            self.logger.info(f"  Granularidade: {periodo_desc.upper()} ({granularidade})")
            self.logger.info(f"  Metodo de calculo: {metodo_desc}")
            if tipo_calculo == 'maximo':
                self.logger.info(f"  Maximo historico medio: {df_demanda['demanda_base'].mean():,.0f} unidades")
                self.logger.info(f"  Fator percentual: {fator_percentual}x")
            else:
                self.logger.info(f"  Percentil utilizado: {percentil}%")
                self.logger.info(f"  Fator de expansao: {fator_expansao}x")
            self.logger.info(f"  Demanda maxima media: {df_demanda['demanda_max'].mean():,.0f} unidades")
            self.logger.info(f"  Periodos agregados por SKU (media): {df_demanda['num_periodos'].mean():.1f}")
            
            # Selecionar colunas para salvar (depende do tipo de calculo)
            colunas_salvar = ['item', 'demanda_max', 'demanda_media_mensal', 'num_periodos']
            if tipo_calculo == 'maximo':
                colunas_salvar.append('demanda_base')  # demanda_base = demanda_maxima
            else:
                if 'demanda_percentil' in df_demanda.columns:
                    colunas_salvar.append('demanda_percentil')
            
            self.dados['demanda_historica'] = df_demanda[colunas_salvar]
            
        except Exception as e:
            self.logger.warning(f"  Erro ao carregar demanda historica: {e}")
            self.logger.warning("  Continuando sem restricoes de demanda historica.")
            self.dados['demanda_historica'] = pd.DataFrame(columns=['item', 'demanda_max'])
    
    def _preparar_dados_otimizacao(self):
        """Prepara dados para otimizacao usando producao por classe."""
        self.logger.info("\n[7/7] Preparando dados para otimizacao...")
        
        df_producao = self.dados['producao']  # Producao por classe
        df_classes = self.dados['classes']  # Mapeamento item -> classe
        df_precos = self.dados['precos']  # Precos por item_id
        df_custos = self.dados['custos']  # Custos por item_id (ja inclui item + embalagem)
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        
        #  Criar base a partir de custos (cada linha ja e um item_id unico)
        # O item_id ja inclui codigo_item + embalagem
        # TODO: Avaliar se é melhor comecar a construção pela base de produção
        df_base = df_custos[['item_id', 'item', 'embalagem', 'custo_ytd']].copy()
        
        # Adicionar classe para cada item_id (usando o codigo do item)
        df_base = df_base.merge(df_classes, on='item', how='inner')
        
        # Filtrar apenas classes que tem producao
        df_base = df_base.merge(df_producao[['classe']], on='classe', how='inner')
        
        # Merge com precos por item_id
        df_base = df_base.merge(df_precos, on='item_id', how='left')
        
        # Preencher precos faltantes com media do SKU (mesmo codigo, diferentes embalagens)
        preco_medio_sku = df_precos.merge(df_custos[['item_id', 'item']], on='item_id').groupby('item')['preco'].mean()
        df_base['preco'] = df_base['preco'].fillna(df_base['item'].map(preco_medio_sku))
        
        # Se ainda nao tiver preco, usar media geral
        if df_base['preco'].isna().any():
            preco_medio_geral = df_precos['preco'].mean()
            df_base['preco'] = df_base['preco'].fillna(preco_medio_geral)
            self.logger.warning(f"  {df_base['preco'].isna().sum()} item_id sem preco - usando preco medio")
        
        # Calcular quantidade de ovos por caixa para cada item_id
        # Necessario para converter quantidade (em ovos) para caixas nos calculos financeiros
        from extrair_compatibilidade_embalagem import calcular_qtd_embalagem
        df_base['qtd_ovos_por_caixa'] = df_base['embalagem'].apply(calcular_qtd_embalagem)
        
        # Validar que todos tem qtd_ovos_por_caixa calculada
        if df_base['qtd_ovos_por_caixa'].isna().any():
            sem_qtd = df_base[df_base['qtd_ovos_por_caixa'].isna()]
            self.logger.warning(f"  [AVISO] {len(sem_qtd)} item_id sem quantidade de ovos por caixa calculada")
            self.logger.warning("  Removendo esses item_id da otimizacao")
            df_base = df_base[df_base['qtd_ovos_por_caixa'].notna()].copy()
        
        # Calcular margem unitaria (em R$/CAIXA)
        df_base['margem_unitaria'] = df_base['preco'] - df_base['custo_ytd']
        
        # Filtrar combinacoes validas (margem positiva e preco valido)
        df_base = df_base[
            (df_base['margem_unitaria'] > 0) &
            (df_base['preco'] > 0) &
            (df_base['custo_ytd'] > 0)
        ]
        
        #  Adicionar producao disponivel por classe
        # A producao e por classe, nao por item_id
        # Todos os item_id da mesma classe compartilham a mesma producao total
        df_base = df_base.merge(df_producao, on='classe', how='left')
        df_base['producao_total'] = df_base['producao_total'].fillna(0)
        
        # Adicionar pedidos por SKU (codigo do item, sem embalagem)
        # IMPORTANTE: Pedidos sao por SKU (codigo), nao por item_id (codigo + embalagem)
        df_base = df_base.merge(
            df_pedidos_sku,
            on='item',
            how='left'
        )
        df_base['quantidade_total_pedida'] = df_base['quantidade_total_pedida'].fillna(0)
        
        # Ler flags de configuracao
        usar_apenas_excedente = self.config.get('modelo', {}).get('usar_apenas_excedente', True)
        atender_pedidos = self.config.get('modelo', {}).get('atender_pedidos', True)
        
        # Validacao e ajuste de flags
        # Se atender_pedidos = True, SEMPRE usa excedente (nao pode "roubar" dos pedidos)
        # A flag usar_apenas_excedente so faz diferenca quando atender_pedidos = False
        if atender_pedidos:
            # Quando atende pedidos, excedente = estoque - pedidos (ja calculado acima)
            # Nao faz sentido usar estoque total, pois isso permitiria "roubar" dos pedidos
            usar_apenas_excedente = True
            self.logger.info("\n[INFO] Como atender_pedidos=true, usando apenas excedente (estoque - pedidos)")
        elif not atender_pedidos and usar_apenas_excedente:
            # Se nao atender pedidos e usar_apenas_excedente=true, nao faz sentido
            # (excedente = estoque - pedidos, se nao ha pedidos, excedente = estoque total)
            self.logger.warning("\n[AVISO] Combinacao invalida detectada:")
            self.logger.warning("  usar_apenas_excedente=true + atender_pedidos=false")
            self.logger.warning("  Se nao ha pedidos, excedente = estoque total.")
            self.logger.warning("  Ajustando para usar_apenas_excedente=false automaticamente.")
            usar_apenas_excedente = False
        
        #  Calcular producao disponivel para otimizacao por classe
        # A producao e por classe, e precisa ser distribuida entre os item_id da classe
        
        # Calcular pedidos totais por classe (soma de todos os pedidos dos SKUs da classe)
        # CORRECAO [C]: Calcular corretamente somando pedidos por SKU, depois agrupando por classe
        # Evita duplicacao quando um SKU tem multiplas embalagens
        if len(df_pedidos_sku) > 0:
            # Fazer merge de pedidos com classes para obter classe de cada SKU
            pedidos_com_classe = df_pedidos_sku.merge(df_classes[['item', 'classe']], on='item', how='left')
            # Filtrar apenas classes que tem producao
            pedidos_com_classe = pedidos_com_classe[pedidos_com_classe['classe'].isin(df_producao['classe'])]
            # Agrupar por classe e somar pedidos (cada SKU conta apenas uma vez)
            pedidos_por_classe = pedidos_com_classe.groupby('classe')['quantidade_total_pedida'].sum()
        else:
            pedidos_por_classe = pd.Series(0, index=df_producao['classe'])
        
        # Calcular producao excedente por classe (apos atender pedidos)
        # IMPORTANTE: Pedidos sao por SKU, mas a producao e por classe
        # Se atender pedidos, excedente = producao - pedidos (limitado a producao)
        if atender_pedidos:
            producao_excedente_por_classe = df_producao.set_index('classe')['producao_total'] - pedidos_por_classe
            producao_excedente_por_classe = producao_excedente_por_classe.clip(lower=0)
        else:
            # Se nao atender pedidos, toda producao esta disponivel
            producao_excedente_por_classe = df_producao.set_index('classe')['producao_total']
        
        # Se usar_apenas_excedente = False e atender_pedidos = False, usar producao total
        if not atender_pedidos and not usar_apenas_excedente:
            producao_disponivel_otimizacao = df_producao.set_index('classe')['producao_total']
        else:
            producao_disponivel_otimizacao = producao_excedente_por_classe
        
        # Adicionar producao disponivel para otimizacao por classe
        df_base['producao_disponivel_otimizacao_classe'] = df_base['classe'].map(producao_disponivel_otimizacao).fillna(0)
        
        # Manter compatibilidade com codigo antigo (renomear para estoque)
        df_base['estoque_disponivel_otimizacao_classe'] = df_base['producao_disponivel_otimizacao_classe']
        df_base['estoque_excedente_classe'] = df_base['producao_disponivel_otimizacao_classe']
        df_base['estoque_classe'] = df_base['producao_total']
        
        # Para compatibilidade: criar estoque_excedente_sku (nao usado na nova logica, mas mantido para logs)
        df_base['estoque_excedente_sku'] = 0  # Será calculado dinamicamente se necessário
        
        self.logger.info(f"  Item_id validos: {len(df_base)}")
        self.logger.info(f"  SKUs validos (codigo): {df_base['item'].nunique()}")
        self.logger.info(f"  Classes validas: {df_base['classe'].nunique()}")
        self.logger.info(f"  Margem unitaria media: R$ {df_base['margem_unitaria'].mean():.2f}")
        
        # Estatisticas de producao e pedidos
        total_producao = df_producao['producao_total'].sum()
        self.logger.info(f"\n  PRODUCAO vs PEDIDOS:")
        self.logger.info(f"    Producao total: {total_producao:,.0f} unidades")
        
        if len(df_pedidos_sku) > 0:
            total_pedido = df_pedidos_sku['quantidade_total_pedida'].sum()
            total_excedente = producao_excedente_por_classe.sum() if atender_pedidos else total_producao
            
            self.logger.info(f"    Pedidos totais: {total_pedido:,.0f} unidades")
            self.logger.info(f"    Producao excedente (apos pedidos): {total_excedente:,.0f} unidades")
            if total_producao > 0:
                self.logger.info(f"    Percentual excedente: {total_excedente/total_producao*100:.1f}%")
        
        # Mostrar top classes por potencial de ganho
        potencial_classe = df_base.groupby('classe').agg({
            'item_id': 'count',  # Numero de item_id (SKU + embalagem)
            'item': 'nunique',  # Numero de SKUs unicos (codigo)
            'producao_disponivel_otimizacao_classe': 'first',
            'margem_unitaria': ['min', 'max', 'mean']
        })
        potencial_classe.columns = ['num_item_id', 'num_skus', 'producao_disponivel', 'margem_min', 'margem_max', 'margem_media']
        potencial_classe['diff_margem'] = potencial_classe['margem_max'] - potencial_classe['margem_min']
        # Converter producao de ovos para caixas antes de calcular potencial
        # diff_margem esta em R$/CAIXA, producao_disponivel esta em OVOS
        # Calcular quantidade media de ovos por caixa por classe
        for classe in potencial_classe.index:
            item_ids_classe = df_base[df_base['classe'] == classe]
            if len(item_ids_classe) > 0:
                qtd_ovos_por_caixa_media = item_ids_classe['qtd_ovos_por_caixa'].mean()
                producao_caixas = potencial_classe.loc[classe, 'producao_disponivel'] / qtd_ovos_por_caixa_media
                potencial_classe.loc[classe, 'potencial_ganho'] = potencial_classe.loc[classe, 'diff_margem'] * producao_caixas * 0.03
            else:
                potencial_classe.loc[classe, 'potencial_ganho'] = 0
        potencial_classe = potencial_classe.sort_values('potencial_ganho', ascending=False)
        
        self.logger.info(f"\n  Classes com maior potencial de ganho:")
        for classe, row in potencial_classe.head(5).iterrows():
            if row['num_skus'] >= 2 and row['producao_disponivel'] > 0:
                self.logger.info(f"    {classe}: {row['num_skus']} SKUs, "
                               f"{row['num_item_id']} item_id, "
                               f"producao {row['producao_disponivel']:,.0f} un, "
                               f"diff margem R$ {row['diff_margem']:.2f}, "
                               f"potencial R$ {row['potencial_ganho']:,.0f}")
        
        # Log do modo de operacao
        modo_operacao = []
        if atender_pedidos:
            modo_operacao.append("ATENDE PEDIDOS")
        else:
            modo_operacao.append("IGNORA PEDIDOS")
        
        if usar_apenas_excedente:
            modo_operacao.append("OTIMIZA APENAS EXCEDENTE")
        else:
            modo_operacao.append("OTIMIZA TODO ESTOQUE")
        
        self.logger.info(f"\n  MODO DE OPERACAO: {' + '.join(modo_operacao)}")
        
        self.dados['base_otimizacao'] = df_base
        self.dados['producao_por_classe'] = df_producao.set_index('classe')['producao_total']
        self.dados['producao_excedente_por_classe'] = producao_excedente_por_classe
        # Manter compatibilidade com codigo antigo
        self.dados['estoque_por_classe'] = self.dados['producao_por_classe']
        self.dados['estoque_excedente_por_classe'] = self.dados['producao_excedente_por_classe']
        self.dados['usar_apenas_excedente'] = usar_apenas_excedente
        self.dados['atender_pedidos'] = atender_pedidos
    
    def criar_modelo(self):
        """Cria o modelo de otimizacao com realocacao."""
        self.logger.info("\n" + "="*80)
        self.logger.info("ETAPA 2: CRIACAO DO MODELO COM REALOCACAO")
        self.logger.info("="*80)
        
        # Criar solver
        solver_type = getattr(pywraplp.Solver, self.config['solver']['solver_type'])
        self.solver = pywraplp.Solver('MixDiarioComRealocacao', solver_type)
        
        df_base = self.dados['base_otimizacao']
        
        # Criar variaveis: 
        # 1. y[item] = quantidade atendida do pedido (pode ser parcial)
        # 2. x[item, embalagem] = quantidade alocada no excedente (para otimizacao)
        self.logger.info("\n[1/4] Criando variaveis de decisao...")
        
        # Variaveis de atendimento aos pedidos (apenas se atender_pedidos = True)
        self.variaveis_pedidos = {}
        atender_pedidos = self.dados.get('atender_pedidos', True)
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        
        if atender_pedidos and len(df_pedidos_sku) > 0:
            for _, row in df_pedidos_sku.iterrows():
                item = int(row['item'])
                qtd_pedida = float(row['quantidade_total_pedida'])
                
                #  Calcular estoque disponivel do SKU (soma de todos os item_id do mesmo SKU)
                # A producao e por classe, mas precisamos saber quanto do SKU esta disponivel
                # Por enquanto, vamos usar a producao da classe como limite (sera ajustado nas restricoes)
                item_ids_do_sku = df_base[df_base['item'] == item]
                if len(item_ids_do_sku) > 0:
                    # Usar producao da classe como limite superior (sera ajustado nas restricoes)
                    producao_classe = float(item_ids_do_sku['producao_disponivel_otimizacao_classe'].iloc[0])
                    limite_atendimento = min(qtd_pedida, producao_classe)
                else:
                    limite_atendimento = 0.0
                
                if limite_atendimento > 0:
                    self.variaveis_pedidos[item] = self.solver.NumVar(
                        0.0,
                        float(limite_atendimento),
                        f"y_pedido_{item}"
                    )
        
        if atender_pedidos:
            self.logger.info(f"  Variaveis de atendimento aos pedidos: {len(self.variaveis_pedidos)}")
        else:
            self.logger.info(f"  Variaveis de atendimento aos pedidos: 0 (pedidos ignorados)")
        
        #  Variaveis de alocacao por item_id (ja inclui SKU + embalagem)
        # Limite superior depende da flag usar_apenas_excedente
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        self.variaveis = {}
        
        for idx, row in df_base.iterrows():
            item_id = row['item_id']
            var_name = f"x_{item_id}"
            
            # Limite superior: producao disponivel para otimizacao da CLASSE
            # A restricao de soma por classe garantira que nao exceda a producao total
            producao_disponivel_classe = row['producao_disponivel_otimizacao_classe']
            
            if producao_disponivel_classe > 0:
                self.variaveis[item_id] = self.solver.NumVar(
                    0,
                    producao_disponivel_classe,
                    var_name
                )
        
        modo_desc = "excedente" if usar_apenas_excedente else "todo estoque"
        self.logger.info(f"  Variaveis de alocacao (otimizacao do {modo_desc}): {len(self.variaveis)}")
        
        # Adicionar restricoes
        self._adicionar_restricoes(df_base)
        
        # Definir objetivo
        self._definir_objetivo(df_base)
        
        self.logger.info("\n[OK] Modelo criado com sucesso!")
    
    def _adicionar_restricoes(self, df_base: pd.DataFrame):
        """Adiciona restricoes ao modelo."""
        self.logger.info("\n[2/4] Adicionando restricoes...")
        
        num_restricoes = 0
        
        # RESTRICAO 1: Atendimento aos pedidos (apenas se atender_pedidos = True)
        atender_pedidos = self.dados.get('atender_pedidos', True)
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        
        if atender_pedidos and len(df_pedidos_sku) > 0:
            for _, row in df_pedidos_sku.iterrows():
                item = row['item']
                qtd_pedida = row['quantidade_total_pedida']
                
                if item in self.variaveis_pedidos:
                    #  Atendimento nao pode exceder o pedido nem a producao disponivel da classe
                    item_ids_do_sku = df_base[df_base['item'] == item]
                    if len(item_ids_do_sku) > 0:
                        producao_classe = float(item_ids_do_sku['producao_disponivel_otimizacao_classe'].iloc[0])
                        limite_atendimento = min(qtd_pedida, producao_classe)
                    else:
                        limite_atendimento = 0
                    
                    # A restricao ja esta no limite da variavel, mas vamos adicionar explicitamente
                    self.solver.Add(self.variaveis_pedidos[item] <= limite_atendimento)
                    num_restricoes += 1
        
        if atender_pedidos:
            self.logger.info(f"  Restricoes de atendimento aos pedidos: {num_restricoes}")
        else:
            self.logger.info(f"  Restricoes de atendimento aos pedidos: 0 (pedidos ignorados)")
        
        # RESTRICAO 2: Volume total por CLASSE <= producao disponivel da classe
        # Esta e a restricao que permite realocacao entre item_id da mesma classe!
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        classes = df_base['classe'].unique()
        
        num_restricoes_classe = 0
        for classe in classes:
            #  Todas as variaveis de item_id desta classe
            item_ids_classe = df_base[df_base['classe'] == classe]['item_id'].unique()
            
            if len(item_ids_classe) == 0:
                continue
            
            # Soma de todas as alocacoes da classe (por item_id)
            soma_classe = sum(
                self.variaveis.get(item_id, 0)
                for item_id in item_ids_classe
                if item_id in self.variaveis
            )
            
            # Producao disponivel para otimizacao da classe
            producao_disponivel_classe = df_base[df_base['classe'] == classe]['producao_disponivel_otimizacao_classe'].iloc[0] if len(df_base[df_base['classe'] == classe]) > 0 else 0
            
            if producao_disponivel_classe > 0:
                self.solver.Add(soma_classe <= producao_disponivel_classe)
                num_restricoes_classe += 1
        
        num_restricoes += num_restricoes_classe
        modo_desc = "EXCEDENTE" if usar_apenas_excedente else "TOTAL"
        self.logger.info(f"  Restricoes de estoque {modo_desc} por CLASSE: {num_restricoes_classe}")
        
        # RESTRICAO 3: Para cada item_id, limite de alocacao baseado em demanda historica
        #  Como cada item_id ja e unico (SKU + embalagem), nao precisamos mais
        # de limite de realocacao por SKU. A demanda historica e aplicada diretamente ao item_id.
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        
        # Ler configuracao de demanda historica
        considerar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        df_demanda = self.dados.get('demanda_historica', pd.DataFrame(columns=['item', 'demanda_max'])) if considerar_demanda else pd.DataFrame(columns=['item', 'demanda_max'])
        
        num_restricoes_item_id = 0
        num_restricoes_demanda = 0
        
        #  Aplicar restricao de demanda historica por item_id
        # Se nao houver demanda historica, o limite e a producao da classe (ja na restricao 2)
        if considerar_demanda and len(df_demanda) > 0:
            # Mapear demanda por item (codigo SKU) para item_id
            for _, row in df_base.iterrows():
                item_id = row['item_id']
                item = row['item']
                
                if item_id not in self.variaveis:
                    continue
                
                # Buscar demanda historica do SKU (codigo)
                demanda_sku = df_demanda[df_demanda['item'] == item]['demanda_max']
                
                if len(demanda_sku) > 0:
                    limite_demanda = float(demanda_sku.iloc[0])
                    # Aplicar restricao: alocacao do item_id nao pode exceder demanda historica
                    self.solver.Add(self.variaveis[item_id] <= limite_demanda)
                    num_restricoes_demanda += 1
                    num_restricoes_item_id += 1
        
        num_restricoes += num_restricoes_item_id
        modo_desc = "excedente" if usar_apenas_excedente else "total"
        considerar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        demanda_desc = " (demanda historica)" if considerar_demanda else ""
        self.logger.info(f"  Restricoes de limite por item_id{demanda_desc}: {num_restricoes_item_id}")
        if considerar_demanda and num_restricoes_demanda > 0:
            self.logger.info(f"    - Restricoes com demanda historica aplicada: {num_restricoes_demanda}")
        
        # RESTRICAO 4: Forcar alocacao minima (especialmente importante para minimizar_custos)
        # Se o objetivo e minimizar custos, precisamos forcar alocacao para evitar solucao trivial (zero)
        # IMPORTANTE: Esta restricao deve ser flexivel para nao conflitar com demanda historica
        # ESTRATEGIA: Em vez de forcar percentual fixo, vamos adicionar um "penalty" na funcao objetivo
        # que desencoraja alocacoes zero. Isso sera feito na funcao _definir_objetivo.
        # Por enquanto, apenas logamos que a restricao seria necessaria
        tipo_objetivo = self.config.get('modelo', {}).get('tipo_objetivo', 'maximizar_margem')
        escoar_todo_estoque = self.config.get('modelo', {}).get('escoar_todo_estoque', False)
        
        num_restricoes_escoamento = 0
        # NOTA: Restricao de escoamento minimo removida temporariamente devido a conflitos
        # com restricoes de demanda historica. A funcao objetivo sera ajustada para desencorajar zeros.
        if False:  # Desabilitado temporariamente
            if tipo_objetivo == 'minimizar_custos' or escoar_todo_estoque:
                # Calcular estoque total disponivel para otimizacao
                estoque_total_disponivel = df_base['estoque_disponivel_otimizacao_classe'].sum()
                
                if estoque_total_disponivel > 0:
                    # Soma total de todas as alocacoes (todas as classes)
                    soma_total = sum(
                        self.variaveis.get((row['item'], row['embalagem']), 0)
                        for _, row in df_base.iterrows()
                        if (row['item'], row['embalagem']) in self.variaveis
                    )
                    
                    # Forcar alocacao de pelo menos 80% do estoque total (mais flexivel que 95% por classe)
                    # Isso evita conflitos com restricoes de demanda historica
                    percentual_minimo = 0.95 if escoar_todo_estoque else 0.80
                    self.solver.Add(soma_total >= estoque_total_disponivel * percentual_minimo)
                    num_restricoes_escoamento = 1
        
        if num_restricoes_escoamento > 0:
            num_restricoes += num_restricoes_escoamento
            motivo = "minimizar_custos" if tipo_objetivo == 'minimizar_custos' else "escoar_todo_estoque"
            percentual = "95%" if escoar_todo_estoque else "80%"
            self.logger.info(f"  Restricoes de escoamento minimo ({percentual} do estoque total) - motivo: {motivo}: {num_restricoes_escoamento}")
        
        self.logger.info(f"  Total de restricoes: {num_restricoes}")
    
    def _definir_objetivo(self, df_base: pd.DataFrame):
        """Define funcao objetivo: maximizar margem ou minimizar custos (pedidos + excedente)."""
        self.logger.info("\n[3/4] Definindo funcao objetivo...")
        
        # Determinar tipo de objetivo
        tipo_objetivo = self.config.get('modelo', {}).get('tipo_objetivo', 'maximizar_margem')
        if tipo_objetivo != 'maximizar_margem':
            tipo_objetivo = 'minimizar_custos'
        
        objetivo_desc = "MAXIMIZAR MARGEM" if tipo_objetivo == 'maximizar_margem' else "MINIMIZAR CUSTOS"
        self.logger.info(f"  Tipo de objetivo: {objetivo_desc}")
        
        # Objetivo tem duas partes:
        # 1. Pedidos atendidos
        # 2. Otimizacao no excedente
        
        objetivo_pedidos = 0.0
        atender_pedidos = self.dados.get('atender_pedidos', True)
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        
        if atender_pedidos and len(df_pedidos_sku) > 0:
            for _, row in df_pedidos_sku.iterrows():
                item = row['item']
                if item in self.variaveis_pedidos:
                    item_ids_do_sku = df_base[df_base['item'] == item]
                    if len(item_ids_do_sku) > 0:
                        # Converter quantidade de ovos para caixas
                        qtd_ovos_por_caixa_item = item_ids_do_sku['qtd_ovos_por_caixa'].iloc[0]
                        qtd_caixas_pedido = self.variaveis_pedidos[item] / qtd_ovos_por_caixa_item
                        
                        if tipo_objetivo == 'maximizar_margem':
                            # Buscar margem unitaria do item (usar primeira embalagem disponivel)
                            margem_item = item_ids_do_sku['margem_unitaria'].iloc[0]
                            objetivo_pedidos += margem_item * qtd_caixas_pedido
                        else:  # minimizar_custos
                            # Buscar custo unitario do item
                            custo_item = item_ids_do_sku['custo_ytd'].iloc[0]
                            objetivo_pedidos += custo_item * qtd_caixas_pedido
        
        #  Objetivo da otimizacao no excedente (usando item_id)
        # IMPORTANTE: Converter quantidade de ovos para caixas antes de multiplicar pela margem
        # margem_unitaria esta em R$/CAIXA, variaveis estao em OVOS
        if tipo_objetivo == 'maximizar_margem':
            objetivo_excedente = sum(
                row['margem_unitaria'] * (self.variaveis.get(row['item_id'], 0) / row['qtd_ovos_por_caixa'])
                for _, row in df_base.iterrows()
                if row['item_id'] in self.variaveis
            )
        else:  # minimizar_custos
            # Para minimizar custos, adicionar um termo que desencoraja alocacoes zero
            # Usamos um peso muito pequeno (negativo) para "recompensar" alocacoes
            # Isso evita solucao trivial (zero) sem criar conflitos de restricoes
            # IMPORTANTE: Converter quantidade de ovos para caixas antes de multiplicar pelo custo
            # custo_ytd esta em R$/CAIXA, variaveis estao em OVOS
            custo_total = sum(
                row['custo_ytd'] * (self.variaveis.get(row['item_id'], 0) / row['qtd_ovos_por_caixa'])
                for _, row in df_base.iterrows()
                if row['item_id'] in self.variaveis
            )
            
            # Termo de "recompensa" por alocacao (peso muito pequeno para nao interferir na minimizacao de custos)
            # Usamos um valor negativo pequeno multiplicado pela quantidade total alocada (em caixas)
            # Isso faz com que o modelo prefira alocar algo em vez de zero
            quantidade_total = sum(
                (self.variaveis.get(row['item_id'], 0) / row['qtd_ovos_por_caixa'])
                for _, row in df_base.iterrows()
                if row['item_id'] in self.variaveis
            )
            
            # Peso: -200.0 por unidade alocada (maior que custo medio para forcar alocacao)
            # Custo medio: R$ 156.51 por unidade
            # Para que alocar seja melhor que nao alocar: custo - peso * qtd < 0
            # Para 1 unidade: 156.51 - 200.0 * 1 = -43.49 < 0 (melhor que zero!)
            # O peso e maior que o custo medio, mas nao muito maior, entao ainda prioriza SKUs com menor custo
            # Exemplo: SKU A (custo 100) vs SKU B (custo 200)
            #   A: 100 - 200 = -100
            #   B: 200 - 200 = 0
            #   Ainda prefere A (menor custo)
            peso_recompensa = -200.0
            objetivo_excedente = custo_total + (peso_recompensa * quantidade_total)
        
        objetivo_total = objetivo_pedidos + objetivo_excedente
        
        # Aplicar objetivo ao solver
        if tipo_objetivo == 'maximizar_margem':
            self.solver.Maximize(objetivo_total)
        else:
            self.solver.Minimize(objetivo_total)
        
        # Calcular metricas potenciais (margem E custos para comparacao)
        atender_pedidos = self.dados.get('atender_pedidos', True)
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        
        #  Metricas de pedidos (usando producao da classe)
        margem_potencial_pedidos = 0.0
        custo_potencial_pedidos = 0.0
        if atender_pedidos and len(df_pedidos_sku) > 0:
            for _, row in df_pedidos_sku.iterrows():
                item = row['item']
                qtd_pedida = row['quantidade_total_pedida']
                item_ids_do_sku = df_base[df_base['item'] == item]
                if len(item_ids_do_sku) > 0:
                    producao_classe = float(item_ids_do_sku['producao_disponivel_otimizacao_classe'].iloc[0])
                    qtd_atendivel = min(qtd_pedida, producao_classe)
                    
                    margem_item = item_ids_do_sku['margem_unitaria'].iloc[0] if len(item_ids_do_sku) > 0 else 0
                    custo_item = item_ids_do_sku['custo_ytd'].iloc[0] if len(item_ids_do_sku) > 0 else 0
                    # Converter quantidade de ovos para caixas
                    qtd_ovos_por_caixa_item = item_ids_do_sku['qtd_ovos_por_caixa'].iloc[0] if len(item_ids_do_sku) > 0 else 360
                    qtd_caixas_atendivel = qtd_atendivel / qtd_ovos_por_caixa_item
                    
                    margem_potencial_pedidos += margem_item * qtd_caixas_atendivel
                    custo_potencial_pedidos += custo_item * qtd_caixas_atendivel
        
        #  Calcular metricas da otimizacao (usando producao disponivel)
        # A producao e por classe, entao usamos a producao disponivel para otimizacao
        modo_desc = "excedente" if usar_apenas_excedente else "total"
        producao_otimizacao = df_base['producao_disponivel_otimizacao_classe']
        
        # Calcular margem e custo potencial (usando media por item_id da classe)
        # Como a producao e por classe, vamos usar a margem/custo medio dos item_id da classe
        margem_potencial_otimizacao = 0.0
        custo_potencial_otimizacao = 0.0
        for classe in df_base['classe'].unique():
            item_ids_classe = df_base[df_base['classe'] == classe]
            if len(item_ids_classe) > 0:
                producao_classe = item_ids_classe['producao_disponivel_otimizacao_classe'].iloc[0]
                margem_media_classe = item_ids_classe['margem_unitaria'].mean()
                custo_medio_classe = item_ids_classe['custo_ytd'].mean()
                # Converter producao de ovos para caixas
                qtd_ovos_por_caixa_media_classe = item_ids_classe['qtd_ovos_por_caixa'].mean()
                qtd_caixas_producao_classe = producao_classe / qtd_ovos_por_caixa_media_classe
                margem_potencial_otimizacao += margem_media_classe * qtd_caixas_producao_classe
                custo_potencial_otimizacao += custo_medio_classe * qtd_caixas_producao_classe
        
        margem_potencial_total = margem_potencial_pedidos + margem_potencial_otimizacao
        custo_potencial_total = custo_potencial_pedidos + custo_potencial_otimizacao
        
        # Logs
        if atender_pedidos:
            self.logger.info(f"  Margem potencial pedidos: R$ {margem_potencial_pedidos:,.2f}")
            self.logger.info(f"  Custo potencial pedidos: R$ {custo_potencial_pedidos:,.2f}")
        
        self.logger.info(f"  Margem potencial {modo_desc} (sem realocacao): R$ {margem_potencial_otimizacao:,.2f}")
        self.logger.info(f"  Custo potencial {modo_desc} (sem realocacao): R$ {custo_potencial_otimizacao:,.2f}")
        self.logger.info(f"  Margem potencial total: R$ {margem_potencial_total:,.2f}")
        self.logger.info(f"  Custo potencial total: R$ {custo_potencial_total:,.2f}")
    
    def resolver(self):
        """Resolve o modelo de otimizacao."""
        self.logger.info("\n" + "="*80)
        self.logger.info("ETAPA 3: RESOLUCAO DO MODELO")
        self.logger.info("="*80)
        
        # Configurar tempo limite
        self.solver.SetTimeLimit(self.config['solver']['time_limit_ms'])
        
        # Resolver
        status = self.solver.Solve()
        
        if status == pywraplp.Solver.OPTIMAL:
            self.logger.info("\n[OK] Solucao otima encontrada!")
            self._extrair_resultado()
            return True
        elif status == pywraplp.Solver.FEASIBLE:
            self.logger.warning("\n[AVISO] Solucao viavel (nao otima) encontrada")
            self._extrair_resultado()
            return True
        else:
            self.logger.error("\n[ERRO] Nenhuma solucao encontrada")
            return False
    
    def _extrair_resultado(self):
        """Extrai resultado da otimizacao."""
        df_base = self.dados['base_otimizacao']
        df_demanda = self.dados.get('demanda_historica', pd.DataFrame(columns=['item', 'demanda_max']))
        demanda_por_item = df_demanda.set_index('item')['demanda_max'].to_dict() if len(df_demanda) > 0 else {}
        considerar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        
        # Determinar tipo baseado no modo de operacao
        atender_pedidos = self.dados.get('atender_pedidos', True)
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        
        # Tipo de alocacao:
        # - Se atender_pedidos=true: tipo = 'EXCEDENTE' (otimiza apenas o que sobrou apos pedidos)
        # - Se atender_pedidos=false e usar_apenas_excedente=false: tipo = 'ESTOQUE_TOTAL' (otimiza todo estoque)
        # - Se atender_pedidos=false e usar_apenas_excedente=true: tipo = 'EXCEDENTE' (mas nao faz sentido, ja ajustado)
        if atender_pedidos:
            tipo_alocacao = 'EXCEDENTE'
        else:
            tipo_alocacao = 'ESTOQUE_TOTAL' if not usar_apenas_excedente else 'EXCEDENTE'
        
        #  Resultados da otimizacao (usando item_id)
        resultados = []
        for item_id, var in self.variaveis.items():
            qtd = var.solution_value()
            if qtd > 0.01:
                # Buscar dados deste item_id
                row_base = df_base[df_base['item_id'] == item_id]
                if len(row_base) > 0:
                    row = row_base.iloc[0]
                    demanda_max = demanda_por_item.get(row['item'])
                    limite_classe = row['producao_disponivel_otimizacao_classe']
                    restricao_quantidade = limite_classe
                    restricao_tipo = 'PRODUCAO_CLASSE'
                    if considerar_demanda and pd.notna(demanda_max):
                        restricao_quantidade = min(limite_classe, float(demanda_max))
                        if restricao_quantidade < limite_classe:
                            restricao_tipo = 'DEMANDA_HISTORICA'
                    # Converter quantidade de ovos para caixas para calculos financeiros
                    # qtd esta em OVOS, preco/custo/margem estao em R$/CAIXA
                    qtd_caixas = qtd / row['qtd_ovos_por_caixa']
                    
                    resultados.append({
                        'item_id': item_id,
                        'item': row['item'],
                        'embalagem': row['embalagem'],
                        'classe': row['classe'],
                        'quantidade': qtd,  # Manter em ovos para referencia
                        'quantidade_caixas': qtd_caixas,  # Adicionar coluna em caixas
                        'tipo': tipo_alocacao,
                        'tipo_restricao': restricao_tipo,
                        'quantidade_restricao': restricao_quantidade,
                        'producao_total': row['producao_total'],  # Producao da classe
                        'producao_disponivel': row['producao_disponivel_otimizacao_classe'],  # Producao disponivel para otimizacao
                        'preco': row['preco'],
                        'custo_ytd': row['custo_ytd'],
                        'margem_unitaria': row['margem_unitaria'],
                        'receita_total': qtd_caixas * row['preco'],  # CAIXAS × R$/CAIXA
                        'custo_total': qtd_caixas * row['custo_ytd'],  # CAIXAS × R$/CAIXA
                        'margem_total': qtd_caixas * row['margem_unitaria']  # CAIXAS × R$/CAIXA
                    })
        
        # Resultados dos pedidos atendidos
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        if len(df_pedidos_sku) > 0:
            for _, row_pedido in df_pedidos_sku.iterrows():
                item = row_pedido['item']
                if item in self.variaveis_pedidos:
                    qtd_atendida = self.variaveis_pedidos[item].solution_value()
                    if qtd_atendida > 0.01:
                        # Buscar dados do item
                        row_base = df_base[df_base['item'] == item]
                        if len(row_base) > 0:
                            row = row_base.iloc[0]
                            producao_disponivel_classe = float(row['producao_disponivel_otimizacao_classe'])
                            limite_pedido = row_pedido['quantidade_total_pedida']
                            quantidade_restricao = min(limite_pedido, producao_disponivel_classe)
                            tipo_restricao = 'PEDIDO' if limite_pedido <= producao_disponivel_classe else 'PRODUCAO_CLASSE'
                            # Converter quantidade de ovos para caixas para calculos financeiros
                            # qtd_atendida esta em OVOS, preco/custo/margem estao em R$/CAIXA
                            # Usar primeira embalagem disponivel do item para conversao
                            qtd_ovos_por_caixa_pedido = row['qtd_ovos_por_caixa'] if 'qtd_ovos_por_caixa' in row else df_base[df_base['item'] == item]['qtd_ovos_por_caixa'].iloc[0] if len(df_base[df_base['item'] == item]) > 0 else 360
                            qtd_caixas_pedido = qtd_atendida / qtd_ovos_por_caixa_pedido
                            
                            resultados.append({
                                'item': item,
                                'embalagem': 'PEDIDO',  # Pedidos nao especificam embalagem
                                'classe': row['classe'],
                                'quantidade': qtd_atendida,  # Manter em ovos
                                'quantidade_caixas': qtd_caixas_pedido,  # Adicionar coluna em caixas
                                'tipo': 'PEDIDO',
                                'tipo_restricao': tipo_restricao,
                                'quantidade_restricao': quantidade_restricao,
                                'producao_total': row['producao_total'],  # Producao da classe
                                'quantidade_pedida': row_pedido['quantidade_total_pedida'],
                                'percentual_atendido': (qtd_atendida / row_pedido['quantidade_total_pedida'] * 100) if row_pedido['quantidade_total_pedida'] > 0 else 0,
                                'preco': row['preco'],
                                'custo_ytd': row['custo_ytd'],
                                'margem_unitaria': row['margem_unitaria'],
                                'receita_total': qtd_caixas_pedido * row['preco'],  # CAIXAS × R$/CAIXA
                                'custo_total': qtd_caixas_pedido * row['custo_ytd'],  # CAIXAS × R$/CAIXA
                                'margem_total': qtd_caixas_pedido * row['margem_unitaria']  # CAIXAS × R$/CAIXA
                            })
        
        self.resultado = pd.DataFrame(resultados)
        
        # Garantir que coluna 'classe' existe mesmo se resultado estiver vazio
        # (necessario para evitar erro no groupby('classe') em salvar_resultados)
        if len(self.resultado) == 0:
            self.resultado = pd.DataFrame(columns=['item_id', 'item', 'embalagem', 'classe', 'quantidade', 'quantidade_caixas', 'tipo', 
                                                   'tipo_restricao', 'quantidade_restricao',
                                                   'producao_total', 'producao_disponivel', 'preco', 
                                                   'custo_ytd', 'margem_unitaria', 'receita_total', 
                                                   'custo_total', 'margem_total'])
        
        if len(self.resultado) > 0:
            # Nota: Removidas colunas variacao_qtd e variacao_pct
            # No novo formato (producao por classe), nao temos baseline por item_id
            # para calcular variacao. A quantidade alocada ja e suficiente.
            
            self.logger.info("\nRESULTADOS:")
            
            # Separar pedidos e otimizacao
            if 'tipo' in self.resultado.columns:
                df_pedidos = self.resultado[self.resultado['tipo'] == 'PEDIDO']
                df_otimizacao = self.resultado[self.resultado['tipo'].isin(['EXCEDENTE', 'ESTOQUE_TOTAL'])]
                
                if len(df_pedidos) > 0:
                    self.logger.info(f"\n  PEDIDOS ATENDIDOS:")
                    self.logger.info(f"    SKUs atendidos: {len(df_pedidos)}")
                    self.logger.info(f"    Quantidade atendida: {df_pedidos['quantidade'].sum():,.0f} unidades")
                    self.logger.info(f"    Margem dos pedidos: R$ {df_pedidos['margem_total'].sum():,.2f}")
                    if 'percentual_atendido' in df_pedidos.columns:
                        self.logger.info(f"    Percentual medio atendido: {df_pedidos['percentual_atendido'].mean():.1f}%")
                
                if len(df_otimizacao) > 0:
                    tipo_desc = df_otimizacao['tipo'].iloc[0]
                    if tipo_desc == 'EXCEDENTE':
                        self.logger.info(f"\n  OTIMIZACAO NO EXCEDENTE:")
                    else:
                        self.logger.info(f"\n  OTIMIZACAO DO ESTOQUE TOTAL:")
                    self.logger.info(f"    Combinacoes escolhidas: {len(df_otimizacao)}")
                    self.logger.info(f"    Quantidade alocada: {df_otimizacao['quantidade'].sum():,.0f} unidades")
                    self.logger.info(f"    Margem: R$ {df_otimizacao['margem_total'].sum():,.2f}")
            else:
                self.logger.info(f"  Combinacoes escolhidas: {len(self.resultado)}")
            
            self.logger.info(f"\n  TOTAIS:")
            self.logger.info(f"    Quantidade total alocada: {self.resultado['quantidade'].sum():,.0f} unidades")
            self.logger.info(f"    Receita total: R$ {self.resultado['receita_total'].sum():,.2f}")
            self.logger.info(f"    Custo total: R$ {self.resultado['custo_total'].sum():,.2f}")
            self.logger.info(f"    Margem total: R$ {self.resultado['margem_total'].sum():,.2f}")
            if self.resultado['receita_total'].sum() > 0:
                self.logger.info(f"    Margem %: {self.resultado['margem_total'].sum() / self.resultado['receita_total'].sum() * 100:.2f}%")
            
            
    
    def calcular_comparativo(self):
        """Calcula margem e custos baseline vs otimizados."""
        if self.resultado is None or len(self.resultado) == 0:
            return None
        
        df_producao = self.dados['producao']  #  Producao por classe
        df_base = self.dados['base_otimizacao']
        
        # Determinar tipo de objetivo para exibir metricas corretas
        tipo_objetivo = self.config.get('modelo', {}).get('tipo_objetivo', 'maximizar_margem')
        if tipo_objetivo != 'maximizar_margem':
            tipo_objetivo = 'minimizar_custos'
        
        # Metricas otimizadas
        margem_otimizada = self.resultado['margem_total'].sum()
        custo_otimizado = self.resultado['custo_total'].sum()
        
        #  Metricas baseline: para cada classe, usar a margem/custo medio dos item_id
        # O baseline assume distribuicao uniforme da producao entre todos os item_id da classe
        df_producao = self.dados['producao']
        margem_baseline = 0.0
        custo_baseline = 0.0
        
        for _, row in df_producao.iterrows():
            classe = row['classe']
            qtd_producao = row['producao_total']
            
            # Buscar todos os item_id desta classe
            item_ids_classe = df_base[df_base['classe'] == classe]
            
            if len(item_ids_classe) == 0:
                continue
            
            # Usar media das margens e custos dos item_id disponiveis como baseline
            # Baseline assume distribuicao uniforme (nao otimizada)
            # IMPORTANTE: Converter producao de ovos para caixas antes de multiplicar
            # qtd_producao esta em OVOS, margem_media/custo_medio estao em R$/CAIXA
            margem_media = item_ids_classe['margem_unitaria'].mean()
            custo_medio = item_ids_classe['custo_ytd'].mean()
            
            # Calcular quantidade media de ovos por caixa para a classe (media ponderada)
            qtd_ovos_por_caixa_media = item_ids_classe['qtd_ovos_por_caixa'].mean()
            qtd_caixas_producao = qtd_producao / qtd_ovos_por_caixa_media
            
            margem_baseline += qtd_caixas_producao * margem_media
            custo_baseline += qtd_caixas_producao * custo_medio
        
        # Calcular ganhos/reducoes
        ganho_margem = margem_otimizada - margem_baseline
        ganho_margem_pct = (ganho_margem / margem_baseline * 100) if margem_baseline > 0 else 0
        
        reducao_custo = custo_baseline - custo_otimizado
        reducao_custo_pct = (reducao_custo / custo_baseline * 100) if custo_baseline > 0 else 0
        
        self.logger.info("\n" + "="*80)
        self.logger.info("COMPARATIVO: BASELINE vs OTIMIZADO")
        self.logger.info("="*80)
        
        # Sempre exibir margem (para comparacao)
        self.logger.info(f"  Margem Baseline (sem realocacao): R$ {margem_baseline:,.2f}")
        self.logger.info(f"  Margem Otimizada (com realocacao): R$ {margem_otimizada:,.2f}")
        self.logger.info(f"  GANHO MARGEM: R$ {ganho_margem:,.2f} ({ganho_margem_pct:.2f}%)")
        
        # Sempre exibir custos (para comparacao)
        self.logger.info(f"  Custo Baseline (sem realocacao): R$ {custo_baseline:,.2f}")
        self.logger.info(f"  Custo Otimizado (com realocacao): R$ {custo_otimizado:,.2f}")
        if tipo_objetivo == 'minimizar_custos':
            self.logger.info(f"  REDUCAO CUSTO: R$ {reducao_custo:,.2f} ({reducao_custo_pct:.2f}%)")
        else:
            self.logger.info(f"  Variacao Custo: R$ {reducao_custo:,.2f} ({reducao_custo_pct:.2f}%)")
        
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
    
    def salvar_resultados(self):
        """Salva resultados em CSV e Excel com timestamp e modo de operacao."""
        if self.resultado is None:
            return
        
        from datetime import datetime
        
        output_dir = Path('resultados')
        output_dir.mkdir(exist_ok=True)
        
        # Gerar timestamp no formato YYYYMMDD_HHMMSS
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Determinar sufixo do modo de operacao
        atender_pedidos = self.dados.get('atender_pedidos', True)
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        
        if atender_pedidos:
            modo_sufixo = 'excedente'  # Sempre usa excedente quando atende pedidos
        else:
            if usar_apenas_excedente:
                modo_sufixo = 'excedente'  # Ajustado automaticamente, mas mantem para compatibilidade
            else:
                modo_sufixo = 'completo'  # Otimiza todo estoque
        
        # Resultado detalhado com timestamp e modo
        arquivo_resultado_csv = output_dir / f'resultado_realocacao_{modo_sufixo}_{timestamp}.csv'
        arquivo_resultado_xlsx = output_dir / f'resultado_realocacao_{modo_sufixo}_{timestamp}.xlsx'
        
        
        # Remover colunas de variacao se existirem (nao fazem sentido no novo formato)
        resultado_para_salvar = self.resultado.copy()
        colunas_para_remover = ['variacao_pct', 'variacao_qtd']
        for col in colunas_para_remover:
            if col in resultado_para_salvar.columns:
                resultado_para_salvar = resultado_para_salvar.drop(columns=[col])
        
        resultado_para_salvar.to_csv(arquivo_resultado_csv, index=False, encoding='utf-8')
        
        #  Resumo por classe com timestamp e modo (usando producao)
        # Verificar quais colunas existem no resultado
        colunas_agregacao = {
            'item': 'nunique',
            'quantidade': 'sum',
            'margem_total': 'sum'
        }
        
        # Adicionar colunas de producao se existirem
        if 'producao_total' in self.resultado.columns:
            colunas_agregacao['producao_total'] = 'first'  # Producao e por classe, usar first
        if 'producao_disponivel' in self.resultado.columns:
            colunas_agregacao['producao_disponivel'] = 'first'
        
        resumo_classe = self.resultado.groupby('classe').agg(colunas_agregacao).reset_index()
        
        # Renomear colunas na ordem correta
        # Ordem apos groupby: ['classe', 'item', 'quantidade', 'margem_total', 'producao_total', 'producao_disponivel']
        renomear_colunas = {
            'item': 'num_skus',
            'quantidade': 'quantidade_alocada'
        }
        resumo_classe = resumo_classe.rename(columns=renomear_colunas)
        
        # Reordenar colunas na ordem desejada
        colunas_ordenadas = ['classe', 'num_skus', 'quantidade_alocada']
        if 'producao_total' in resumo_classe.columns:
            colunas_ordenadas.append('producao_total')
        if 'producao_disponivel' in resumo_classe.columns:
            colunas_ordenadas.append('producao_disponivel')
        colunas_ordenadas.append('margem_total')
        
        resumo_classe = resumo_classe[colunas_ordenadas]
        
        arquivo_resumo_csv = output_dir / f'resumo_por_classe_{modo_sufixo}_{timestamp}.csv'
        resumo_classe.to_csv(arquivo_resumo_csv, index=False, encoding='utf-8')
        
        # Criar Excel com multiplas abas
        with pd.ExcelWriter(arquivo_resultado_xlsx, engine='openpyxl') as writer:
            # Aba 1: Resultado detalhado 
            resultado_para_salvar.to_excel(writer, sheet_name='Resultado Detalhado', index=False)
            
            # Aba 2: Resumo por classe
            resumo_classe.to_excel(writer, sheet_name='Resumo por Classe', index=False)
            
            # Aba 3: Estatisticas e Resumo Executivo
            df_estatisticas = self._criar_aba_estatisticas(resumo_classe)
            df_estatisticas.to_excel(writer, sheet_name='Estatisticas', index=False)
        
        # Salvar resumo por classe separado (para compatibilidade)
        arquivo_resumo_xlsx = output_dir / f'resumo_por_classe_{modo_sufixo}_{timestamp}.xlsx'
        resumo_classe.to_excel(arquivo_resumo_xlsx, index=False, engine='openpyxl')
        
        self.logger.info(f"\n[OK] Resultados salvos em {output_dir}/")
        self.logger.info(f"  CSV:")
        self.logger.info(f"    - {arquivo_resultado_csv.name}")
        self.logger.info(f"    - {arquivo_resumo_csv.name}")
        self.logger.info(f"  Excel:")
        self.logger.info(f"    - {arquivo_resultado_xlsx.name} (com 3 abas: Detalhado, Resumo, Estatisticas)")
        self.logger.info(f"    - {arquivo_resumo_xlsx.name}")
    
    def _criar_aba_estatisticas(self, resumo_classe: pd.DataFrame):
        """Cria DataFrame com estatisticas e resumo executivo."""
        estatisticas = []
        
        # Separar pedidos e excedente
        if 'tipo' in self.resultado.columns:
            df_pedidos = self.resultado[self.resultado['tipo'] == 'PEDIDO']
            df_excedente = self.resultado[self.resultado['tipo'] == 'EXCEDENTE']
        else:
            df_pedidos = pd.DataFrame()
            df_excedente = self.resultado
        
        # 1. PRODUCAO vs PEDIDOS ( usando producao por classe)
        df_producao = self.dados['producao']
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        
        total_producao = df_producao['producao_total'].sum()
        atender_pedidos = self.dados.get('atender_pedidos', True)
        # Se atender_pedidos=false, mostrar 0 nos pedidos (ou nao considerar pedidos)
        if atender_pedidos and len(df_pedidos_sku) > 0:
            total_pedido = df_pedidos_sku['quantidade_total_pedida'].sum()
        else:
            total_pedido = 0
        producao_excedente_por_classe = self.dados.get('producao_excedente_por_classe', pd.Series())
        total_excedente = producao_excedente_por_classe.sum() if len(producao_excedente_por_classe) > 0 else total_producao
        
        estatisticas.append({'Categoria': 'PRODUCAO vs PEDIDOS', 'Metrica': 'Producao Total', 'Valor': f'{total_producao:,.0f}', 'Unidade': 'unidades'})
        estatisticas.append({'Categoria': 'PRODUCAO vs PEDIDOS', 'Metrica': 'Pedidos Totais', 'Valor': f'{total_pedido:,.0f}', 'Unidade': 'unidades'})
        estatisticas.append({'Categoria': 'PRODUCAO vs PEDIDOS', 'Metrica': 'Producao Excedente', 'Valor': f'{total_excedente:,.0f}', 'Unidade': 'unidades'})
        # Percentual excedente (producao disponivel para otimizacao / producao total)
        if total_producao > 0:
            estatisticas.append({'Categoria': 'PRODUCAO vs PEDIDOS', 'Metrica': 'Percentual Excedente', 'Valor': f'{total_excedente/total_producao*100:.1f}', 'Unidade': '%'})
        
        # 2. PEDIDOS ATENDIDOS
        if len(df_pedidos) > 0:
            qtd_atendida = df_pedidos['quantidade'].sum()
            margem_pedidos = df_pedidos['margem_total'].sum()
            pct_medio = df_pedidos['percentual_atendido'].mean() if 'percentual_atendido' in df_pedidos.columns else 0
            
            estatisticas.append({'Categoria': 'PEDIDOS ATENDIDOS', 'Metrica': 'SKUs Atendidos', 'Valor': f'{len(df_pedidos)}', 'Unidade': 'SKUs'})
            estatisticas.append({'Categoria': 'PEDIDOS ATENDIDOS', 'Metrica': 'Quantidade Atendida', 'Valor': f'{qtd_atendida:,.0f}', 'Unidade': 'unidades'})
            estatisticas.append({'Categoria': 'PEDIDOS ATENDIDOS', 'Metrica': 'Percentual Medio Atendido', 'Valor': f'{pct_medio:.1f}', 'Unidade': '%'})
            estatisticas.append({'Categoria': 'PEDIDOS ATENDIDOS', 'Metrica': 'Margem dos Pedidos', 'Valor': f'R$ {margem_pedidos:,.2f}', 'Unidade': 'R$'})
        
        # 3. OTIMIZACAO NO EXCEDENTE
        if len(df_excedente) > 0:
            qtd_excedente = df_excedente['quantidade'].sum()
            margem_excedente = df_excedente['margem_total'].sum()
            
            estatisticas.append({'Categoria': 'OTIMIZACAO NO EXCEDENTE', 'Metrica': 'Combinacoes Escolhidas', 'Valor': f'{len(df_excedente)}', 'Unidade': 'combinacoes'})
            estatisticas.append({'Categoria': 'OTIMIZACAO NO EXCEDENTE', 'Metrica': 'Quantidade Alocada', 'Valor': f'{qtd_excedente:,.0f}', 'Unidade': 'unidades'})
            estatisticas.append({'Categoria': 'OTIMIZACAO NO EXCEDENTE', 'Metrica': 'Margem do Excedente', 'Valor': f'R$ {margem_excedente:,.2f}', 'Unidade': 'R$'})
        
        # 4. TOTAIS
        qtd_total = self.resultado['quantidade'].sum()
        receita_total = self.resultado['receita_total'].sum()
        custo_total = self.resultado['custo_total'].sum()
        margem_total = self.resultado['margem_total'].sum()
        margem_pct = (margem_total / receita_total * 100) if receita_total > 0 else 0
        
        estatisticas.append({'Categoria': 'TOTAIS', 'Metrica': 'Quantidade Total Alocada', 'Valor': f'{qtd_total:,.0f}', 'Unidade': 'unidades'})
        estatisticas.append({'Categoria': 'TOTAIS', 'Metrica': 'Receita Total', 'Valor': f'R$ {receita_total:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'TOTAIS', 'Metrica': 'Custo Total', 'Valor': f'R$ {custo_total:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'TOTAIS', 'Metrica': 'Margem Total', 'Valor': f'R$ {margem_total:,.2f}', 'Unidade': 'R$'})
        estatisticas.append({'Categoria': 'TOTAIS', 'Metrica': 'Margem Percentual', 'Valor': f'{margem_pct:.2f}', 'Unidade': '%'})
        
        # 5. COMPARATIVO BASELINE vs OTIMIZADO
        comparativo = self.calcular_comparativo()
        if comparativo:
            estatisticas.append({'Categoria': 'COMPARATIVO', 'Metrica': 'Margem Baseline', 'Valor': f'R$ {comparativo["margem_baseline"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': 'COMPARATIVO', 'Metrica': 'Margem Otimizada', 'Valor': f'R$ {comparativo["margem_otimizada"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': 'COMPARATIVO', 'Metrica': 'Ganho Absoluto', 'Valor': f'R$ {comparativo["ganho_absoluto"]:,.2f}', 'Unidade': 'R$'})
            estatisticas.append({'Categoria': 'COMPARATIVO', 'Metrica': 'Ganho Percentual', 'Valor': f'{comparativo["ganho_percentual"]:.2f}', 'Unidade': '%'})
        
        # 6. REALOCACOES SIGNIFICATIVAS (top 10) - Removido variacao_pct (sempre 0)
        # Nota: Como variacao_pct sempre e 0 no novo formato (sem baseline por item_id),
        # esta secao foi removida
        
        # 7. CLASSES COM MAIOR POTENCIAL
        if len(resumo_classe) > 0:
            top_classes = resumo_classe.nlargest(5, 'margem_total')
            estatisticas.append({'Categoria': 'TOP CLASSES', 'Metrica': 'Numero de Classes Analisadas', 'Valor': f'{len(resumo_classe)}', 'Unidade': 'classes'})
            for _, row in top_classes.iterrows():
                estatisticas.append({
                    'Categoria': 'TOP CLASSES',
                    'Metrica': row['classe'],
                    'Valor': f'Margem: R$ {row["margem_total"]:,.2f} | {int(row["num_skus"])} SKUs | {row["quantidade_alocada"]:,.0f} un',
                    'Unidade': ''
                })
        
        return pd.DataFrame(estatisticas)


def main():
    """Funcao principal."""
    modelo = ModeloOtimizacaoComRealocacao()
    modelo.carregar_dados()
    modelo.criar_modelo()
    
    if modelo.resolver():
        modelo.calcular_comparativo()
        modelo.salvar_resultados()


if __name__ == '__main__':
    main()
