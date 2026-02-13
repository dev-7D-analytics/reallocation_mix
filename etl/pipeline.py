"""
ETL Pipeline para o Modelo de Otimização de Mix.

Este módulo é responsável por:
1. Carregar dados de múltiplas fontes (produção, custos, preços, etc.)
2. Transformar e validar os dados
3. Produzir o DataFrame `base_otimizacao` que é o contrato com o modelo de otimização

O modelo de otimização NÃO deve conhecer detalhes de como os dados são carregados.
Ele apenas recebe o DataFrame pronto.

Autor: Romulo Brito
Data: 2025-01-30
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, Optional, Tuple
import logging
from dataclasses import dataclass


@dataclass
class DadosCarregados:
    """Container para todos os dados carregados pelo ETL."""
    producao: pd.DataFrame
    classes: pd.DataFrame
    pedidos: pd.DataFrame
    pedidos_por_sku: pd.DataFrame
    precos: pd.DataFrame
    custos: pd.DataFrame
    demanda_historica: pd.DataFrame
    skus_restritos: list


@dataclass
class ResultadoETL:
    """Resultado do pipeline ETL - contrato com o modelo de otimização."""
    base_otimizacao: pd.DataFrame
    producao_por_classe: pd.Series
    producao_excedente_por_classe: Dict[str, float]
    pedidos_garantidos_por_sku: Dict[int, float]
    pedidos_garantidos: pd.DataFrame
    pedidos_ignorados: pd.DataFrame
    usar_apenas_excedente: bool
    atender_pedidos: bool
    dados_brutos: DadosCarregados  # Para auditoria/debug
    base_outros: Optional[pd.DataFrame] = None  # Itens classe OUTROS (não otimizados; aparecem no output com alocação 0)


class ETLPipeline:
    """
    Pipeline de ETL que carrega e transforma dados para o modelo de otimização.
    
    Uso:
        etl = ETLPipeline(config)
        resultado = etl.executar()
        df_base = resultado.base_otimizacao
    """
    
    def __init__(self, config: Dict, logger: Optional[logging.Logger] = None):
        """
        Inicializa o pipeline ETL.
        
        Args:
            config: Dicionário de configuração (carregado do config.yaml)
            logger: Logger opcional. Se não fornecido, cria um padrão.
        """
        self.config = config
        self.logger = logger or self._criar_logger()
        self._dados: Optional[DadosCarregados] = None
    
    def _criar_logger(self) -> logging.Logger:
        """Cria logger padrão se não fornecido."""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger
    
    def executar(self) -> ResultadoETL:
        """
        Executa o pipeline ETL completo.
        
        Returns:
            ResultadoETL contendo:
            - base_otimizacao: DataFrame pronto para o modelo
            - Metadados adicionais (produção por classe, pedidos, etc.)
        """
        self.logger.info("\n" + "="*80)
        self.logger.info("ETAPA 1: CARREGAMENTO DE DADOS (ETL)")
        self.logger.info("="*80)
        
        # 1. Carregar dados brutos
        dados = self._carregar_todos_dados()
        self._dados = dados
        
        # 2. Preparar base de otimização (transformações)
        resultado = self._preparar_base_otimizacao(dados)
        
        self.logger.info("\n[OK] ETL concluído com sucesso!")
        
        return resultado
    
    def _carregar_todos_dados(self) -> DadosCarregados:
        """Carrega todos os dados necessários."""
        producao = self._carregar_producao()
        classes = self._carregar_classes()
        pedidos, pedidos_por_sku = self._carregar_pedidos()
        precos = self._carregar_precos()
        custos = self._carregar_custos()
        demanda_historica = self._carregar_demanda_historica()
        skus_restritos = self._carregar_skus_restritos()
        
        return DadosCarregados(
            producao=producao,
            classes=classes,
            pedidos=pedidos,
            pedidos_por_sku=pedidos_por_sku,
            precos=precos,
            custos=custos,
            demanda_historica=demanda_historica,
            skus_restritos=skus_restritos
        )
    
    def _carregar_producao(self) -> pd.DataFrame:
        """Carrega produção por classe de produtos."""
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
        
        # Log distribuição
        self.logger.info("\n  Distribuicao por classe (top 10):")
        for _, row in df_producao_agg.nlargest(10, 'producao_total').iterrows():
            self.logger.info(f"    {row['classe']}: {row['producao_total']:,.0f} unidades")
        
        return df_producao_agg
    
    def _carregar_classes(self) -> pd.DataFrame:
        """Carrega mapeamento SKU -> Classe."""
        self.logger.info("\n[2/8] Carregando classificacao de SKUs...")
        
        path = Path(self.config['paths'].get('classes', 'inputs/classes.csv'))
        
        if not path.exists():
            raise FileNotFoundError(f"Arquivo de classes nao encontrado: {path}")
        
        # Suporta CSV e Excel
        if path.suffix in ['.xlsx', '.xls']:
            df_classes = pd.read_excel(path)
        else:
            df_classes = pd.read_csv(path)
        
        # Padronizar nomes de colunas
        col_mapping = {}
        for col in df_classes.columns:
            col_lower = col.lower()
            if col_lower == 'item' or 'sku' in col_lower or 'codigo' in col_lower:
                col_mapping[col] = 'item'
            elif 'classe' in col_lower:
                col_mapping[col] = 'classe'
        
        df_classes = df_classes.rename(columns=col_mapping)
        
        if 'item' not in df_classes.columns or 'classe' not in df_classes.columns:
            raise ValueError(f"Arquivo de classes deve conter colunas de item e classe. Encontradas: {list(df_classes.columns)}")
        
        df_classes['item'] = pd.to_numeric(df_classes['item'], errors='coerce')
        df_classes = df_classes[df_classes['item'].notna()]
        df_classes['item'] = df_classes['item'].astype(int)
        
        self.logger.info(f"  SKUs com classe: {len(df_classes)}")
        self.logger.info(f"  Classes unicas: {df_classes['classe'].nunique()}")
        
        return df_classes[['item', 'classe']].drop_duplicates()
    
    def _carregar_pedidos(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Carrega pedidos de clientes."""
        self.logger.info("\n[3/8] Carregando pedidos de clientes...")
        
        path = Path(self.config['paths'].get('pedidos', 'inputs/pedidos.csv'))
        
        if not path.exists():
            self.logger.warning(f"  Arquivo de pedidos nao encontrado: {path}")
            df_vazio = pd.DataFrame(columns=['cod_cliente', 'item', 'quantidade_pedida'])
            return df_vazio, pd.DataFrame(columns=['item', 'quantidade_total_pedida'])
        
        # Suporta CSV e Excel
        if path.suffix in ['.xlsx', '.xls']:
            df_pedidos = pd.read_excel(path)
        else:
            df_pedidos = pd.read_csv(path)
        
        # Padronizar colunas
        col_mapping = {}
        for col in df_pedidos.columns:
            col_lower = col.lower()
            if 'cliente' in col_lower or 'estabelecimento' in col_lower:
                col_mapping[col] = 'cod_cliente'
            elif col_lower == 'item' or 'sku' in col_lower:
                col_mapping[col] = 'item'
            elif 'qtd' in col_lower or 'quantidade' in col_lower:
                col_mapping[col] = 'quantidade_pedida'
        
        df_pedidos = df_pedidos.rename(columns=col_mapping)
        
        df_pedidos['item'] = pd.to_numeric(df_pedidos['item'], errors='coerce')
        df_pedidos = df_pedidos[df_pedidos['item'].notna()]
        df_pedidos['item'] = df_pedidos['item'].astype(int)
        
        # Agregar por SKU
        pedidos_por_sku = df_pedidos.groupby('item')['quantidade_pedida'].sum().reset_index()
        pedidos_por_sku.columns = ['item', 'quantidade_total_pedida']
        
        self.logger.info(f"  Pedidos carregados: {len(df_pedidos):,}")
        self.logger.info(f"  Clientes unicos: {df_pedidos['cod_cliente'].nunique() if 'cod_cliente' in df_pedidos.columns else 0}")
        self.logger.info(f"  SKUs com pedidos: {pedidos_por_sku['item'].nunique()}")
        self.logger.info(f"  Quantidade total pedida: {pedidos_por_sku['quantidade_total_pedida'].sum():,.0f} unidades")
        
        return df_pedidos, pedidos_por_sku
    
    def _carregar_precos(self) -> pd.DataFrame:
        """Carrega preços por item (SKU).
        
        O arquivo de preços precisa ter apenas as colunas 'item' e 'preco'.
        A coluna 'embalagem' é opcional (backward-compatible); se presente,
        será usada para criar 'item_id', mas o merge principal usa 'item'.
        """
        self.logger.info("\n[4/8] Carregando precos...")
        
        path = Path(self.config['paths'].get('precos', 'inputs/precos.csv'))
        
        if not path.exists():
            self.logger.warning(f"  Arquivo de precos nao encontrado: {path}")
            return pd.DataFrame(columns=['item', 'preco'])
        
        # Suporta CSV e Parquet
        if path.suffix == '.parquet':
            df_precos = pd.read_parquet(path)
        else:
            df_precos = pd.read_csv(path)
        
        # Garantir coluna 'item' numérica
        if 'item' in df_precos.columns:
            df_precos['item'] = pd.to_numeric(df_precos['item'], errors='coerce')
            df_precos = df_precos[df_precos['item'].notna()].copy()
            df_precos['item'] = df_precos['item'].astype(int)
        
        # Criar item_id se embalagem estiver presente (backward-compatible)
        if 'item' in df_precos.columns and 'embalagem' in df_precos.columns:
            df_precos['item_id'] = df_precos['item'].astype(str) + '_' + df_precos['embalagem']
        
        # Detectar coluna de preço
        if 'preco' not in df_precos.columns:
            for col in df_precos.columns:
                if 'preco' in col.lower() or 'price' in col.lower():
                    df_precos['preco'] = df_precos[col]
                    break
        
        n_skus = df_precos['item'].nunique() if 'item' in df_precos.columns else 0
        self.logger.info(f"  SKUs com preco: {n_skus}")
        self.logger.info(f"  Preco medio: R$ {df_precos['preco'].mean():.2f}" if 'preco' in df_precos.columns else "  Preco: N/A")
        
        return df_precos
    
    def _carregar_custos(self) -> pd.DataFrame:
        """Carrega custos por item (SKU).
        
        Lê o CSV pré-calculado (gerado por gerar_custos_sku.py).
        O arquivo precisa ter ao menos 'item' e 'custo_ytd'.
        A coluna 'embalagem' é opcional; se ausente, é recuperada do
        arquivo de compatibilidade SKU/embalagem.
        """
        self.logger.info("\n[5/8] Carregando custos...")
        
        # Prioridade: custos_calculados (CSV editável) > custos (Parquet bruto)
        path_csv = Path(self.config['paths'].get('custos_calculados', 'inputs/custos_sku.csv'))
        path_parquet = Path(self.config['paths'].get('custos', 'inputs/custos.parquet'))
        
        if path_csv.exists():
            path = path_csv
        elif path_parquet.exists() and path_parquet.suffix == '.parquet':
            self.logger.warning(f"  CSV de custos não encontrado ({path_csv}). Usando Parquet bruto.")
            self.logger.warning(f"  Execute 'python gerar_custos_sku.py' para gerar o CSV editável.")
            # Fallback: processar Parquet inline (lógica legada)
            return self._carregar_custos_parquet(path_parquet)
        else:
            raise FileNotFoundError(
                f"Nenhum arquivo de custos encontrado.\n"
                f"  Esperado CSV: {path_csv}\n"
                f"  Ou Parquet: {path_parquet}\n"
                f"  Execute 'python gerar_custos_sku.py' para gerar o CSV."
            )
        
        self.logger.info(f"  Arquivo: {path}")
        df_custo = pd.read_csv(path)
        
        # Garantir coluna 'item' numérica
        df_custo['item'] = pd.to_numeric(df_custo['item'], errors='coerce')
        df_custo = df_custo[df_custo['item'].notna()].copy()
        df_custo['item'] = df_custo['item'].astype(int)
        
        # Detectar coluna de custo
        if 'custo_ytd' not in df_custo.columns:
            for col in df_custo.columns:
                if 'custo' in col.lower() or 'cost' in col.lower():
                    df_custo['custo_ytd'] = df_custo[col]
                    break
        
        # Filtrar custos válidos
        df_custo = df_custo[
            df_custo['custo_ytd'].notna() &
            (df_custo['custo_ytd'] > 0)
        ].copy()
        
        # Se embalagem ausente, recuperar do arquivo de compatibilidade
        if 'embalagem' not in df_custo.columns or df_custo['embalagem'].isna().all():
            self.logger.info("  Embalagem não encontrada no CSV de custos. Recuperando da compatibilidade...")
            path_compat = Path('inputs/compatibilidade_sku_embalagem.csv')
            if path_compat.exists():
                compat = pd.read_csv(path_compat)
                compat['item'] = pd.to_numeric(compat['item'], errors='coerce').astype('Int64')
                emb_map = compat.drop_duplicates('item').set_index('item')['embalagem'].to_dict()
                df_custo['embalagem'] = df_custo['item'].map(emb_map)
            else:
                self.logger.warning(f"  Arquivo de compatibilidade não encontrado: {path_compat}")
        
        # Preencher embalagens faltantes de linhas editadas manualmente
        if 'embalagem' in df_custo.columns:
            sem_emb = df_custo['embalagem'].isna()
            if sem_emb.any():
                path_compat = Path('inputs/compatibilidade_sku_embalagem.csv')
                if path_compat.exists():
                    compat = pd.read_csv(path_compat)
                    compat['item'] = pd.to_numeric(compat['item'], errors='coerce').astype('Int64')
                    emb_map = compat.drop_duplicates('item').set_index('item')['embalagem'].to_dict()
                    df_custo.loc[sem_emb, 'embalagem'] = df_custo.loc[sem_emb, 'item'].map(emb_map)
                n_sem = df_custo['embalagem'].isna().sum()
                if n_sem > 0:
                    self.logger.warning(f"  {n_sem} SKUs sem embalagem (não encontrados na compatibilidade)")
        
        # Criar item_id
        if 'embalagem' in df_custo.columns:
            mask_com_emb = df_custo['embalagem'].notna()
            df_custo.loc[mask_com_emb, 'item_id'] = (
                df_custo.loc[mask_com_emb, 'item'].astype(str) + '_' + df_custo.loc[mask_com_emb, 'embalagem']
            )
        
        if 'item_id' not in df_custo.columns:
            df_custo['item_id'] = df_custo['item'].astype(str)
        
        n_skus = df_custo['item'].nunique()
        self.logger.info(f"  SKUs com custo: {n_skus}")
        self.logger.info(f"  Custo medio: R$ {df_custo['custo_ytd'].mean():.2f}")
        
        return df_custo
    
    def _carregar_custos_parquet(self, path: Path) -> pd.DataFrame:
        """Fallback: processa custos diretamente do Parquet bruto (lógica legada).
        
        Usado apenas quando inputs/custos_sku.csv não existe.
        Para gerar o CSV, execute: python gerar_custos_sku.py
        """
        from extrair_compatibilidade_embalagem import extrair_embalagem_descricao
        
        colunas_necessarias = ['Estab', 'item', 'MÊS', 'ano', 'Custo Médio', 'Quantidade', 'UF', 'Descrição do item']
        self.logger.info(f"  Lendo arquivo Parquet (apenas colunas necessarias)...")
        
        df_custo = pd.read_parquet(path, columns=colunas_necessarias, engine='pyarrow')
        
        dados_config = self.config.get('dados', {})
        mes_custo = dados_config.get('mes_custo', 11)
        ano_custo = dados_config.get('ano_custo', 2025)
        estab_custo = dados_config.get('estab_custo', 100)
        meses_janela = dados_config.get('meses_janela_custo', 6)
        
        periodos = []
        for i in range(meses_janela):
            mes = mes_custo - i
            ano = ano_custo
            while mes <= 0:
                mes += 12
                ano -= 1
            periodos.append((ano, mes))
        
        self.logger.info(f"  Aplicando filtros: Estab={estab_custo}, janela de {meses_janela} meses...")
        self.logger.info(f"  Períodos incluídos: {', '.join([f'{a}-{m:02d}' for a, m in periodos])}")
        
        df_custo = df_custo[df_custo['Estab'] == estab_custo].copy()
        df_custo['_periodo'] = list(zip(df_custo['ano'], df_custo['MÊS']))
        df_custo = df_custo[df_custo['_periodo'].isin(periodos)].copy()
        df_custo = df_custo.drop(columns=['_periodo'])
        
        self.logger.info(f"  Registros após filtros de periodo: {len(df_custo):,}")
        
        antes_uf = len(df_custo)
        df_custo = df_custo[df_custo['UF'] != 'EX'].copy()
        if antes_uf - len(df_custo) > 0:
            self.logger.info(f"  Registros de exportacao removidos: {antes_uf - len(df_custo):,}")
        
        df_custo['Custo Médio'] = pd.to_numeric(df_custo['Custo Médio'], errors='coerce')
        df_custo['Quantidade'] = pd.to_numeric(df_custo['Quantidade'], errors='coerce')
        df_custo = df_custo[(df_custo['Quantidade'] > 0) & (df_custo['Custo Médio'].notna())].copy()
        
        df_custo['custo_total'] = df_custo['Custo Médio']
        df_custo['embalagem'] = df_custo['Descrição do item'].apply(extrair_embalagem_descricao)
        df_custo['item'] = pd.to_numeric(df_custo['item'], errors='coerce')
        df_custo = df_custo[df_custo['item'].notna() & df_custo['embalagem'].notna()].copy()
        df_custo['item_id'] = df_custo['item'].astype(int).astype(str) + '_' + df_custo['embalagem']
        df_custo['ano_mes'] = df_custo['ano'].astype(str) + '-' + df_custo['MÊS'].astype(str).str.zfill(2)
        
        self.logger.info(f"  Agregando por item_id (média ponderada)...")
        
        custos_mes = df_custo.groupby(['item', 'embalagem', 'ano_mes']).agg({
            'Quantidade': 'sum', 'custo_total': 'sum'
        }).reset_index()
        
        df_custo_agg = custos_mes.groupby(['item', 'embalagem']).agg({
            'Quantidade': 'sum', 'custo_total': 'sum'
        }).reset_index()
        
        df_custo_agg['custo_ytd'] = df_custo_agg['custo_total'] / df_custo_agg['Quantidade']
        df_custo_agg['item_id'] = df_custo_agg['item'].astype(int).astype(str) + '_' + df_custo_agg['embalagem']
        
        df_custo = df_custo_agg[['item_id', 'item', 'embalagem', 'custo_ytd']]
        df_custo = df_custo[df_custo['custo_ytd'].notna() & (df_custo['custo_ytd'] > 0)].copy()
        
        self.logger.info(f"  Itens unicos (item_id) com custo: {df_custo['item_id'].nunique()}")
        self.logger.info(f"  Custo medio: R$ {df_custo['custo_ytd'].mean():.2f}")
        
        return df_custo
    
    def _aplicar_correcao_estabelecimento(self, df_fat: pd.DataFrame) -> pd.DataFrame:
        """
        Aplica correção de estabelecimento na base de faturamento.
        
        Alguns clientes foram registrados no Estab 100 mas na verdade são atendidos
        por outros estabelecimentos. O arquivo 'ESTAB CORRIGIDO.xlsx' contém o 
        mapeamento Cliente -> Estab Padrao correto.
        
        Cria uma nova coluna 'Estab_Corrigido' preservando o 'Estab' original.
        """
        # Verificar se existe arquivo de correção
        path_correcao = Path(self.config['paths'].get('estab_corrigido', 'inputs/ESTAB CORRIGIDO.xlsx'))
        
        if not path_correcao.exists():
            self.logger.warning(f"  Arquivo de correção de estabelecimento não encontrado: {path_correcao}")
            # Se não existe arquivo, apenas copia Estab para Estab_Corrigido
            df_fat['Estab_Corrigido'] = df_fat['Estab']
            return df_fat
        
        # Verificar se existe coluna de cliente na base de faturamento
        col_cliente = 'Cod.Emitente' if 'Cod.Emitente' in df_fat.columns else None
        
        if col_cliente is None:
            self.logger.warning("  Coluna 'Cod.Emitente' não encontrada - correção de Estab não aplicada")
            df_fat['Estab_Corrigido'] = df_fat['Estab']
            return df_fat
        
        try:
            # Carregar mapeamento Cliente -> Estab Padrao
            df_correcao = pd.read_excel(path_correcao)
            
            if 'Cliente' not in df_correcao.columns or 'Estab Padrao' not in df_correcao.columns:
                self.logger.warning("  Colunas 'Cliente' ou 'Estab Padrao' não encontradas no arquivo de correção")
                df_fat['Estab_Corrigido'] = df_fat['Estab']
                return df_fat
            
            # Criar dicionário de mapeamento Cliente -> Estab Padrao
            mapa_estab = dict(zip(df_correcao['Cliente'], df_correcao['Estab Padrao']))
            
            # Criar coluna Estab_Corrigido
            # Se o cliente está no mapeamento, usa o Estab Padrao; senão, mantém o Estab original
            df_fat['Estab_Corrigido'] = df_fat.apply(
                lambda row: mapa_estab.get(row[col_cliente], row['Estab']),
                axis=1
            )
            
            # Contar quantos registros foram corrigidos
            registros_corrigidos = (df_fat['Estab'] != df_fat['Estab_Corrigido']).sum()
            
            self.logger.info(f"  Correção de estabelecimento aplicada:")
            self.logger.info(f"    - Mapeamento carregado: {len(mapa_estab)} clientes")
            self.logger.info(f"    - Registros corrigidos: {registros_corrigidos:,} de {len(df_fat):,}")
            
            if registros_corrigidos > 0:
                # Mostrar distribuição das correções
                correcoes = df_fat[df_fat['Estab'] != df_fat['Estab_Corrigido']].groupby(['Estab', 'Estab_Corrigido']).size()
                self.logger.info(f"    - Principais correções:")
                for (estab_orig, estab_novo), count in correcoes.head(5).items():
                    self.logger.info(f"      {estab_orig} -> {estab_novo}: {count:,} registros")
            
        except Exception as e:
            self.logger.warning(f"  Erro ao aplicar correção de estabelecimento: {e}")
            df_fat['Estab_Corrigido'] = df_fat['Estab']
        
        return df_fat
    
    def _carregar_demanda_historica(self) -> pd.DataFrame:
        """Carrega demanda histórica por SKU."""
        self.logger.info("\n[6/8] Carregando demanda historica...")
        
        usar_demanda = self.config.get('modelo', {}).get('considerar_demanda_historica', False)
        
        if not usar_demanda:
            self.logger.info("  Demanda historica: Desabilitada")
            return pd.DataFrame(columns=['item', 'demanda_max'])
        
        path = Path(self.config['paths'].get('faturamento', 'inputs/faturamento.parquet'))
        
        if not path.exists():
            self.logger.warning(f"  Arquivo de faturamento nao encontrado: {path}")
            return pd.DataFrame(columns=['item', 'demanda_max'])
        
        # Importar funções necessárias
        from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem
        
        df_fat = pd.read_parquet(path)
        
        # Detectar colunas
        col_item = 'item' if 'item' in df_fat.columns else None
        col_qtd = 'Quantidade' if 'Quantidade' in df_fat.columns else None
        col_data = 'Dt.Emissão' if 'Dt.Emissão' in df_fat.columns else None
        col_desc = 'Descrição do item' if 'Descrição do item' in df_fat.columns else None
        
        if not all([col_item, col_qtd, col_data]):
            self.logger.warning("  Colunas necessárias não encontradas no faturamento")
            return pd.DataFrame(columns=['item', 'demanda_max'])
        
        # Aplicar correção de estabelecimento (alguns clientes foram registrados no Estab errado)
        df_fat = self._aplicar_correcao_estabelecimento(df_fat)
        
        # Filtrar estabelecimentos usando coluna corrigida
        estabs_manter = self.config.get('negocio', {}).get('filtrar_granjas', [])
        if estabs_manter and 'Estab_Corrigido' in df_fat.columns:
            antes = len(df_fat)
            df_fat = df_fat[df_fat['Estab_Corrigido'].astype(str).isin([str(e) for e in estabs_manter])].copy()
            self.logger.info(f"  Mantendo estabelecimentos (corrigido): {estabs_manter}")
            self.logger.info(f"  Registros após filtro: {len(df_fat)} de {antes}")
        
        # A base de faturamento está normalizada para caixas de 360 ovos
        # A coluna 'Quantidade' representa caixas equivalentes de 360 ovos
        # Para obter quantidade em ovos: Quantidade × 360
        # Nota: CONV. P OVO = Quantidade × 360 (verificado em 100% dos registros)
        self.logger.info(f"  Convertendo quantidade para ovos (Quantidade × 360)")
        df_fat[col_qtd] = df_fat[col_qtd] * 360
        
        # Converter data e filtrar período (mesmo critério de custo/preço: mes_ref + janela)
        df_fat[col_data] = pd.to_datetime(df_fat[col_data], errors='coerce')
        df_fat = df_fat[df_fat[col_data].notna()]
        
        dados_config = self.config.get('dados', {})
        mes_ref = dados_config.get('mes_custo', 11)
        ano_ref = dados_config.get('ano_custo', 2025)
        meses_janela = dados_config.get('meses_janela_custo', 6)
        periodos = []
        for i in range(meses_janela):
            mes = mes_ref - i
            ano = ano_ref
            while mes <= 0:
                mes += 12
                ano -= 1
            periodos.append((ano, mes))
        df_fat['_ano'] = df_fat[col_data].dt.year
        df_fat['_mes'] = df_fat[col_data].dt.month
        periodos_set = set(periodos)
        df_fat['_periodo'] = list(zip(df_fat['_ano'], df_fat['_mes']))
        df_fat = df_fat[df_fat['_periodo'].isin(periodos_set)].copy()
        df_fat = df_fat.drop(columns=['_ano', '_mes', '_periodo'])
        self.logger.info(f"  Periodo demanda: janela de {meses_janela} meses ate {ano_ref}-{mes_ref:02d} -> {', '.join([f'{a}-{m:02d}' for a, m in periodos])}")
        
        # Agregar por período (semanal por padrão)
        granularidade = self.config.get('modelo', {}).get('granularidade_demanda', 'S').upper()
        
        if granularidade == 'S':
            df_fat['periodo'] = df_fat[col_data].dt.to_period('W')
        elif granularidade == 'M':
            df_fat['periodo'] = df_fat[col_data].dt.to_period('M')
        else:
            df_fat['periodo'] = df_fat[col_data].dt.date
        
        # Calcular demanda por SKU agregada por período
        df_agregado = df_fat.groupby([col_item, 'periodo'])[col_qtd].sum().reset_index()
        df_agregado.columns = ['item', 'periodo', 'demanda_periodo']
        
        # Tipo de cálculo: maximo, media ou percentil
        tipo_calculo = self.config.get('modelo', {}).get('tipo_calculo_demanda', 'maximo').lower()
        fator = self.config.get('modelo', {}).get('fator_demanda_maxima', 1.2)
        
        if tipo_calculo == 'media':
            # Média dos períodos
            df_demanda = df_agregado.groupby('item')['demanda_periodo'].mean().reset_index()
            df_demanda.columns = ['item', 'demanda_max']
            self.logger.info(f"  Tipo de calculo: MEDIA dos periodos x {fator}")
        elif tipo_calculo == 'percentil':
            # Percentil configurado
            percentil = self.config.get('modelo', {}).get('percentil_demanda', 95)
            df_demanda = df_agregado.groupby('item')['demanda_periodo'].quantile(percentil/100).reset_index()
            df_demanda.columns = ['item', 'demanda_max']
            self.logger.info(f"  Tipo de calculo: PERCENTIL {percentil} x {fator}")
        else:
            # Máximo histórico (padrão)
            df_demanda = df_agregado.groupby('item')['demanda_periodo'].max().reset_index()
            df_demanda.columns = ['item', 'demanda_max']
            self.logger.info(f"  Tipo de calculo: MAXIMO historico x {fator}")
        
        # Aplicar fator de expansão
        df_demanda['demanda_max'] = df_demanda['demanda_max'] * fator
        
        # Calcular volume total histórico por SKU (SEM fator) para proporção do baseline
        # Isso representa o volume REAL vendido no período, usado para calcular proporção
        df_volume_total = df_agregado.groupby('item')['demanda_periodo'].sum().reset_index()
        df_volume_total.columns = ['item', 'volume_historico_total']
        df_demanda = df_demanda.merge(df_volume_total, on='item', how='left')
        
        self.logger.info(f"  SKUs com demanda historica: {len(df_demanda)}")
        self.logger.info(f"  Limite demanda medio: {df_demanda['demanda_max'].mean():,.0f} unidades")
        self.logger.info(f"  Volume historico total medio: {df_demanda['volume_historico_total'].mean():,.0f} unidades")
        
        return df_demanda
    
    def _carregar_skus_restritos(self) -> list:
        """Carrega lista de SKUs permitidos/restritos."""
        self.logger.info("\n[7/8] Carregando SKUs permitidos...")
        
        path = Path(self.config['paths'].get('skus_restritos', 'inputs/skus_restritos.xlsx'))
        
        if not path.exists():
            self.logger.info("  Arquivo de SKUs restritos nao encontrado - todos permitidos")
            return []
        
        try:
            df = pd.read_excel(path)
            total_original = len(df)
            
            # Procurar coluna de item
            col_item = None
            for col in df.columns:
                if 'item' in col.lower() or 'sku' in col.lower() or 'codigo' in col.lower():
                    col_item = col
                    break
            
            if col_item is None:
                col_item = df.columns[0]
            
            # Aplicar filtros de validação
            # 1. STATUS = ATIVO
            if 'STATUS' in df.columns:
                df = df[df['STATUS'] == 'ATIVO']
                self.logger.info(f"  Filtro STATUS='ATIVO': {len(df)} de {total_original}")
            
            # 2. ESTAB = 100
            if 'ESTAB' in df.columns:
                antes = len(df)
                df = df[df['ESTAB'] == 100]
                self.logger.info(f"  Filtro ESTAB=100: {len(df)} de {antes}")
            
            # 3. TIPO diferente de ["Exportação", "GRANEL", "MARCA PROPRIA"]
            tipos_excluidos = ['Exportação', 'GRANEL', 'MARCA PROPRIA']
            if 'TIPO' in df.columns:
                antes = len(df)
                df = df[~df['TIPO'].isin(tipos_excluidos)]
                self.logger.info(f"  Filtro TIPO not in {tipos_excluidos}: {len(df)} de {antes}")
            
            skus = df[col_item].dropna().astype(int).tolist()
            
            self.logger.info(f"  SKUs permitidos (após filtros): {len(skus)} de {total_original} originais")
            
            return skus
        except Exception as e:
            self.logger.warning(f"  Erro ao carregar SKUs restritos: {e}")
            return []
    
    def _preparar_base_otimizacao(self, dados: DadosCarregados) -> ResultadoETL:
        """
        Prepara o DataFrame base_otimizacao que é o contrato com o modelo.
        
        Esta é a transformação principal que combina todos os dados carregados.
        """
        self.logger.info("\n[8/8] Preparando base de otimizacao...")
        
        from extrair_compatibilidade_embalagem import calcular_qtd_embalagem
        
        # Filtrar por SKUs na produção do estabelecimento
        skus_producao = self._obter_skus_producao()
        
        # Aplicar filtro de SKUs permitidos
        if len(dados.skus_restritos) > 0 and len(skus_producao) > 0:
            skus_producao = skus_producao & set(dados.skus_restritos)
            self.logger.info(f"  SKUs após interseção com permitidos: {len(skus_producao)}")
        
        # Classes com produção
        classes_com_producao = set(dados.producao['classe'].unique())
        
        # SKUs com produção (que pertencem a classes com produção)
        skus_com_producao = set(dados.classes[dados.classes['classe'].isin(classes_com_producao)]['item'].unique())
        if len(skus_producao) > 0:
            skus_com_producao = skus_com_producao & skus_producao
        
        # Começar pela base de custos
        df_base = dados.custos[['item_id', 'item', 'embalagem', 'custo_ytd']].copy()
        df_base['item'] = df_base['item'].astype(int)
        
        # Filtrar por SKUs na produção
        if len(skus_producao) > 0:
            df_base = df_base[df_base['item'].isin(skus_producao)].copy()
        
        # Flag de custo médio
        df_base['custo_medio_classe'] = False
        
        # Adicionar classe
        df_base = df_base.merge(dados.classes, on='item', how='inner')
        
        # Filtrar apenas classes com produção
        df_base = df_base[df_base['classe'].isin(classes_com_producao)].copy()
        
        # Calcular ovos por caixa ANTES de usar na agregação
        df_base['qtd_ovos_por_caixa'] = df_base['embalagem'].apply(calcular_qtd_embalagem)
        
        # =====================================================================
        # INCLUIR SKUs SEM CUSTOS: LÓGICA MELHORADA
        # Usar apenas embalagens reais (preço, demanda histórica, ou típica da classe)
        # Ajustar custo proporcionalmente ao tamanho da embalagem
        # =====================================================================
        skus_com_custos = set(df_base['item'].unique())
        skus_sem_custos = skus_com_producao - skus_com_custos
        
        if len(skus_sem_custos) > 0:
            self.logger.info(f"  SKUs sem custos: {len(skus_sem_custos)} (aplicando lógica melhorada)")
            
            # Calcular custo médio por classe e embalagem de referência
            custo_por_classe = df_base.groupby('classe').agg({
                'custo_ytd': 'mean',
                'qtd_ovos_por_caixa': 'mean'  # Embalagem média da classe
            }).to_dict('index')
            
            # Calcular custo médio geral - ALERTAR se usar fallback hardcoded
            if len(df_base) > 0:
                custo_medio_geral = df_base['custo_ytd'].mean()
                ovos_ref_geral = df_base['qtd_ovos_por_caixa'].mean()
                usou_fallback_hardcoded = False
            else:
                custo_medio_geral = 132.82  # FALLBACK HARDCODED
                ovos_ref_geral = 360  # FALLBACK HARDCODED
                usou_fallback_hardcoded = True
                self.logger.warning("=" * 80)
                self.logger.warning("ALERTA: Usando valores HARDCODED pois df_base está vazio!")
                self.logger.warning(f"  - Custo médio geral: R$ {custo_medio_geral:.2f} (HARDCODED)")
                self.logger.warning(f"  - Ovos por caixa referência: {ovos_ref_geral} (HARDCODED)")
                self.logger.warning("=" * 80)
            
            # Criar índices para busca rápida
            precos_por_item = dados.precos.groupby('item')['embalagem'].apply(set).to_dict() if 'embalagem' in dados.precos.columns else {}
            demanda_por_item = dados.demanda_historica.set_index('item')['demanda_max'].to_dict() if len(dados.demanda_historica) > 0 else {}
            
            # Embalagem mais comum por classe (fallback)
            embalagem_comum_classe = df_base.groupby('classe')['embalagem'].agg(
                lambda x: x.value_counts().index[0] if len(x) > 0 else 'CX 12 BJ 30 UN'
            ).to_dict()
            
            linhas_sem_custo = []
            stats = {'preco': 0, 'demanda': 0, 'classe': 0}
            alertas_fallback = []  # Lista de SKUs que usaram fallback
            
            for item in skus_sem_custos:
                classe_sku = dados.classes[dados.classes['item'] == item]['classe'].values
                if len(classe_sku) == 0:
                    continue
                    
                classe = classe_sku[0]
                
                # Obter custo médio da classe e ovos de referência
                if classe in custo_por_classe:
                    custo_ref = custo_por_classe[classe]['custo_ytd']
                    ovos_ref = custo_por_classe[classe]['qtd_ovos_por_caixa']
                else:
                    custo_ref = custo_medio_geral
                    ovos_ref = ovos_ref_geral
                
                if custo_ref == 0 or pd.isna(custo_ref):
                    custo_ref = custo_medio_geral
                if ovos_ref == 0 or pd.isna(ovos_ref):
                    ovos_ref = ovos_ref_geral
                
                # HIERARQUIA DE EMBALAGENS REAIS:
                # 1. Se tem preço → usar embalagens com preço (custo médio da classe)
                embalagens_item = set()
                fonte = None
                
                if item in precos_por_item:
                    embalagens_item = precos_por_item[item]
                    fonte = 'preco'
                    stats['preco'] += 1
                
                # 2. Se tem demanda histórica → usar embalagem mais comum da classe
                elif item in demanda_por_item:
                    embalagens_item = {embalagem_comum_classe.get(classe, 'CX 12 BJ 30 UN')}
                    fonte = 'demanda'
                    stats['demanda'] += 1
                
                # 3. Fallback: usar embalagem mais comum da classe
                else:
                    embalagens_item = {embalagem_comum_classe.get(classe, 'CX 12 BJ 30 UN')}
                    fonte = 'classe'
                    stats['classe'] += 1
                
                # Criar item_ids apenas para embalagens reais
                # Usar custo médio da classe SEM ajuste proporcional (como o original)
                # para manter consistência com os preços
                for embalagem in embalagens_item:
                    ovos_embalagem = calcular_qtd_embalagem(embalagem)
                    if ovos_embalagem is None or ovos_embalagem == 0:
                        continue
                    
                    item_id = f"{item}_{embalagem}"
                    
                    # Determinar origem do custo para rastreabilidade
                    if classe in custo_por_classe and custo_por_classe[classe]['custo_ytd'] > 0:
                        origem_custo = 'custo_medio_classe'
                    else:
                        origem_custo = 'custo_medio_geral'
                        if usou_fallback_hardcoded:
                            origem_custo = 'FALLBACK_HARDCODED'
                            alertas_fallback.append(f"{item_id} (custo={custo_ref:.2f})")
                    
                    linhas_sem_custo.append({
                        'item_id': item_id,
                        'item': item,
                        'embalagem': embalagem,
                        'custo_ytd': custo_ref,
                        'classe': classe,
                        'custo_medio_classe': True,
                        'origem_custo': origem_custo,
                        'origem_embalagem': fonte  # preco, demanda, ou classe
                    })
            
            if linhas_sem_custo:
                df_sem_custo = pd.DataFrame(linhas_sem_custo)
                df_base = pd.concat([df_base, df_sem_custo], ignore_index=True)
                self.logger.info(f"  Adicionados {len(linhas_sem_custo)} item_ids (fonte embalagem: preço={stats['preco']}, demanda={stats['demanda']}, classe={stats['classe']})")
                
                # ALERTAR se usou fallbacks
                if alertas_fallback:
                    self.logger.warning("=" * 80)
                    self.logger.warning(f"ALERTA: {len(alertas_fallback)} SKUs usaram CUSTO FALLBACK HARDCODED!")
                    for alerta in alertas_fallback[:10]:  # Mostrar até 10
                        self.logger.warning(f"  - {alerta}")
                    if len(alertas_fallback) > 10:
                        self.logger.warning(f"  ... e mais {len(alertas_fallback) - 10} SKUs")
                    self.logger.warning("=" * 80)
                
                # Resumo das origens
                if 'origem_custo' in df_sem_custo.columns:
                    origem_counts = df_sem_custo['origem_custo'].value_counts()
                    self.logger.info(f"  Origem dos custos: {origem_counts.to_dict()}")
        
        # Adicionar preços (merge por item/SKU - não exige embalagem no arquivo de preços)
        # Prioridade: 1) match por item_id (se disponível), 2) match por item (SKU)
        if 'origem_preco' not in df_base.columns:
            df_base['origem_preco'] = None
        
        if 'item_id' in dados.precos.columns:
            # Se arquivo de preços tem item_id (backward-compatible), tentar match exato primeiro
            df_base = df_base.merge(
                dados.precos[['item_id', 'preco']],
                on='item_id',
                how='left'
            )
            df_base.loc[df_base['preco'].notna(), 'origem_preco'] = 'preco_direto'
        else:
            df_base['preco'] = None
        
        # Preencher preços faltantes por item (SKU) - merge principal
        if 'item' in dados.precos.columns:
            preco_por_item = dados.precos.groupby('item')['preco'].mean()
            mask_sem_preco_1 = df_base['preco'].isna()
            df_base['preco'] = df_base['preco'].fillna(df_base['item'].map(preco_por_item))
            df_base.loc[mask_sem_preco_1 & df_base['preco'].notna(), 'origem_preco'] = 'preco_por_item'
        
        # Se ainda não tiver preço, usar média GERAL (como o modelo original)
        preco_medio_geral = dados.precos['preco'].mean()
        mask_sem_preco_2 = df_base['preco'].isna()
        if mask_sem_preco_2.any():
            df_base.loc[mask_sem_preco_2, 'preco'] = preco_medio_geral
            df_base.loc[mask_sem_preco_2, 'origem_preco'] = 'preco_medio_geral'
            self.logger.warning(f"  ALERTA: {mask_sem_preco_2.sum()} item_ids sem preço direto - usando preço médio geral (R$ {preco_medio_geral:.2f})")
            # Listar os SKUs afetados
            skus_sem_preco = df_base.loc[mask_sem_preco_2, 'item_id'].tolist()
            for sku in skus_sem_preco[:5]:
                self.logger.warning(f"    - {sku}")
            if len(skus_sem_preco) > 5:
                self.logger.warning(f"    ... e mais {len(skus_sem_preco) - 5} SKUs")
        
        if df_base['preco'].isna().any():
            self.logger.warning(f"  {df_base['preco'].isna().sum()} item_ids ainda sem preço após fallbacks")
        
        # Recalcular ovos por caixa para novos item_ids e filtrar
        df_base['qtd_ovos_por_caixa'] = df_base['embalagem'].apply(calcular_qtd_embalagem)
        df_base = df_base[df_base['qtd_ovos_por_caixa'].notna()].copy()
        
        # Calcular margem
        df_base['margem_unitaria'] = df_base['preco'] - df_base['custo_ytd']
        
        # Adicionar descrição dos itens
        df_base = self._adicionar_descricao(df_base)
        
        # Filtrar margens válidas
        df_base = df_base[
            (df_base['margem_unitaria'] > 0) &
            (df_base['preco'] > 0) &
            (df_base['custo_ytd'] > 0)
        ].copy()
        
        # Adicionar produção por classe
        producao_por_classe = dados.producao.set_index('classe')['producao_total']
        df_base['producao_total'] = df_base['classe'].map(producao_por_classe).fillna(0)
        
        # Adicionar pedidos
        pedidos_dict = dados.pedidos_por_sku.set_index('item')['quantidade_total_pedida'].to_dict()
        df_base['quantidade_total_pedida'] = df_base['item'].map(pedidos_dict).fillna(0)
        
        # Adicionar demanda histórica
        if len(dados.demanda_historica) > 0:
            demanda_dict = dados.demanda_historica.set_index('item')['demanda_max'].to_dict()
            df_base['tem_demanda_historica'] = df_base['item'].isin(demanda_dict.keys())
            df_base['limite_demanda_historica'] = df_base['item'].map(demanda_dict)
            # Adicionar volume histórico total (sem fator) para cálculo de proporção no baseline
            if 'volume_historico_total' in dados.demanda_historica.columns:
                volume_dict = dados.demanda_historica.set_index('item')['volume_historico_total'].to_dict()
                df_base['volume_historico_total'] = df_base['item'].map(volume_dict)
            else:
                df_base['volume_historico_total'] = np.nan
        else:
            df_base['tem_demanda_historica'] = False
            df_base['limite_demanda_historica'] = np.nan
            df_base['volume_historico_total'] = np.nan
        
        # Calcular produção disponível para otimização
        usar_apenas_excedente = self.config.get('modelo', {}).get('usar_apenas_excedente', True)
        atender_pedidos = self.config.get('modelo', {}).get('atender_pedidos', True)
        
        # Processar pedidos e calcular excedente
        pedidos_garantidos_por_sku, pedidos_garantidos_df, pedidos_ignorados = \
            self._processar_pedidos(df_base, dados, atender_pedidos)
        
        # Calcular produção excedente por classe
        # IMPORTANTE: Considerar TODOS os pedidos garantidos da classe,
        # mesmo de SKUs que não estão na otimização (ex: GRANEL, Exportação)
        producao_excedente = {}
        
        # Criar mapeamento sku -> classe a partir dos pedidos garantidos
        pedidos_por_classe = {}
        if len(pedidos_garantidos_df) > 0:
            for _, row in pedidos_garantidos_df.iterrows():
                classe = row['classe']
                qtd = row['quantidade_atendida']
                pedidos_por_classe[classe] = pedidos_por_classe.get(classe, 0) + qtd
        
        # Calcular excedente para cada classe
        for classe in df_base['classe'].unique():
            producao_classe = producao_por_classe.get(classe, 0)
            pedidos_classe = pedidos_por_classe.get(classe, 0)
            excedente = max(0, producao_classe - pedidos_classe)
            producao_excedente[classe] = excedente
            
            if pedidos_classe > 0:
                self.logger.info(f"    {classe}: produção={producao_classe:,.0f}, reservado={pedidos_classe:,.0f}, excedente={excedente:,.0f}")
        
        # Adicionar produção disponível para otimização
        if usar_apenas_excedente:
            df_base['producao_disponivel_otimizacao_classe'] = df_base['classe'].map(producao_excedente).fillna(0)
        else:
            df_base['producao_disponivel_otimizacao_classe'] = df_base['producao_total']
        
        # Log resumo
        self.logger.info(f"  Item_id validos: {len(df_base)}")
        self.logger.info(f"  SKUs validos: {df_base['item'].nunique()}")
        self.logger.info(f"  Classes validas: {df_base['classe'].nunique()}")
        self.logger.info(f"  Margem unitaria media: R$ {df_base['margem_unitaria'].mean():.2f}")
        
        # RESUMO DE RASTREABILIDADE - Informar origens dos dados
        self.logger.info("\n" + "=" * 80)
        self.logger.info("RASTREABILIDADE DOS DADOS")
        self.logger.info("=" * 80)
        
        # Origem dos custos
        if 'custo_medio_classe' in df_base.columns:
            custo_direto = (~df_base['custo_medio_classe'].fillna(False)).sum()
            custo_classe = df_base['custo_medio_classe'].fillna(False).sum()
            self.logger.info(f"CUSTOS:")
            self.logger.info(f"  - Custo direto (base de custos): {custo_direto} SKUs")
            self.logger.info(f"  - Custo médio da classe: {custo_classe} SKUs")
        
        # Origem dos preços
        if 'origem_preco' in df_base.columns:
            origem_preco_counts = df_base['origem_preco'].value_counts()
            self.logger.info(f"PREÇOS:")
            for origem, count in origem_preco_counts.items():
                self.logger.info(f"  - {origem}: {count} SKUs")
        
        # Origem das embalagens (para SKUs sem custo)
        if 'origem_embalagem' in df_base.columns:
            origem_emb_counts = df_base['origem_embalagem'].dropna().value_counts()
            if len(origem_emb_counts) > 0:
                self.logger.info(f"EMBALAGENS (SKUs sem custo direto):")
                for origem, count in origem_emb_counts.items():
                    self.logger.info(f"  - {origem}: {count} SKUs")
        
        self.logger.info("=" * 80)
        
        # Classe OUTROS não é otimizada: excluir da base enviada ao modelo; manter para aparecer no output com alocação 0
        mask_outros = df_base['classe'] == 'OUTROS'
        if mask_outros.any():
            df_outros = df_base[mask_outros].copy()
            df_base = df_base[~mask_outros].copy()
            self.logger.info(f"  Classe OUTROS: {len(df_outros)} itens excluídos da otimização (alocação = 0 no output)")
        else:
            df_outros = pd.DataFrame()
        
        return ResultadoETL(
            base_otimizacao=df_base,
            producao_por_classe=producao_por_classe,
            producao_excedente_por_classe=producao_excedente,
            pedidos_garantidos_por_sku=pedidos_garantidos_por_sku,
            pedidos_garantidos=pedidos_garantidos_df,
            pedidos_ignorados=pedidos_ignorados,
            usar_apenas_excedente=usar_apenas_excedente,
            atender_pedidos=atender_pedidos,
            dados_brutos=dados,
            base_outros=df_outros if len(df_outros) > 0 else None,
        )
    
    def _obter_skus_producao(self) -> set:
        """
        Obtém lista de SKUs que aparecem na produção do estabelecimento.
        
        IMPORTANTE: Apenas SKUs com embalagem válida (extraível) são considerados,
        para manter consistência com a lógica de comparação.
        """
        from extrair_compatibilidade_embalagem import extrair_embalagem_descricao, calcular_qtd_embalagem
        
        path = Path(self.config['paths'].get('producao_bruta', 'inputs/PRODUÇÃO DIA.xlsx'))
        
        if not path.exists():
            return set()
        
        try:
            df = pd.read_excel(path, sheet_name="CE0302", skiprows=1)
            
            # Filtrar por semana
            semana_ref = self.config.get('dados', {}).get('semana_ref', '2025-51')
            df['week'] = pd.to_datetime(df['Data Trans']).dt.isocalendar().week
            df['year'] = pd.to_datetime(df['Data Trans']).dt.isocalendar().year
            df['year_week'] = df['year'].astype(str) + '-' + df['week'].astype(str).str.zfill(2)
            df = df[df['year_week'] == semana_ref].copy()
            
            # Filtrar por estabelecimento
            estabelecimentos = self.config.get('dados', {}).get('estabelecimentos', [100])
            col_estab = 'Est' if 'Est' in df.columns else 'Estab'
            if col_estab in df.columns:
                df = df[df[col_estab].isin(estabelecimentos)].copy()
            
            # Extrair embalagem e calcular quantidade (mesma lógica da comparação)
            df['embalagem'] = df['Desc Item'].apply(extrair_embalagem_descricao)
            df['qtd_embalagem'] = df['embalagem'].apply(calcular_qtd_embalagem)
            df['quantidade'] = df['QUANTIDADE CORRIGIDA'] * df['qtd_embalagem']
            
            # Filtrar apenas SKUs com embalagem válida e quantidade > 0
            df = df[
                (df['embalagem'].notna()) & 
                (df['quantidade'].notna()) & 
                (df['quantidade'] > 0)
            ].copy()
            
            # Extrair SKUs
            col_item = 'Cod Item' if 'Cod Item' in df.columns else 'CODIGO ITEM'
            if col_item in df.columns:
                df[col_item] = pd.to_numeric(df[col_item], errors='coerce')
                return set(df[col_item].dropna().astype(int).unique())
        except Exception as e:
            self.logger.warning(f"  Erro ao obter SKUs da produção: {e}")
        
        return set()
    
    def _processar_pedidos(
        self, 
        df_base: pd.DataFrame, 
        dados: DadosCarregados,
        atender_pedidos: bool
    ) -> Tuple[Dict[int, float], pd.DataFrame, pd.DataFrame]:
        """
        Processa pedidos e retorna quantidades garantidas.
        
        REGRA: Todos os pedidos com classe mapeada são reservados.
        A reserva é descontada da produção da classe correspondente.
        
        A classe é buscada primeiro na base de otimização, depois na base de classes.
        """
        
        if not atender_pedidos:
            return {}, pd.DataFrame(), dados.pedidos
        
        pedidos_garantidos = {}
        pedidos_garantidos_list = []
        pedidos_ignorados_list = []
        
        # Criar mapeamento item -> classe a partir da base de classes
        classes_mapeamento = dados.classes.set_index('item')['classe'].to_dict() if len(dados.classes) > 0 else {}
        
        # Criar mapeamento classe -> produção
        producao_por_classe = dados.producao.set_index('classe')['producao_total'].to_dict() if len(dados.producao) > 0 else {}
        
        # Agrupar pedidos por SKU
        for item, qtd in dados.pedidos_por_sku.set_index('item')['quantidade_total_pedida'].items():
            # Tentar obter classe - primeiro da base de otimização, depois do mapeamento geral
            classe = None
            producao_classe = 0
            
            if item in df_base['item'].values:
                # SKU está na base de otimização
                classe = df_base[df_base['item'] == item]['classe'].iloc[0]
                producao_classe = df_base[df_base['item'] == item]['producao_total'].iloc[0]
            elif item in classes_mapeamento:
                # SKU não está na otimização mas tem classe mapeada
                # (ex: GRANEL, Exportação, MARCA PROPRIA)
                classe = classes_mapeamento[item]
                producao_classe = producao_por_classe.get(classe, 0)
            
            if classe is not None:
                # SKU válido - garantir pedido (reserva descontada da classe)
                pedidos_garantidos[item] = qtd
                pedidos_garantidos_list.append({
                    'item': item,
                    'classe': classe,
                    'quantidade_pedida': qtd,
                    'quantidade_atendida': qtd,  # Reserva total do pedido
                    'producao_total_classe': producao_classe,
                    'na_base_otimizacao': item in df_base['item'].values
                })
                self.logger.info(f"    Pedido SKU {item} ({classe}): {qtd:,.0f} ovos reservados")
            else:
                # SKU não encontrado em nenhuma base de classes
                pedidos_ignorados_list.append({
                    'item': item,
                    'quantidade_pedida': qtd,
                    'classe': None,
                    'motivo': 'SKU sem classe mapeada'
                })
                self.logger.info(f"    Pedido SKU {item}: {qtd:,.0f} ovos IGNORADO (sem classe mapeada)")
        
        if pedidos_garantidos_list:
            self.logger.info(f"  Total de pedidos garantidos: {len(pedidos_garantidos_list)}")
            self.logger.info(f"  Quantidade total reservada: {sum(p['quantidade_atendida'] for p in pedidos_garantidos_list):,.0f} ovos")
        
        if pedidos_ignorados_list:
            self.logger.info(f"  Pedidos ignorados (sem classe): {len(pedidos_ignorados_list)} SKUs")
        
        return (
            pedidos_garantidos,
            pd.DataFrame(pedidos_garantidos_list) if pedidos_garantidos_list else pd.DataFrame(),
            pd.DataFrame(pedidos_ignorados_list) if pedidos_ignorados_list else pd.DataFrame()
        )
    
    def _adicionar_descricao(self, df_base: pd.DataFrame) -> pd.DataFrame:
        """Adiciona coluna de descrição dos itens a partir da base de faturamento."""
        try:
            path_fat = Path(self.config['paths'].get('faturamento', 'inputs/manti_fat_2025_full.parquet'))
            
            if not path_fat.exists():
                self.logger.warning("  Base de faturamento não encontrada - descrição não adicionada")
                df_base['descricao'] = None
                return df_base
            
            # Carregar apenas colunas necessárias
            df_desc = pd.read_parquet(path_fat, columns=['item', 'Descrição do item'])
            df_desc = df_desc.drop_duplicates(subset=['item'])
            df_desc.columns = ['item', 'descricao']
            df_desc['item'] = df_desc['item'].astype(int)
            
            # Merge com df_base
            df_base = df_base.merge(df_desc, on='item', how='left')
            
            # Log
            com_desc = df_base['descricao'].notna().sum()
            self.logger.info(f"  Descrições adicionadas: {com_desc} de {len(df_base)} itens")
            
        except Exception as e:
            self.logger.warning(f"  Erro ao carregar descrições: {e}")
            df_base['descricao'] = None
        
        return df_base
