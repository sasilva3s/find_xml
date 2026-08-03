@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "APP_ROOT=%SCRIPT_DIR%..\..\.."
set "PYTHON_EXE=%APP_ROOT%\python3\python.exe"
set "SCRIPT_PATH=%SCRIPT_DIR%backfill_missing_order.py"
set "AUDIT_OUTPUT_FOLDER=%APP_ROOT%\data\server\auditlog"

if "%~1"=="" (
    echo Uso: run_backfill_missing_order.bat ORDER_ID [ORDER_ID2 ...] [--force]
    echo Exemplo: run_backfill_missing_order.bat 317157 317186
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo Python nao encontrado em: %PYTHON_EXE%
    echo Ajuste APP_ROOT neste .bat se a instalacao estiver em outro caminho.
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT_PATH%" --order-ids %* --audit-output-folder "%AUDIT_OUTPUT_FOLDER%"

echo.
echo Concluido. Codigo de saida: %ERRORLEVEL%
pause

endlocal
