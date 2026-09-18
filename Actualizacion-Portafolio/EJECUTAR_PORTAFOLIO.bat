@echo off
REM ─────────────────────────────────────────────────────────────
REM  EJECUTAR_PORTAFOLIO.bat
REM  Doble clic para transformar el portafolio Fruver.
REM  Ajusta ORIGEN y DESTINO si los archivos están en otra ruta.
REM ─────────────────────────────────────────────────────────────

set ORIGEN=%~dp0Input\Segmentación_Portafolio.xlsx
set DESTINO=%~dp0Output\Portafolio_Fruver_Centro_Largo.xlsx
set SCRIPT=%~dp0transformar_portafolio.py

echo.
echo  Transformando portafolio Fruver...
echo  Origen:  %ORIGEN%
echo  Destino: %DESTINO%
echo.

python "%SCRIPT%" --origen "%ORIGEN%" --destino "%DESTINO%"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo  [OK] Archivo generado exitosamente.
    echo  Abriendo resultado...
    start "" "%DESTINO%"
) else (
    echo.
    echo  [ERROR] Revisa que Python este instalado y las rutas sean correctas.
)

pause
