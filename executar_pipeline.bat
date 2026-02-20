@echo off
chcp 65001 >nul 2>&1
REM ==============================================================================
REM Pipeline completo: preparação de dados + otimização + relatório
REM ==============================================================================
REM
REM Ordem de execução:
REM   1. extrair_compatibilidade_embalagem.py → inputs/compatibilidade_sku_embalagem.csv
REM   2. extrair_precos_embalagem.py          → inputs/precos_sku_embalagem.csv
REM   3. gerar_custos_sku.py                  → inputs/custos_sku.csv
REM   4. gerar_demanda_historica.py           → inputs/demanda_historica.csv
REM   5. gerar_pedidos_clientes.py            → inputs/pedidos_clientes.csv
REM   6. gerar_producao_classe.py             → inputs/producao_classe.csv
REM   7. main.py                              → ETL + Otimização + Baseline
REM   8. comparar_producao_alocacao.py        → Relatório comparativo
REM
REM Uso:
REM   executar_pipeline.bat                   → Executa tudo (passos 1 a 8)
REM   executar_pipeline.bat --sem-preparacao  → Só otimização (passos 7 e 8)
REM   executar_pipeline.bat --sem-relatorio   → Sem relatório (passos 1 a 7)
REM
REM ==============================================================================

setlocal enabledelayedexpansion

REM Ir para o diretório do script
cd /d "%~dp0"

REM Argumentos
set SKIP_PREPARACAO=0
set SKIP_RELATORIO=0

for %%a in (%*) do (
    if "%%a"=="--sem-preparacao" set SKIP_PREPARACAO=1
    if "%%a"=="--sem-relatorio" set SKIP_RELATORIO=1
    if "%%a"=="--help" goto :usage
    if "%%a"=="-h" goto :usage
)

echo ============================================================
echo   PIPELINE DE OTIMIZACAO DE MIX
echo ============================================================
echo.

REM Criar pastas se não existirem
if not exist inputs mkdir inputs
if not exist resultados mkdir resultados

REM Marcar início
set INICIO=%time%

REM ============================================================
REM  PREPARAÇÃO DE INPUTS (passos 1-6)
REM ============================================================
if %SKIP_PREPARACAO%==1 (
    echo [1/8] Extracao de compatibilidade: PULADO --sem-preparacao
    echo [2/8] Extracao de precos: PULADO --sem-preparacao
    echo [3/8] Geracao de custos: PULADO --sem-preparacao
    echo [4/8] Geracao de demanda historica: PULADO --sem-preparacao
    echo [5/8] Geracao de pedidos: PULADO --sem-preparacao
    echo [6/8] Geracao de producao: PULADO --sem-preparacao
    echo.
    goto :otimizacao
)

REM PASSO 1: Extrair compatibilidade de embalagem
echo [1/8] Extraindo compatibilidade SKU/embalagem...
python extrair_compatibilidade_embalagem.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na extracao de compatibilidade
    goto :erro
)
echo [OK] Compatibilidade extraida
echo.

REM PASSO 2: Extrair preços por embalagem
echo [2/8] Extraindo precos por SKU/embalagem...
python extrair_precos_embalagem.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na extracao de precos
    goto :erro
)
echo [OK] Precos extraidos
echo.

REM PASSO 3: Gerar custos por SKU
echo [3/8] Gerando custos por SKU...
python gerar_custos_sku.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na geracao de custos
    goto :erro
)
echo [OK] Custos gerados
echo.

REM PASSO 4: Gerar demanda histórica
echo [4/8] Gerando demanda historica por SKU...
python gerar_demanda_historica.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na geracao de demanda historica
    goto :erro
)
echo [OK] Demanda historica gerada
echo.

REM PASSO 5: Gerar pedidos de clientes
echo [5/8] Gerando pedidos de clientes...
python gerar_pedidos_clientes.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na geracao de pedidos
    goto :erro
)
echo [OK] Pedidos gerados
echo.

REM PASSO 6: Gerar produção por classe
echo [6/8] Gerando producao por classe...
python gerar_producao_classe.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na geracao de producao
    goto :erro
)
echo [OK] Producao gerada
echo.

:otimizacao
REM ============================================================
REM  PASSO 7: ETL + Otimização
REM ============================================================
echo [7/8] Executando ETL + Otimizacao...
python main.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na otimizacao
    goto :erro
)
echo [OK] Otimizacao concluida
echo.

REM ============================================================
REM  PASSO 8: Relatório comparativo
REM ============================================================
if %SKIP_RELATORIO%==1 (
    echo [8/8] Relatorio comparativo: PULADO --sem-relatorio
    goto :fim
)

echo [8/8] Gerando relatorio comparativo...
python comparar_producao_alocacao.py
if %errorlevel% neq 0 (
    echo [ERRO] Falha na geracao do relatorio
    goto :erro
)
echo [OK] Relatorio gerado
echo.

:fim
echo.
echo ============================================================
echo   PIPELINE CONCLUIDO
echo ============================================================
echo.
echo Resultados em: resultados\
exit /b 0

:erro
echo.
echo ============================================================
echo   PIPELINE FALHOU
echo ============================================================
exit /b 1

:usage
echo Uso: %~nx0 [--sem-preparacao] [--sem-relatorio]
echo.
echo   --sem-preparacao   Pula preparacao de inputs (passos 1-6)
echo   --sem-relatorio    Pula geracao do relatorio comparativo (passo 8)
exit /b 0
