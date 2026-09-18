@echo off
REM ─────────────────────────────────────────────────────────────
REM  Generar_Exe.bat
REM  Doble clic para (re)generar:
REM   1) Distribuidor_Fruver_Proyecto.exe  -- el programa (queda en
REM      ..\Distribucion-Fruver, junto a su Input\/Output\)
REM   2) Instalador_Distribuidor_Fruver.exe -- para llevar a OTRA PC:
REM      pregunta una carpeta destino, instala el programa + Input de
REM      ejemplo + Output vacia ahi, y crea su propio acceso directo
REM  y crea/actualiza el acceso directo del Escritorio de ESTA PC.
REM ─────────────────────────────────────────────────────────────
cd /d "%~dp0"

set APP_DIR=..\Distribucion-Fruver
set VENV=%APP_DIR%\distribuidor-fruver\venv

if not exist "%VENV%" (
    echo.
    echo  Creando entorno virtual...
    python -m venv "%VENV%"
)

echo.
echo  Instalando dependencias...
"%VENV%\Scripts\python.exe" -m pip install -r "%APP_DIR%\distribuidor-fruver\requirements.txt" pyinstaller -q

echo.
echo  [1/2] Generando Distribuidor_Fruver_Proyecto.exe...
REM Se usa "python -m PyInstaller" (no el pyinstaller.exe del venv): ese launcher
REM graba la ruta absoluta del venv al crearse, y si el venv se recrea en otra
REM ruta el .exe stub queda roto. "python -m" siempre resuelve en runtime.
"%VENV%\Scripts\python.exe" -m PyInstaller --onefile --noconfirm --name Distribuidor_Fruver_Proyecto --icon assets\manzana.ico --paths "%APP_DIR%\distribuidor-fruver" --distpath "%APP_DIR%" --workpath build --specpath . "%APP_DIR%\run.py"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [ERROR] La generacion del .exe fallo. Revisa el log de arriba.
    pause
    exit /b 1
)

echo.
echo  [2/2] Generando Instalador_Distribuidor_Fruver.exe (para llevar a otra PC)...
"%VENV%\Scripts\python.exe" -m PyInstaller --onefile --windowed --noconfirm --name Instalador_Distribuidor_Fruver --icon assets\manzana.ico --add-data "%APP_DIR%\Distribuidor_Fruver_Proyecto.exe;payload" --add-data "%APP_DIR%\Input;payload\Input" --add-data "assets\manzana.ico;payload" --distpath . --workpath build --specpath . instalador.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  [ERROR] La generacion del instalador fallo. Revisa el log de arriba.
    pause
    exit /b 1
)

echo.
echo  Creando acceso directo en el Escritorio de esta PC...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Crear_Acceso_Directo.ps1"

echo.
echo  [OK] Listo.
echo   - Programa (esta PC):        Distribucion-Fruver\Distribuidor_Fruver_Proyecto.exe
echo   - Instalador (para otra PC):  Exe-Accesos-Directos\Instalador_Distribuidor_Fruver.exe
pause
