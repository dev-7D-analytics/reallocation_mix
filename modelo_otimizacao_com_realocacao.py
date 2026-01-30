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
        self._carregar_precos()
        self._carregar_custos()
        self._carregar_demanda_historica()
        self._carregar_skus_restritos()
        self._preparar_dados_otimizacao()
        
        self.logger.info("\n[OK] Dados carregados com sucesso!")
    
    def _carregar_producao(self):
        """Carrega producao por classe de produtos."""
        self.logger.info("\n[1/8] Carregando producao por classe...")
        
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
        self.logger.info("\n[2/8] Carregando classificacao de SKUs...")
        
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
        self.logger.info("\n[3/8] Carregando pedidos de clientes...")
        
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
        self.logger.info("\n[4/8] Carregando precos...")
        
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
        self.logger.info("\n[5/8] Carregando custos...")
        
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
            self.logger.info("\n[6/8] Demanda historica: Desabilitada")
            self.dados['demanda_historica'] = pd.DataFrame(columns=['item', 'demanda_max'])
            return
        
        self.logger.info("\n[6/8] Carregando demanda historica...")
        
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
            
            # Filtrar para MANTER apenas estabelecimentos configurados (se lista não vazia)
            estabs_manter = self.config.get('negocio', {}).get('filtrar_granjas', [])
            if estabs_manter:
                if 'Estab' in df_fat.columns:
                    df_fat = df_fat[df_fat['Estab'].astype(str).isin(estabs_manter)].copy()
                    self.logger.info(f"  Mantendo apenas estabelecimentos: {estabs_manter}")
                    self.logger.info(f"  Registros apos filtro: {len(df_fat):,}")
                else:
                    self.logger.warning("  Coluna 'Estab' nao encontrada para filtro de estabelecimentos.")
            
            # Converter quantidade de CAIXAS para OVOS usando a embalagem real de cada item
            # (ao invés de assumir 360 ovos/caixa para todos)
            from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem
            
            # Detectar coluna de descrição
            col_desc = None
            for col in df_fat.columns:
                if 'descri' in col.lower() and 'item' in col.lower():
                    col_desc = col
                    break
            if col_desc is None:
                col_desc = 'Descrição do item'  # fallback
            
            # Extrair embalagem e calcular ovos por caixa
            df_fat['_embalagem'] = df_fat[col_desc].apply(extrair_embalagem_descricao)
            df_fat['_ovos_por_caixa'] = df_fat['_embalagem'].apply(calcular_qtd_embalagem)
            
            # Usar 360 como fallback para embalagens não reconhecidas
            df_fat['_ovos_por_caixa'] = df_fat['_ovos_por_caixa'].fillna(360)
            
            # Log de estatísticas da conversão
            ovos_dist = df_fat['_ovos_por_caixa'].value_counts().to_dict()
            self.logger.info(f"  Distribuição de ovos/caixa: {ovos_dist}")
            
            # Converter quantidade para ovos
            df_fat[col_qtd] = df_fat[col_qtd] * df_fat['_ovos_por_caixa']
            
            # Limpar colunas temporárias
            df_fat = df_fat.drop(columns=['_embalagem', '_ovos_por_caixa'])
            
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
    
    def _carregar_skus_restritos(self):
        """Carrega lista de SKUs permitidos para produção no estabelecimento (ESTAB, STATUS=ATIVO).
        Usada para filtrar PRODUÇÃO DIA: só alocamos quem apareceu na produção E está nesta lista."""
        self.logger.info("\n[7/8] Carregando SKUs permitidos para produção (estabelecimento)...")
        
        path_restritos_str = self.config['paths'].get('skus_restritos', 'inputs/skus_restritos.xlsx')
        path_restritos = Path(path_restritos_str) if path_restritos_str else None
        
        if not path_restritos or not path_restritos.exists():
            self.logger.warning("  Arquivo de SKUs permitidos nao encontrado. Filtro por lista nao aplicado.")
            self.dados['skus_restritos'] = []
            return
        
        try:
            df_restritos = pd.read_excel(path_restritos)
            
            # Ler lista de estabelecimentos do config (pode ser lista ou valor unico)
            estabelecimentos_config = self.config.get('dados', {}).get('estabelecimentos', [100])
            # Garantir que seja uma lista
            if not isinstance(estabelecimentos_config, list):
                estabelecimentos_config = [estabelecimentos_config]
            
            # Converter para int para garantir compatibilidade
            estabelecimentos = [int(estab) for estab in estabelecimentos_config]
            
            self.logger.info(f"  Estabelecimentos considerados: {estabelecimentos}")
            
            # Filtrar por STATUS='ATIVO' e ESTAB na lista de estabelecimentos
            df_restritos_filtrado = df_restritos[
                (df_restritos['STATUS'] == 'ATIVO') & 
                (df_restritos['ESTAB'].isin(estabelecimentos))
            ].copy()
            
            # Extrair lista de SKUs permitidos para produção no estabelecimento
            skus_restritos = df_restritos_filtrado['item'].tolist()
            
            # Converter para int para garantir compatibilidade
            skus_restritos = [int(item) for item in skus_restritos if pd.notna(item)]
            
            self.logger.info(f"  SKUs permitidos para produção (ESTAB {estabelecimentos}, ATIVO): {len(skus_restritos)}")
            if len(skus_restritos) > 0:
                self.logger.info(f"  Exemplos: {skus_restritos[:5]}")
            
            self.dados['skus_restritos'] = skus_restritos
            
        except Exception as e:
            self.logger.warning(f"  Erro ao carregar SKUs permitidos: {e}")
            self.logger.warning("  Continuando sem filtro por lista de permitidos.")
            self.dados['skus_restritos'] = []
    
    def _preparar_dados_otimizacao(self):
        """Prepara dados para otimizacao usando producao por classe."""
        self.logger.info("\n[8/8] Preparando dados para otimizacao...")
        
        df_producao = self.dados['producao']  # Producao por classe
        df_classes = self.dados['classes']  # Mapeamento item -> classe
        df_precos = self.dados['precos']  # Precos por item_id
        df_custos = self.dados['custos']  # Custos por item_id (ja inclui item + embalagem)
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        
        #  Criar base a partir de custos (cada linha ja e um item_id unico)
        # O item_id ja inclui codigo_item + embalagem
        # TODO: Avaliar se é melhor comecar a construção pela base de produção
        df_base = df_custos[['item_id', 'item', 'embalagem', 'custo_ytd']].copy()
        
        # =====================================================================
        # FILTRO CRÍTICO: Apenas SKUs que estão na produção do estabelecimento 100
        # =====================================================================
        # Carregar produção bruta para obter lista de SKUs do estabelecimento 100
        estabelecimentos_config = self.config.get('dados', {}).get('estabelecimentos', [100])
        if not isinstance(estabelecimentos_config, list):
            estabelecimentos_config = [estabelecimentos_config]
        estabelecimentos = [int(estab) for estab in estabelecimentos_config]
        
        path_producao_bruta = Path(self.config['paths'].get('producao_bruta', 'inputs/PRODUÇÃO DIA.xlsx'))
        skus_producao_estab = set()
        
        if path_producao_bruta.exists():
            try:
                df_prod_bruta = pd.read_excel(path_producao_bruta, sheet_name="CE0302", skiprows=1)
                
                # Filtrar por semana de referência
                semana_ref = self.config.get('dados', {}).get('semana_ref', '2025-51')
                df_prod_bruta['week'] = pd.to_datetime(df_prod_bruta['Data Trans']).dt.isocalendar().week
                df_prod_bruta['year'] = pd.to_datetime(df_prod_bruta['Data Trans']).dt.isocalendar().year
                df_prod_bruta['year_week'] = df_prod_bruta['year'].astype(str) + '-' + df_prod_bruta['week'].astype(str).str.zfill(2)
                df_prod_bruta = df_prod_bruta[df_prod_bruta['year_week'] == semana_ref].copy()
                
                # Filtrar por estabelecimento (coluna 'Est' ou 'Estab')
                col_estab = 'Est' if 'Est' in df_prod_bruta.columns else ('Estab' if 'Estab' in df_prod_bruta.columns else None)
                if col_estab:
                    df_prod_bruta = df_prod_bruta[df_prod_bruta[col_estab].isin(estabelecimentos)].copy()
                    
                    # Extrair SKUs únicos da produção do estabelecimento
                    col_item = 'Cod Item' if 'Cod Item' in df_prod_bruta.columns else 'CODIGO ITEM'
                    if col_item in df_prod_bruta.columns:
                        df_prod_bruta[col_item] = pd.to_numeric(df_prod_bruta[col_item], errors='coerce')
                        df_prod_bruta = df_prod_bruta[df_prod_bruta[col_item].notna()].copy()
                        skus_producao_estab = set(df_prod_bruta[col_item].astype(int).unique())
                        
                        self.logger.info(f"  SKUs na produção do estabelecimento {estabelecimentos}: {len(skus_producao_estab)} SKUs")
                        if len(skus_producao_estab) > 0:
                            self.logger.info(f"    Exemplos: {sorted(list(skus_producao_estab))[:10]}...")
                    else:
                        self.logger.warning(f"  Coluna de item não encontrada na produção bruta. Pulando filtro por estabelecimento.")
                else:
                    self.logger.warning(f"  Coluna de estabelecimento não encontrada na produção bruta. Pulando filtro por estabelecimento.")
            except Exception as e:
                self.logger.warning(f"  Erro ao carregar produção bruta para filtrar por estabelecimento: {e}")
                self.logger.warning("  Continuando sem filtro por estabelecimento (pode incluir SKUs de outros estabelecimentos)")
        
        # Filtrar PRODUÇÃO DIA pela lista de itens permitidos (A∩B: apareceu na semana E está na lista)
        skus_permitidos = set(int(sku) for sku in self.dados.get('skus_restritos', []) if pd.notna(sku))
        if len(skus_permitidos) > 0 and len(skus_producao_estab) > 0:
            antes_interseccao = len(skus_producao_estab)
            skus_producao_estab = skus_producao_estab & skus_permitidos
            self.logger.info(f"  Filtrado pela lista de itens permitidos: {len(skus_producao_estab)} SKUs (interseção produção semana × permitidos)")
            if len(skus_producao_estab) < antes_interseccao:
                self.logger.info(f"    Removidos da base: {antes_interseccao - len(skus_producao_estab)} SKUs (não estão na lista de permitidos)")
        
        # Aplicar filtro: apenas SKUs que estão na produção do estabelecimento
        if len(skus_producao_estab) > 0:
            antes_filtro_estab = len(df_base)
            df_base = df_base[df_base['item'].isin(skus_producao_estab)].copy()
            removidos_estab = antes_filtro_estab - len(df_base)
            if removidos_estab > 0:
                self.logger.info(f"  SKUs removidos (não estão na produção do estabelecimento {estabelecimentos}): {removidos_estab} item_ids")
        else:
            self.logger.warning("  [AVISO] Não foi possível filtrar por estabelecimento. Todos os SKUs serão considerados.")
        
        # Adicionar flag indicando que custo veio de dados reais (não calculado)
        df_base['custo_medio_classe'] = False
        
        # Adicionar classe para cada item_id (usando o codigo do item)
        df_base = df_base.merge(df_classes, on='item', how='inner')
        
        # Filtrar apenas classes que tem producao
        df_base = df_base.merge(df_producao[['classe']], on='classe', how='inner')
        
        # Ler flags de configuracao ANTES de usar
        usar_apenas_excedente = self.config.get('modelo', {}).get('usar_apenas_excedente', True)
        atender_pedidos = self.config.get('modelo', {}).get('atender_pedidos', True)
        
       
        # =====================================================================
        # Pedidos são garantidos (fixos) e descontados da produção da classe
        # SKUs com pedidos NÃO entram na otimização (removidos de df_base)
        # =====================================================================
        
        # Calcular pedidos garantidos por SKU (capados pela produção da classe)
        pedidos_garantidos_por_sku = {}  # item -> qtd_atendida (garantida)
        pedidos_garantidos_df = []  # Lista para DataFrame
        
        if len(df_pedidos_sku) > 0 and atender_pedidos:
            # Criar dicionário de produção por classe
            producao_por_classe_dict = df_producao.set_index('classe')['producao_total'].to_dict()
            
            for _, row_pedido in df_pedidos_sku.iterrows():
                item = int(row_pedido['item'])
                qtd_pedida = float(row_pedido['quantidade_total_pedida'])
                
                # Buscar classe do SKU
                classe_sku = df_classes[df_classes['item'] == item]['classe'].values
                if len(classe_sku) > 0:
                    classe = classe_sku[0]
                    producao_total_classe = producao_por_classe_dict.get(classe, 0.0)
                    
                    # Pedido garantido = min(pedido, produção da classe)
                    qtd_atendida = min(qtd_pedida, producao_total_classe)
                    
                    if qtd_atendida > 0:
                        pedidos_garantidos_por_sku[item] = qtd_atendida
                        pedidos_garantidos_df.append({
                            'item': item,
                            'classe': classe,
                            'quantidade_pedida': qtd_pedida,
                            'quantidade_atendida': qtd_atendida,
                            'producao_total_classe': producao_total_classe
                        })
        
        # Guardar pedidos garantidos em self.dados
        self.dados['pedidos_garantidos_por_sku'] = pedidos_garantidos_por_sku
        if len(pedidos_garantidos_df) > 0:
            self.dados['pedidos_garantidos'] = pd.DataFrame(pedidos_garantidos_df)
        else:
            self.dados['pedidos_garantidos'] = pd.DataFrame(columns=['item', 'classe', 'quantidade_pedida', 'quantidade_atendida', 'producao_total_classe'])
        
        # Identificar SKUs que devem ser removidos de df_base: apenas os que têm pedido garantido
        # (Produção do estabelecimento já foi filtrada por A∩B acima; não removemos "restritos")
        skus_com_pedido = set(int(sku) for sku in pedidos_garantidos_por_sku.keys() if pd.notna(sku)) if atender_pedidos else set()
        skus_a_remover = skus_com_pedido
        
        # Garantir que a coluna 'item' seja int para comparação consistente
        if 'item' in df_base.columns:
            df_base['item'] = pd.to_numeric(df_base['item'], errors='coerce').astype('Int64')
        
        if len(skus_a_remover) > 0:
            antes_filtro = len(df_base)
            df_base = df_base[~df_base['item'].isin(skus_a_remover)].copy()
            removidos = antes_filtro - len(df_base)
            if removidos > 0:
                self.logger.info(f"  SKUs removidos do df_base (com pedido garantido): {removidos} item_ids")
                self.logger.info(f"    Exemplos removidos: {sorted(list(skus_a_remover))[:10]}...")
        
        # =====================================================================
        # INCLUIR SKUs SEM CUSTOS: usar custo médio da classe
        # =====================================================================
        # Identificar SKUs em classes com produção que não têm custos
        # (após remover SKUs com pedido)
        # IMPORTANTE: Apenas SKUs que pertencem ao estabelecimento configurado (skus_producao_estab)
        skus_com_custos = set(df_base['item'].unique())
        skus_com_producao = set(df_classes[df_classes['classe'].isin(df_producao['classe'])]['item'].unique())
        
        # FILTRO CRÍTICO: Apenas SKUs que estão na produção do estabelecimento
        # Se não foi possível filtrar por estabelecimento (skus_producao_estab vazio),
        # usar todos os SKUs com produção (comportamento antigo)
        if len(skus_producao_estab) > 0:
            skus_com_producao = skus_com_producao & skus_producao_estab
            self.logger.info(f"  Filtrando SKUs sem custos: apenas {len(skus_com_producao)} SKUs do estabelecimento {estabelecimentos}")
        
        skus_sem_custos = (skus_com_producao - skus_com_custos) - skus_a_remover  # Excluir apenas com pedido
        
        if len(skus_sem_custos) > 0:
            # Calcular custo médio por classe (apenas dos SKUs que têm custos)
            custo_medio_por_classe = df_base.groupby('classe')['custo_ytd'].mean().to_dict()
            
            # Calcular custo médio geral de df_base e df_custos (para fallback)
            custo_medio_geral_df_base = df_base['custo_ytd'].mean() if len(df_base) > 0 else 0.0
            custo_medio_geral_df_custos = df_custos['custo_ytd'].mean() if len(df_custos) > 0 and 'custo_ytd' in df_custos.columns else 0.0
            
            # Para cada SKU sem custo, criar item_ids com todas as embalagens possíveis
            # Primeiro, identificar embalagens disponíveis (de preços ou custos existentes)
            embalagens_disponiveis = set(df_precos['embalagem'].unique()) if 'embalagem' in df_precos.columns else set(df_custos['embalagem'].unique())
            
            # Se não houver embalagens em preços, usar embalagens dos custos
            if len(embalagens_disponiveis) == 0:
                embalagens_disponiveis = set(df_custos['embalagem'].unique())
            
            # Se ainda não houver, usar embalagem padrão
            if len(embalagens_disponiveis) == 0:
                embalagens_disponiveis = {'CX12'}  # Embalagem padrão
            
            # Criar linhas para SKUs sem custos
            linhas_sem_custo = []
            for item in skus_sem_custos:
                # Buscar classe do SKU
                classe_sku = df_classes[df_classes['item'] == item]['classe'].values
                if len(classe_sku) > 0:
                    classe = classe_sku[0]
                    custo_medio_classe = custo_medio_por_classe.get(classe, 0.0)
                    
                    # Se a classe não tem custo médio, usar custo médio geral
                    if custo_medio_classe == 0.0:
                        # Tentar primeiro custo médio geral de df_base, depois df_custos
                        if custo_medio_geral_df_base > 0.0:
                            custo_medio_classe = custo_medio_geral_df_base
                        elif custo_medio_geral_df_custos > 0.0:
                            custo_medio_classe = custo_medio_geral_df_custos
                        else:
                            # Se ainda for 0.0, usar um valor padrão razoável (custo médio histórico)
                            custo_medio_classe = 132.82  # Valor padrão baseado em custos históricos
                            self.logger.warning(f"  SKU {item} (classe {classe}) sem custo médio disponível - usando valor padrão R$ {custo_medio_classe:.2f}")
                    
                    # Criar item_id para cada embalagem disponível
                    for embalagem in embalagens_disponiveis:
                        item_id = f"{item}_{embalagem}"
                        linhas_sem_custo.append({
                            'item_id': item_id,
                            'item': item,
                            'embalagem': embalagem,
                            'custo_ytd': custo_medio_classe,
                            'custo_medio_classe': True,  # Flag indicando que custo foi calculado
                            'classe': classe
                        })
            
            if len(linhas_sem_custo) > 0:
                df_sem_custos = pd.DataFrame(linhas_sem_custo)
                # Adicionar ao df_base
                df_base = pd.concat([df_base, df_sem_custos], ignore_index=True)
                self.logger.info(f"  SKUs sem custos incluídos usando custo médio da classe: {len(skus_sem_custos)} SKUs")
        
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
        
        # Calcular pedidos GARANTIDOS por classe (usando pedidos_garantidos_por_sku)
        # Usar quantidade_atendida (garantida)
        pedidos_ignorados = []  # Rastrear pedidos ignorados
        
        if atender_pedidos and len(df_pedidos_sku) > 0:
            # Identificar pedidos ignorados (pedidos que não foram garantidos)
            for _, row_pedido in df_pedidos_sku.iterrows():
                item = int(row_pedido['item'])
                qtd_pedida = float(row_pedido['quantidade_total_pedida'])
                
                # Verificar se pedido foi garantido
                if item not in pedidos_garantidos_por_sku:
                    # Pedido não foi garantido - identificar motivo
                    classe_sku = df_classes[df_classes['item'] == item]['classe'].values
                    if len(classe_sku) == 0:
                        motivo = 'SKU nao encontrado em classes'
                    elif classe_sku[0] not in df_producao['classe'].values:
                        motivo = f"Classe '{classe_sku[0]}' nao tem producao"
                    else:
                        # Classe existe mas pedido não foi garantido (provavelmente produção insuficiente)
                        motivo = 'Producao insuficiente para atender pedido completo'
                    
                    pedidos_ignorados.append({
                        'item': item,
                        'quantidade_total_pedida': qtd_pedida,
                        'motivo': motivo
                    })
                elif pedidos_garantidos_por_sku[item] < qtd_pedida:
                    # Pedido foi parcialmente atendido
                    pedidos_ignorados.append({
                        'item': item,
                        'quantidade_total_pedida': qtd_pedida,
                        'quantidade_atendida': pedidos_garantidos_por_sku[item],
                        'motivo': 'Pedido parcialmente atendido (producao insuficiente)'
                    })
            
            # Calcular pedidos garantidos por classe
            if len(self.dados['pedidos_garantidos']) > 0:
                df_pedidos_garantidos = self.dados['pedidos_garantidos']
                pedidos_por_classe = df_pedidos_garantidos.groupby('classe')['quantidade_atendida'].sum()
                # Garantir que todas as classes de producao estejam presentes
                pedidos_por_classe = pedidos_por_classe.reindex(df_producao['classe'], fill_value=0)
            else:
                pedidos_por_classe = pd.Series(0, index=df_producao['classe'])
            
            # Log resumo de pedidos ignorados
            if len(pedidos_ignorados) > 0:
                total_ignorado = sum(p.get('quantidade_total_pedida', 0) - p.get('quantidade_atendida', 0) for p in pedidos_ignorados)
                self.logger.warning(f"\n  [ATENCAO] {len(pedidos_ignorados)} pedidos ignorados/parciais (total não atendido: {total_ignorado:,.0f} unidades)")
        else:
            pedidos_por_classe = pd.Series(0, index=df_producao['classe'])
        
        # Armazenar pedidos ignorados para uso nos outputs
        self.dados['pedidos_ignorados'] = pedidos_ignorados
        
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
        
        # Para compatibilidade: criar estoque_excedente_sku 
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
        
       
        # =====================================================================
        # Pedidos foram calculados em _preparar_dados_otimizacao e são parâmetros
        # =====================================================================
        atender_pedidos = self.dados.get('atender_pedidos', True)
        pedidos_garantidos_por_sku = self.dados.get('pedidos_garantidos_por_sku', {})
        
        # Manter compatibilidade: self.variaveis_pedidos agora armazena valores fixos 
        # Isso permite que _extrair_resultado continue funcionando
        self.variaveis_pedidos = pedidos_garantidos_por_sku.copy()
        
        if atender_pedidos:
            self.logger.info(f"  Pedidos garantidos (valores fixos): {len(pedidos_garantidos_por_sku)} SKUs")
            if len(pedidos_garantidos_por_sku) > 0:
                total_garantido = sum(pedidos_garantidos_por_sku.values())
                self.logger.info(f"    Total garantido: {total_garantido:,.0f} unidades")
        else:
            self.logger.info(f"  Pedidos garantidos: 0 (pedidos ignorados)")
        
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
        
        # Carregar df_classes para identificar TODOS os SKUs da classe (incluindo restritos)
        df_classes = self.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
        
        # =====================================================================
        # RESTRICAO 1: Pedidos são GARANTIDOS (valores fixos)
        # =====================================================================
        # Pedidos foram calculados em _preparar_dados_otimizacao e são parâmetros
        # =====================================================================
        atender_pedidos = self.dados.get('atender_pedidos', True)
        
        if atender_pedidos:
            pedidos_garantidos_por_sku = self.dados.get('pedidos_garantidos_por_sku', {})
            self.logger.info(f"  Restricoes de atendimento aos pedidos: 0 (pedidos são valores fixos garantidos)")
            self.logger.info(f"    Pedidos garantidos: {len(pedidos_garantidos_por_sku)} SKUs")
        else:
            self.logger.info(f"  Restricoes de atendimento aos pedidos: 0 (pedidos ignorados)")
        
        # RESTRICAO 2: Volume total por CLASSE <= producao disponivel da classe
        #  Pedidos devem consumir producao da classe
        # Esta restricao garante que: soma(pedidos + excedente) <= producao_total
        # Esta e a restricao que permite realocacao entre item_id da mesma classe!
        usar_apenas_excedente = self.dados.get('usar_apenas_excedente', True)
        
        # CORRECAO: Usar df_producao para pegar TODAS as classes com producao
        # Isso garante que classes que so tem SKUs restritos tambem tenham restricao criada
        df_producao = self.dados.get('producao', pd.DataFrame(columns=['classe', 'producao_total']))
        producao_por_classe = df_producao.set_index('classe')['producao_total'].to_dict() if len(df_producao) > 0 else {}
        classes = df_producao['classe'].unique() if len(df_producao) > 0 else []
        
        num_restricoes_classe = 0
        for classe in classes:
            #  Todas as variaveis de item_id desta classe (pode ser vazio se classe so tem SKUs restritos)
            item_ids_classe = df_base[df_base['classe'] == classe]['item_id'].unique()
            
            # Soma de todas as alocacoes de EXCEDENTE da classe (por item_id)
            # Se classe so tem SKUs restritos, item_ids_classe sera vazio e soma_excedente = 0
            if len(item_ids_classe) > 0:
                soma_excedente_classe = sum(
                    self.variaveis.get(item_id, 0)
                    for item_id in item_ids_classe
                    if item_id in self.variaveis
                )
            else:
                soma_excedente_classe = 0  # Classe so tem SKUs restritos, nao ha excedente
            
            # Soma de todos os PEDIDOS GARANTIDOS da classe (valores fixos)
            # IMPORTANTE: Pedidos são valores fixos calculados em _preparar_dados_otimizacao
            # Usar df_classes para identificar TODOS os SKUs da classe (incluindo restritos)
            items_da_classe = df_classes[df_classes['classe'] == classe]['item'].unique()
            pedidos_garantidos_por_sku = self.dados.get('pedidos_garantidos_por_sku', {})
            soma_pedidos_classe = sum(
                pedidos_garantidos_por_sku.get(item, 0.0)
                for item in items_da_classe
            )
            
            # Producao total da classe (fixo, nao depende de pedidos)
            # Usar df_producao diretamente para garantir que funciona mesmo se classe so tiver SKUs restritos
            producao_total_classe = producao_por_classe.get(classe, 0.0)
            
            # Verificar se ha variaveis para restringir (pedidos ou excedente)
            # IMPORTANTE: soma_pedidos_classe agora é um valor numérico fixo 
            # soma_excedente_classe ainda é uma expressão do solver
            tem_pedidos = soma_pedidos_classe > 0  # Valor fixo > 0
            tem_excedente = len(item_ids_classe) > 0 and any(item_id in self.variaveis for item_id in item_ids_classe)
            
            #  Restricao deve garantir que excedente <= producao_total - pedidos_garantidos
            # Pedidos já foram descontados da produção disponível em _preparar_dados_otimizacao
            # Mas vamos garantir explicitamente: excedente <= producao_total - pedidos_garantidos
            # Criar restricao se houver producao E (pedidos OU excedente) para evitar restricoes vazias
            if producao_total_classe > 0 and (tem_pedidos or tem_excedente):
                # Restricao: excedente <= producao_total - pedidos_garantidos
                # Como pedidos são fixos, podemos calcular producao_disponivel diretamente
                producao_disponivel_classe = producao_total_classe - soma_pedidos_classe
                self.solver.Add(soma_excedente_classe <= producao_disponivel_classe)
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
        
        #  Aplicar restricao de demanda historica por SKU (soma de todas embalagens)
        # Restricao deve ser por SKU (soma de todas embalagens), nao por item_id individual
        #
        # COMPORTAMENTO: SKU SEM historico de demanda
        # - Nao recebe restricao de demanda (excedente + pedidos <= demanda_max).
        # - O unico limite e a producao da classe (restricao 2).
        # - O modelo PODE alocar volume nele; o solver decide pela margem/objetivo.
        # - Nao e limite zero: quem tem limite zero nao entra em df_base (ex.: restritos).
        if considerar_demanda and len(df_demanda) > 0:
            # Agrupar item_id por SKU (item) para aplicar restricao somada
            # Criar dicionario: item -> lista de item_id desse SKU
            skus_com_demanda = {}
            demanda_por_sku = df_demanda.set_index('item')['demanda_max'].to_dict()
            
            # Agrupar item_id por SKU
            for _, row in df_base.iterrows():
                item_id = row['item_id']
                item = row['item']
                
                if item_id not in self.variaveis:
                    continue
                
                if item not in skus_com_demanda:
                    skus_com_demanda[item] = []
                skus_com_demanda[item].append(item_id)
            
            # Aplicar restricao somada por SKU (apenas para SKUs que TEM demanda historica)
            # SKUs sem entrada em demanda_por_sku nao recebem esta restricao -> limite = producao da classe
            for item, item_ids_do_sku in skus_com_demanda.items():
                if item in demanda_por_sku:
                    limite_demanda = float(demanda_por_sku[item])
                    # Soma de todas embalagens do SKU (excedente) <= demanda_max
                    soma_embalagens_excedente = sum(self.variaveis[item_id] for item_id in item_ids_do_sku)
                    
                    # Incluir pedidos na restricao de demanda (se houver pedido para este SKU)
                    pedido_do_sku = self.variaveis_pedidos.get(item, 0)
                    
                    # Restricao: excedente + pedidos <= demanda_max
                    # Isso garante que a demanda historica seja respeitada mesmo com pedidos
                    self.solver.Add(soma_embalagens_excedente + pedido_do_sku <= limite_demanda)
                    num_restricoes_demanda += 1
                    num_restricoes_item_id += 1
        
        num_restricoes += num_restricoes_item_id
        modo_desc = "excedente" if usar_apenas_excedente else "total"
        considerar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        demanda_desc = " (demanda historica por SKU)" if considerar_demanda else ""
        self.logger.info(f"  Restricoes de limite por item_id{demanda_desc}: {num_restricoes_item_id}")
        if considerar_demanda and num_restricoes_demanda > 0:
            self.logger.info(f"    - Restricoes com demanda historica aplicada (por SKU, soma embalagens): {num_restricoes_demanda}")
        
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
        pedidos_garantidos_por_sku = self.dados.get('pedidos_garantidos_por_sku', {})
        
        if atender_pedidos and len(df_pedidos_sku) > 0:
            # Carregar dados necessarios para SKUs restritos
            df_classes = self.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
            df_precos = self.dados.get('precos', pd.DataFrame(columns=['item_id', 'preco']))
            df_custos = self.dados.get('custos', pd.DataFrame(columns=['item_id', 'custo_ytd']))
            
            for _, row in df_pedidos_sku.iterrows():
                item = row['item']
                
                # Usar quantidade GARANTIDA (fixa)
                if item in pedidos_garantidos_por_sku:
                    qtd_atendida_ovos = pedidos_garantidos_por_sku[item]  # Valor fixo
                    item_ids_do_sku = df_base[df_base['item'] == item]
                    if len(item_ids_do_sku) > 0:
                        # SKU normal: usar embalagem que maximiza margem total para o pedido
                        # Para pedidos em ovos, calcular qual embalagem da maior margem total
                        # Usar quantidade garantida (valor numerico) para calcular melhor embalagem
                        qtd_pedida_ovos = row['quantidade_total_pedida']
                        
                        if tipo_objetivo == 'maximizar_margem':
                            # Calcular margem total para cada embalagem (assumindo pedido completo)
                            # margem_total = (qtd_ovos / qtd_ovos_por_caixa) * margem_unitaria
                            item_ids_do_sku_copy = item_ids_do_sku.copy()
                            item_ids_do_sku_copy['margem_total_pedido'] = (
                                qtd_pedida_ovos / item_ids_do_sku_copy['qtd_ovos_por_caixa']
                            ) * item_ids_do_sku_copy['margem_unitaria']
                            # Usar embalagem com maior margem total
                            melhor_embalagem_idx = item_ids_do_sku_copy['margem_total_pedido'].idxmax()
                            melhor_embalagem = item_ids_do_sku_copy.loc[melhor_embalagem_idx]
                            qtd_ovos_por_caixa_item = melhor_embalagem['qtd_ovos_por_caixa']
                            # Converter quantidade garantida (em ovos) para caixas usando melhor embalagem
                            qtd_caixas_pedido = qtd_atendida_ovos / qtd_ovos_por_caixa_item
                            margem_item = melhor_embalagem['margem_unitaria']
                            objetivo_pedidos += margem_item * qtd_caixas_pedido
                        else:  # minimizar_custos
                            # Para minimizar custos, usar embalagem com menor custo total
                            item_ids_do_sku_copy = item_ids_do_sku.copy()
                            item_ids_do_sku_copy['custo_total_pedido'] = (
                                qtd_pedida_ovos / item_ids_do_sku_copy['qtd_ovos_por_caixa']
                            ) * item_ids_do_sku_copy['custo_ytd']
                            # Usar embalagem com menor custo total
                            melhor_embalagem_idx = item_ids_do_sku_copy['custo_total_pedido'].idxmin()
                            melhor_embalagem = item_ids_do_sku_copy.loc[melhor_embalagem_idx]
                            qtd_ovos_por_caixa_item = melhor_embalagem['qtd_ovos_por_caixa']
                            # Converter quantidade garantida (em ovos) para caixas usando melhor embalagem
                            qtd_caixas_pedido = qtd_atendida_ovos / qtd_ovos_por_caixa_item
                            custo_item = melhor_embalagem['custo_ytd']
                            objetivo_pedidos += custo_item * qtd_caixas_pedido
                    else:
                        # SKU restrito ou com pedido (não está em df_base): buscar classe e usar preco/custo medio da classe
                        classe_sku = df_classes[df_classes['item'] == item]['classe'].values
                        if len(classe_sku) > 0:
                            classe = classe_sku[0]
                            # Buscar item_ids da classe que tem preco e custo (mesmo que SKU não esteja em df_base)
                            item_ids_da_classe = df_base[df_base['classe'] == classe]
                            if len(item_ids_da_classe) > 0:
                                # Usar media de preco/custo/margem da classe
                                preco_medio = item_ids_da_classe['preco'].mean()
                                custo_medio = item_ids_da_classe['custo_ytd'].mean()
                                margem_media = item_ids_da_classe['margem_unitaria'].mean()
                                qtd_ovos_por_caixa_media = item_ids_da_classe['qtd_ovos_por_caixa'].mean()
                                
                                # Converter quantidade garantida (em ovos) para caixas
                                qtd_caixas_pedido = qtd_atendida_ovos / qtd_ovos_por_caixa_media
                                
                                if tipo_objetivo == 'maximizar_margem':
                                    objetivo_pedidos += margem_media * qtd_caixas_pedido
                                else:  # minimizar_custos
                                    objetivo_pedidos += custo_medio * qtd_caixas_pedido
                            else:
                                # Classe sem item_ids no df_base (muito raro, mas tratar)
                                # Usar valores padrao ou ignorar
                                self.logger.warning(f"  [AVISO] SKU {item} na classe {classe} sem item_ids no df_base. Ignorando no objetivo.")
        
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
            # Carregar dados necessarios para SKUs restritos
            df_classes = self.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
            df_producao = self.dados.get('producao', pd.DataFrame(columns=['classe', 'producao_total']))
            producao_por_classe = df_producao.set_index('classe')['producao_total'].to_dict() if len(df_producao) > 0 else {}
            
            for _, row in df_pedidos_sku.iterrows():
                item = row['item']
                qtd_pedida = row['quantidade_total_pedida']
                item_ids_do_sku = df_base[df_base['item'] == item]
                if len(item_ids_do_sku) > 0:
                    # SKU normal: usar dados do df_base
                    producao_classe = float(item_ids_do_sku['producao_disponivel_otimizacao_classe'].iloc[0])
                    qtd_atendivel = min(qtd_pedida, producao_classe)
                    
                    margem_item = item_ids_do_sku['margem_unitaria'].iloc[0] if len(item_ids_do_sku) > 0 else 0
                    custo_item = item_ids_do_sku['custo_ytd'].iloc[0] if len(item_ids_do_sku) > 0 else 0
                    # Converter quantidade de ovos para caixas
                    qtd_ovos_por_caixa_item = item_ids_do_sku['qtd_ovos_por_caixa'].iloc[0] if len(item_ids_do_sku) > 0 else 360
                    qtd_caixas_atendivel = qtd_atendivel / qtd_ovos_por_caixa_item
                    
                    margem_potencial_pedidos += margem_item * qtd_caixas_atendivel
                    custo_potencial_pedidos += custo_item * qtd_caixas_atendivel
                else:
                    # SKU restrito: buscar classe e usar preco/custo medio da classe
                    classe_sku = df_classes[df_classes['item'] == item]['classe'].values
                    if len(classe_sku) > 0:
                        classe = classe_sku[0]
                        producao_total_classe = producao_por_classe.get(classe, 0.0)
                        qtd_atendivel = min(qtd_pedida, producao_total_classe)
                        
                        # Buscar item_ids da classe que tem preco e custo
                        item_ids_da_classe = df_base[df_base['classe'] == classe]
                        if len(item_ids_da_classe) > 0:
                            # Usar media de preco/custo/margem da classe
                            margem_media = item_ids_da_classe['margem_unitaria'].mean()
                            custo_medio = item_ids_da_classe['custo_ytd'].mean()
                            qtd_ovos_por_caixa_media = item_ids_da_classe['qtd_ovos_por_caixa'].mean()
                            qtd_caixas_atendivel = qtd_atendivel / qtd_ovos_por_caixa_media
                            
                            margem_potencial_pedidos += margem_media * qtd_caixas_atendivel
                            custo_potencial_pedidos += custo_medio * qtd_caixas_atendivel
        
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
        
        # Carregar informacoes para mapeamento nos outputs
        df_pedidos_sku = self.dados.get('pedidos_por_sku', pd.DataFrame(columns=['item', 'quantidade_total_pedida']))
        skus_com_pedido = set(df_pedidos_sku['item'].tolist()) if len(df_pedidos_sku) > 0 else set()
        
        # IMPORTANTE: Usar apenas SKUs restritos filtrados por estabelecimento para mapeamento
        # Não carregar TODOS os SKUs restritos, pois isso incluiria SKUs de outros estabelecimentos
        # que não deveriam estar sendo alocados de qualquer forma
        skus_restritos_filtrados = self.dados.get('skus_restritos', [])
        skus_restritos = set(int(sku) for sku in skus_restritos_filtrados if pd.notna(sku))
        
        skus_com_demanda_historica = set(demanda_por_item.keys()) if len(demanda_por_item) > 0 else set()
        
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
                    item_int = int(row['item']) if pd.notna(row['item']) else None
                    demanda_max = demanda_por_item.get(item_int) if item_int is not None else None
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
                    
                    # Mapear informacoes do SKU
                    item = row['item']
                    tem_pedido = item in skus_com_pedido
                    sku_restrito = False  # Base já filtrada por A∩B; coluna mantida por compatibilidade
                    tem_demanda_historica = item in skus_com_demanda_historica
                    
                    # Verificar se custo foi calculado usando média da classe
                    # Para pandas Series, usar acesso direto em vez de .get()
                    if 'custo_medio_classe' in row.index:
                        custo_medio_classe = bool(row['custo_medio_classe'])
                    else:
                        custo_medio_classe = False
                    
                    resultados.append({
                        'item_id': item_id,
                        'item': item,
                        'embalagem': row['embalagem'],
                        'classe': row['classe'],
                        'quantidade': qtd,  # Manter em ovos para referencia
                        'quantidade_caixas': qtd_caixas,  # Adicionar coluna em caixas
                        'tipo': tipo_alocacao,
                        'tipo_restricao': restricao_tipo,
                        'quantidade_restricao': restricao_quantidade,
                        'limite_demanda_historica': demanda_max if demanda_max is not None else None,  # Limite calculado de demanda histórica
                        'producao_total': row['producao_total'],  # Producao da classe
                        'producao_disponivel': row['producao_disponivel_otimizacao_classe'],  # Producao disponivel para otimizacao
                        'preco': row['preco'],
                        'custo_ytd': row['custo_ytd'],
                        'margem_unitaria': row['margem_unitaria'],  # R$/CAIXA
                        'margem_por_ovo': row['margem_unitaria'] / row['qtd_ovos_por_caixa'],  # R$/OVO - métrica otimizada
                        'receita_total': qtd_caixas * row['preco'],  # CAIXAS × R$/CAIXA
                        'custo_total': qtd_caixas * row['custo_ytd'],  # CAIXAS × R$/CAIXA
                        'margem_total': qtd_caixas * row['margem_unitaria'],  # CAIXAS × R$/CAIXA
                        'tem_pedido': tem_pedido,  # SKU tem pedido
                        'sku_restrito': sku_restrito,  # SKU esta na lista de restritos
                        'tem_demanda_historica': tem_demanda_historica,  # SKU tem historico de demanda
                        'custo_medio_classe': custo_medio_classe  # Custo foi calculado usando média da classe
                    })
        
        # Resultados dos pedidos atendidos (GARANTIDOS - valores fixos)
        # Nota: skus_com_pedido já foi definido acima com tipos int
        pedidos_garantidos_por_sku = self.dados.get('pedidos_garantidos_por_sku', {})
        
        if len(df_pedidos_sku) > 0:
            for _, row_pedido in df_pedidos_sku.iterrows():
                item = row_pedido['item']
                # Usar quantidade garantida (valor fixo)
                if item in pedidos_garantidos_por_sku:
                    qtd_atendida = pedidos_garantidos_por_sku[item]  # Valor fixo garantido
                    if qtd_atendida > 0.01:
                        # Buscar dados do item
                        row_base = df_base[df_base['item'] == item]
                        if len(row_base) > 0:
                            # SKU normal: usar dados do df_base
                            row = row_base.iloc[0]
                            producao_total_classe = float(row['producao_total'])
                            limite_pedido = row_pedido['quantidade_total_pedida']
                            quantidade_restricao = min(limite_pedido, producao_total_classe)
                            tipo_restricao = 'PEDIDO' if limite_pedido <= producao_total_classe else 'PRODUCAO_CLASSE'
                            classe = row['classe']
                            # Garantir que custo_medio_classe está presente (pode não estar se SKU tem custo real)
                            if 'custo_medio_classe' not in row:
                                row['custo_medio_classe'] = False
                        else:
                            # SKU restrito: buscar classe via df_classes e producao via df_producao
                            df_classes = self.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
                            df_producao = self.dados.get('producao', pd.DataFrame(columns=['classe', 'producao_total']))
                            producao_por_classe = df_producao.set_index('classe')['producao_total'].to_dict() if len(df_producao) > 0 else {}
                            
                            classe_sku = df_classes[df_classes['item'] == item]['classe'].values
                            if len(classe_sku) > 0:
                                classe = classe_sku[0]
                                producao_total_classe = producao_por_classe.get(classe, 0.0)
                                limite_pedido = row_pedido['quantidade_total_pedida']
                                quantidade_restricao = min(limite_pedido, producao_total_classe)
                                tipo_restricao = 'PEDIDO' if limite_pedido <= producao_total_classe else 'PRODUCAO_CLASSE'
                                
                                # Buscar preco/custo/margem medio da classe para valores financeiros corretos
                                # O codigo abaixo espera um objeto 'row' com colunas especificas.
                                # Se SKU e restrito, nao esta em df_base, entao criamos um 'row fake'
                                # com valores medios da classe para calcular receita/custo/margem corretamente.
                                item_ids_da_classe = df_base[df_base['classe'] == classe]
                                if len(item_ids_da_classe) > 0:
                                    # Usar media de preco/custo/margem da classe
                                    preco_medio = item_ids_da_classe['preco'].mean()
                                    custo_medio = item_ids_da_classe['custo_ytd'].mean()
                                    margem_media = item_ids_da_classe['margem_unitaria'].mean()
                                    qtd_ovos_por_caixa_media = item_ids_da_classe['qtd_ovos_por_caixa'].mean()
                                else:
                                    # Classe sem item_ids no df_base (muito raro)
                                    # Tentar usar valores médios de df_custos ou df_precos
                                    df_custos_orig = self.dados.get('custos', pd.DataFrame(columns=['item', 'custo_ytd']))
                                    df_precos_orig = self.dados.get('precos', pd.DataFrame(columns=['item_id', 'preco', 'qtd_ovos_por_caixa']))
                                    df_classes_orig = self.dados.get('classes', pd.DataFrame(columns=['item', 'classe']))
                                    
                                    # Buscar SKUs da classe em df_classes e depois custos desses SKUs
                                    if len(df_classes_orig) > 0:
                                        skus_da_classe = set(df_classes_orig[df_classes_orig['classe'] == classe]['item'].unique())
                                        if len(skus_da_classe) > 0 and len(df_custos_orig) > 0:
                                            custos_da_classe = df_custos_orig[df_custos_orig['item'].isin(skus_da_classe)]
                                            if len(custos_da_classe) > 0:
                                                custo_medio = custos_da_classe['custo_ytd'].mean()
                                            else:
                                                custo_medio = df_custos_orig['custo_ytd'].mean() if 'custo_ytd' in df_custos_orig.columns else 132.82
                                        else:
                                            custo_medio = df_custos_orig['custo_ytd'].mean() if len(df_custos_orig) > 0 and 'custo_ytd' in df_custos_orig.columns else 132.82
                                    else:
                                        custo_medio = df_custos_orig['custo_ytd'].mean() if len(df_custos_orig) > 0 and 'custo_ytd' in df_custos_orig.columns else 132.82
                                    
                                    # Buscar preços da classe
                                    if len(df_precos_orig) > 0 and len(df_classes_orig) > 0:
                                        # Buscar item_ids de SKUs da classe
                                        skus_da_classe = set(df_classes_orig[df_classes_orig['classe'] == classe]['item'].unique())
                                        if len(skus_da_classe) > 0:
                                            # Extrair item de item_id (formato: "item_embalagem")
                                            df_precos_orig['item'] = df_precos_orig['item_id'].str.split('_').str[0].astype(int)
                                            precos_da_classe = df_precos_orig[df_precos_orig['item'].isin(skus_da_classe)]
                                            if len(precos_da_classe) > 0:
                                                preco_medio = precos_da_classe['preco'].mean()
                                                qtd_ovos_por_caixa_media = precos_da_classe['qtd_ovos_por_caixa'].mean() if 'qtd_ovos_por_caixa' in precos_da_classe.columns else 360
                                            else:
                                                preco_medio = df_precos_orig['preco'].mean() if 'preco' in df_precos_orig.columns else 178.69
                                                qtd_ovos_por_caixa_media = df_precos_orig['qtd_ovos_por_caixa'].mean() if 'qtd_ovos_por_caixa' in df_precos_orig.columns else 360
                                        else:
                                            preco_medio = df_precos_orig['preco'].mean() if 'preco' in df_precos_orig.columns else 178.69
                                            qtd_ovos_por_caixa_media = df_precos_orig['qtd_ovos_por_caixa'].mean() if 'qtd_ovos_por_caixa' in df_precos_orig.columns else 360
                                    else:
                                        preco_medio = 178.69
                                        qtd_ovos_por_caixa_media = 360
                                    
                                    margem_media = preco_medio - custo_medio
                                    
                                    self.logger.warning(f"  Classe {classe} sem item_ids no df_base - usando valores médios: custo={custo_medio:.2f}, preço={preco_medio:.2f}")
                                
                                # Criar row fake para compatibilidade com valores da classe
                                # O codigo abaixo espera um objeto 'row' com colunas especificas.
                                # Se SKU e restrito ou com pedido, nao esta em df_base, entao criamos um 'row fake'
                                # com as colunas necessarias para evitar erros.
                                # Verificar se custo foi calculado usando média da classe
                                # Verificar se SKU tem custos reais (pode ter sido removido de df_base por ter pedido)
                                df_custos = self.dados.get('custos', pd.DataFrame(columns=['item']))
                                skus_com_custos_reais = set(df_custos['item'].unique()) if len(df_custos) > 0 else set()
                                tem_custo_real = item in skus_com_custos_reais
                                
                                # Se tem custo real, usar custo real; senão, usar média da classe
                                if tem_custo_real:
                                    # Buscar custo real do SKU (pode ter múltiplas embalagens, usar primeira)
                                    custos_sku = df_custos[df_custos['item'] == item]
                                    if len(custos_sku) > 0:
                                        custo_real = custos_sku.iloc[0]['custo_ytd']
                                        # Buscar preço real também se disponível
                                        df_precos = self.dados.get('precos', pd.DataFrame(columns=['item_id', 'preco']))
                                        precos_sku = df_precos[df_precos['item_id'].isin(custos_sku['item_id'].values)]
                                        if len(precos_sku) > 0:
                                            preco_real = precos_sku.iloc[0]['preco']
                                            margem_real = preco_real - custo_real
                                        else:
                                            preco_real = preco_medio
                                            margem_real = preco_real - custo_real
                                    else:
                                        custo_real = custo_medio
                                        preco_real = preco_medio
                                        margem_real = margem_media
                                    
                                    custo_medio_classe_flag = False  # Tem custo real
                                    custo_usar = custo_real
                                    preco_usar = preco_real
                                    margem_usar = margem_real
                                else:
                                    # Não tem custo real, usar média da classe
                                    custo_medio_classe_flag = True  # Custo foi calculado usando média
                                    custo_usar = custo_medio
                                    preco_usar = preco_medio
                                    margem_usar = margem_media
                                
                                row = pd.Series({
                                    'classe': classe,
                                    'producao_total': producao_total_classe,
                                    'preco': preco_usar,
                                    'custo_ytd': custo_usar,
                                    'margem_unitaria': margem_usar,
                                    'qtd_ovos_por_caixa': qtd_ovos_por_caixa_media,
                                    'custo_medio_classe': custo_medio_classe_flag  # Flag indicando se custo foi calculado
                                })
                            else:
                                continue  # SKU nao encontrado, pular
                            # Converter quantidade de ovos para caixas para calculos financeiros
                            # qtd_atendida esta em OVOS, preco/custo/margem estao em R$/CAIXA
                            # Usar primeira embalagem disponivel do item para conversao
                            qtd_ovos_por_caixa_pedido = row['qtd_ovos_por_caixa'] if 'qtd_ovos_por_caixa' in row else df_base[df_base['item'] == item]['qtd_ovos_por_caixa'].iloc[0] if len(df_base[df_base['item'] == item]) > 0 else 360
                            qtd_caixas_pedido = qtd_atendida / qtd_ovos_por_caixa_pedido
                            
                            # Criar item_id para pedidos
                            # Tentar usar primeira embalagem disponivel do SKU em df_base
                            # Se SKU restrito nao esta em df_base, usar embalagem mais comum da classe
                            item_id_pedido = None
                            embalagem_pedido = None
                            
                            # Tentar encontrar embalagem do SKU em df_base
                            item_ids_do_sku = df_base[df_base['item'] == item]
                            if len(item_ids_do_sku) > 0:
                                # SKU normal: usar primeira embalagem disponivel
                                embalagem_pedido = item_ids_do_sku['embalagem'].iloc[0]
                                item_id_pedido = f"{item}_{embalagem_pedido}"
                            else:
                                # SKU restrito ou com pedido (não está em df_base): usar embalagem mais comum da classe
                                item_ids_da_classe = df_base[df_base['classe'] == classe]
                                if len(item_ids_da_classe) > 0:
                                    embalagem_mais_comum = item_ids_da_classe['embalagem'].mode()
                                    if len(embalagem_mais_comum) > 0:
                                        embalagem_pedido = embalagem_mais_comum.iloc[0]
                                        item_id_pedido = f"{item}_{embalagem_pedido}"
                                    else:
                                        # Fallback: usar primeira embalagem da classe
                                        embalagem_pedido = item_ids_da_classe['embalagem'].iloc[0]
                                        item_id_pedido = f"{item}_{embalagem_pedido}"
                                else:
                                    # Classe sem item_ids em df_base (todos os SKUs foram removidos)
                                    # Usar embalagem padrão mais comum (buscar em df_custos original)
                                    df_custos = self.dados.get('custos', pd.DataFrame(columns=['item_id', 'item', 'embalagem']))
                                    if len(df_custos) > 0:
                                        # Buscar embalagem mais comum em toda a base de custos
                                        embalagem_mais_comum_geral = df_custos['embalagem'].mode()
                                        if len(embalagem_mais_comum_geral) > 0:
                                            embalagem_pedido = embalagem_mais_comum_geral.iloc[0]
                                        else:
                                            embalagem_pedido = df_custos['embalagem'].iloc[0] if len(df_custos) > 0 else 'CX12'
                                        item_id_pedido = f"{item}_{embalagem_pedido}"
                                    else:
                                        # Último fallback: usar embalagem padrão
                                        embalagem_pedido = 'CX12'  # Embalagem padrão mais comum
                                        item_id_pedido = f"{item}_{embalagem_pedido}"
                            
                            # Se ainda não conseguimos criar um item_id válido, usar fallback final
                            if item_id_pedido is None or embalagem_pedido is None:
                                embalagem_pedido = 'CX12'  # Embalagem padrão
                                item_id_pedido = f"{item}_{embalagem_pedido}"
                                self.logger.warning(f"  [AVISO] Pedido do SKU {item} usando embalagem padrão (CX12): classe sem item_ids em df_base.")
                            
                            # Mapear informacoes do SKU
                            tem_pedido = True  # Sempre True para pedidos atendidos
                            item_int = int(item) if pd.notna(item) else None
                            sku_restrito = False  # Base já filtrada por A∩B; coluna mantida por compatibilidade
                            tem_demanda_historica = item_int in skus_com_demanda_historica if item_int is not None else False
                            demanda_max_pedido = demanda_por_item.get(item_int) if item_int is not None else None
                            
                            # Verificar se custo foi calculado usando média da classe
                            # Para pandas Series, usar acesso direto em vez de .get()
                            if 'custo_medio_classe' in row.index:
                                custo_medio_classe = bool(row['custo_medio_classe'])
                            else:
                                custo_medio_classe = False
                            
                            resultados.append({
                                'item_id': item_id_pedido,
                                'item': item,
                                'embalagem': embalagem_pedido,  # Usar embalagem real quando possivel
                                'classe': row['classe'],
                                'quantidade': qtd_atendida,  # Manter em ovos
                                'quantidade_caixas': qtd_caixas_pedido,  # Adicionar coluna em caixas
                                'tipo': 'PEDIDO',
                                'tipo_restricao': tipo_restricao,
                                'quantidade_restricao': quantidade_restricao,
                                'limite_demanda_historica': demanda_max_pedido if demanda_max_pedido is not None else None,  # Limite calculado de demanda histórica
                                'producao_total': row['producao_total'],  # Producao da classe
                                'quantidade_pedida': row_pedido['quantidade_total_pedida'],
                                'percentual_atendido': (qtd_atendida / row_pedido['quantidade_total_pedida'] * 100) if row_pedido['quantidade_total_pedida'] > 0 else 0,
                                'preco': row['preco'],
                                'custo_ytd': row['custo_ytd'],
                                'margem_unitaria': row['margem_unitaria'],  # R$/CAIXA
                                'margem_por_ovo': row['margem_unitaria'] / row['qtd_ovos_por_caixa'],  # R$/OVO - métrica otimizada
                                'receita_total': qtd_caixas_pedido * row['preco'],  # CAIXAS × R$/CAIXA
                                'custo_total': qtd_caixas_pedido * row['custo_ytd'],  # CAIXAS × R$/CAIXA
                                'margem_total': qtd_caixas_pedido * row['margem_unitaria'],  # CAIXAS × R$/CAIXA
                                'tem_pedido': tem_pedido,  # SKU tem pedido
                                'sku_restrito': sku_restrito,  # SKU esta na lista de restritos
                                'tem_demanda_historica': tem_demanda_historica,  # SKU tem historico de demanda
                                'custo_medio_classe': custo_medio_classe  # Custo foi calculado usando média da classe
                            })
        
        self.resultado = pd.DataFrame(resultados)
        
        # Garantir que coluna 'classe' existe mesmo se resultado estiver vazio
        # (necessario para evitar erro no groupby('classe') em salvar_resultados)
        if len(self.resultado) == 0:
            self.resultado = pd.DataFrame(columns=['item_id', 'item', 'embalagem', 'classe', 'quantidade', 'quantidade_caixas', 'tipo', 
                                                   'tipo_restricao', 'quantidade_restricao', 'limite_demanda_historica',
                                                   'producao_total', 'producao_disponivel', 'preco', 
                                                   'custo_ytd', 'margem_unitaria', 'margem_por_ovo', 'receita_total', 
                                                   'custo_total', 'margem_total', 'tem_pedido', 
                                                   'sku_restrito', 'tem_demanda_historica', 'custo_medio_classe'])
        
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
        # O baseline assume distribuicao uniforme entre os item_id da classe.
        # Quando considerar_demanda_historica=true, o baseline deve usar o MESMO volume
        # alocado que a solucao otimizada (por classe), senao comparamos volume total
        # diferente (producao 100% vs volume limitado pela demanda) e o ganho fica distorcido.
        considerar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        df_producao = self.dados['producao']
        margem_baseline = 0.0
        custo_baseline = 0.0
        
        # Volume alocado por classe (da solucao otimizada), para baseline com demanda historica
        if considerar_demanda and 'classe' in self.resultado.columns and 'quantidade' in self.resultado.columns:
            qtd_alocada_por_classe = self.resultado.groupby('classe')['quantidade'].sum()
        else:
            qtd_alocada_por_classe = None
        
        for _, row in df_producao.iterrows():
            classe = row['classe']
            # Com demanda historica: baseline = mesmo volume alocado (distribuicao uniforme)
            # Sem demanda historica: baseline = producao total da classe (distribuicao uniforme)
            if qtd_alocada_por_classe is not None and classe in qtd_alocada_por_classe.index:
                qtd_producao = float(qtd_alocada_por_classe[classe])
            else:
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
        if considerar_demanda:
            self.logger.info("  (Baseline = mesmo volume alocado, distribuicao uniforme por classe)")
        
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
            
            # Aba 4: Pedidos Ignorados (se houver)
            pedidos_ignorados = self.dados.get('pedidos_ignorados', [])
            if len(pedidos_ignorados) > 0:
                df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
                df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_total_pedida', ascending=False)
                df_pedidos_ignorados.to_excel(writer, sheet_name='Pedidos Ignorados', index=False)
        
        # Salvar resumo por classe separado (para compatibilidade)
        arquivo_resumo_xlsx = output_dir / f'resumo_por_classe_{modo_sufixo}_{timestamp}.xlsx'
        resumo_classe.to_excel(arquivo_resumo_xlsx, index=False, engine='openpyxl')
        
        # Salvar pedidos ignorados (se houver)
        pedidos_ignorados = self.dados.get('pedidos_ignorados', [])
        if len(pedidos_ignorados) > 0:
            df_pedidos_ignorados = pd.DataFrame(pedidos_ignorados)
            df_pedidos_ignorados = df_pedidos_ignorados.sort_values('quantidade_total_pedida', ascending=False)
            arquivo_pedidos_ignorados_csv = output_dir / f'pedidos_ignorados_{modo_sufixo}_{timestamp}.csv'
            arquivo_pedidos_ignorados_xlsx = output_dir / f'pedidos_ignorados_{modo_sufixo}_{timestamp}.xlsx'
            df_pedidos_ignorados.to_csv(arquivo_pedidos_ignorados_csv, index=False, encoding='utf-8')
            df_pedidos_ignorados.to_excel(arquivo_pedidos_ignorados_xlsx, index=False, engine='openpyxl')
        
        self.logger.info(f"\n[OK] Resultados salvos em {output_dir}/")
        self.logger.info(f"  CSV:")
        self.logger.info(f"    - {arquivo_resultado_csv.name}")
        self.logger.info(f"    - {arquivo_resumo_csv.name}")
        num_abas = 4 if len(pedidos_ignorados) > 0 else 3
        self.logger.info(f"  Excel:")
        self.logger.info(f"    - {arquivo_resultado_xlsx.name} (com {num_abas} abas: Detalhado, Resumo, Estatisticas" + (", Pedidos Ignorados" if len(pedidos_ignorados) > 0 else "") + ")")
        self.logger.info(f"    - {arquivo_resumo_xlsx.name}")
        if len(pedidos_ignorados) > 0:
            self.logger.info(f"    - {arquivo_pedidos_ignorados_csv.name}")
            self.logger.info(f"    - {arquivo_pedidos_ignorados_xlsx.name}")
    
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
        
        # Adicionar estatisticas de pedidos ignorados
        pedidos_ignorados = self.dados.get('pedidos_ignorados', [])
        if len(pedidos_ignorados) > 0:
            total_ignorado = sum(p['quantidade_total_pedida'] for p in pedidos_ignorados)
            skus_sem_classe = len([p for p in pedidos_ignorados if 'nao encontrado em classes' in p['motivo']])
            classes_sem_producao = len([p for p in pedidos_ignorados if 'nao tem producao' in p['motivo']])
            estatisticas.append({'Categoria': 'PEDIDOS IGNORADOS', 'Metrica': 'SKUs Ignorados', 'Valor': f'{len(pedidos_ignorados)}', 'Unidade': 'SKUs'})
            estatisticas.append({'Categoria': 'PEDIDOS IGNORADOS', 'Metrica': 'Quantidade Ignorada', 'Valor': f'{total_ignorado:,.0f}', 'Unidade': 'unidades'})
            estatisticas.append({'Categoria': 'PEDIDOS IGNORADOS', 'Metrica': 'SKUs sem Classe', 'Valor': f'{skus_sem_classe}', 'Unidade': 'SKUs'})
            estatisticas.append({'Categoria': 'PEDIDOS IGNORADOS', 'Metrica': 'Classes sem Producao', 'Valor': f'{classes_sem_producao}', 'Unidade': 'SKUs'})
        
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
