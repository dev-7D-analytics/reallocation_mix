#!/bin/bash
# ==============================================================================
# Pipeline completo: preparação de dados + otimização + relatório
# ==============================================================================
#
# Ordem de execução:
#   1. extrair_precos_embalagem.py   → inputs/precos_sku_embalagem.csv
#   2. gerar_producao_classe.py      → inputs/producao_classe.csv
#   3. main.py                       → ETL + Otimização + Baseline
#   4. comparar_producao_alocacao.py → Relatório comparativo (opcional)
#
# Uso:
#   ./executar_pipeline.sh           → Executa tudo (passos 1 a 4)
#   ./executar_pipeline.sh --sem-preparacao  → Só otimização (passos 3 e 4)
#   ./executar_pipeline.sh --sem-relatorio   → Sem relatório (passos 1 a 3)
#
# ==============================================================================

set -e  # Para na primeira falha

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # Sem cor

# Argumentos
SKIP_PREPARACAO=false
SKIP_RELATORIO=false

for arg in "$@"; do
    case $arg in
        --sem-preparacao) SKIP_PREPARACAO=true ;;
        --sem-relatorio)  SKIP_RELATORIO=true ;;
        --help|-h)
            echo "Uso: $0 [--sem-preparacao] [--sem-relatorio]"
            echo ""
            echo "  --sem-preparacao   Pula extração de preços e produção (passos 1-2)"
            echo "  --sem-relatorio    Pula geração do relatório comparativo (passo 4)"
            exit 0
            ;;
        *)
            echo -e "${RED}[ERRO] Argumento desconhecido: $arg${NC}"
            echo "Use --help para ver opções."
            exit 1
            ;;
    esac
done

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  PIPELINE DE OTIMIZAÇÃO DE MIX                             ${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

INICIO=$(date +%s)

#  PASSO 1: Extrair preços 
if [ "$SKIP_PREPARACAO" = false ]; then
    echo -e "${YELLOW}[1/4] Extraindo preços por SKU/embalagem...${NC}"
    python3 extrair_precos_embalagem.py
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}[OK] Preços extraídos → inputs/precos_sku_embalagem.csv${NC}"
    else
        echo -e "${RED}[ERRO] Falha na extração de preços${NC}"
        exit 1
    fi
    echo ""

    #  PASSO 2: Gerar produção por classe 
    echo -e "${YELLOW}[2/4] Gerando produção por classe...${NC}"
    python3 gerar_producao_classe.py
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}[OK] Produção gerada → inputs/producao_classe.csv${NC}"
    else
        echo -e "${RED}[ERRO] Falha na geração de produção${NC}"
        exit 1
    fi
    echo ""
else
    echo -e "${YELLOW}[1/4] Extração de preços: PULADO (--sem-preparacao)${NC}"
    echo -e "${YELLOW}[2/4] Geração de produção: PULADO (--sem-preparacao)${NC}"
    echo ""
fi

#  PASSO 3: ETL + Otimização 
echo -e "${YELLOW}[3/4] Executando ETL + Otimização...${NC}"
python3 main.py
if [ $? -eq 0 ]; then
    echo -e "${GREEN}[OK] Otimização concluída${NC}"
else
    echo -e "${RED}[ERRO] Falha na otimização${NC}"
    exit 1
fi
echo ""

#  PASSO 4: Relatório comparativo 
if [ "$SKIP_RELATORIO" = false ]; then
    echo -e "${YELLOW}[4/4] Gerando relatório comparativo...${NC}"
    python3 comparar_producao_alocacao.py
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}[OK] Relatório gerado${NC}"
    else
        echo -e "${RED}[ERRO] Falha na geração do relatório${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}[4/4] Relatório comparativo: PULADO (--sem-relatorio)${NC}"
fi

#  Resumo 
FIM=$(date +%s)
DURACAO=$((FIM - INICIO))
MINUTOS=$((DURACAO / 60))
SEGUNDOS=$((DURACAO % 60))

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  PIPELINE CONCLUÍDO em ${MINUTOS}m ${SEGUNDOS}s${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""
echo "Resultados em: resultados/"
