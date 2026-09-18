# CLAUDE.md

Guía para Claude Code sobre esta carpeta (`Distribucion-Fruver/`): el proyecto
principal de distribución automática de inventario Fruver. Ver `../CLAUDE.md`
(raíz del repo) para el mapa de las otras carpetas del repositorio.

## Comandos

Todos los comandos se ejecutan desde aquí (`Distribucion-Fruver/`), con el venv
activo, **excepto** el CLI raíz de esta carpeta:

```bash
# Setup (una sola vez)
cd distribuidor-fruver
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Aplicación web (modo principal)
streamlit run app.py           # http://localhost:8501

# CLI batch (lee Input/, escribe Output/)
cd ..                          # volver a esta carpeta (Distribucion-Fruver/)
venv\Scripts\activate
python run.py

# Tests
cd distribuidor-fruver
PYTHONPATH=. pytest tests/ -v  # todos los tests
PYTHONPATH=. pytest tests/test_algorithm.py::test_distribucion_total -v  # un test
```

## Ejecutable de escritorio (`Distribuidor_Fruver_Proyecto.exe`)

`Distribuidor_Fruver_Proyecto.exe` (en esta carpeta, generado, no versionado) es
un ejecutable real construido con PyInstaller (`--onefile`) a partir de `run.py`
— no requiere Python instalado en la máquina destino. Vive junto a `Input/` y
`Output/` porque resuelve su propia carpeta en tiempo de ejecución (ver más
abajo) — no lo muevas de aquí sin actualizar esa lógica.

El script y los assets para (re)generarlo viven en `../Exe-Accesos-Directos/`
(carpeta separada — ver `../CLAUDE.md`). Para regenerarlo tras un cambio de
código:

```bash
..\Exe-Accesos-Directos\Generar_Exe.bat    # doble clic, o desde consola
```

Este script crea/reusa `distribuidor-fruver/venv` (aquí, no en
`Exe-Accesos-Directos/`), instala `distribuidor-fruver/requirements.txt` +
`pyinstaller`, reconstruye el `.exe` en esta carpeta (mismo nombre, sobreescribe
el anterior) con el ícono `../Exe-Accesos-Directos/assets/manzana.ico`, y llama a
`Crear_Acceso_Directo.ps1` para (re)crear el acceso directo `Distribuidor
Fruver.lnk` en el Escritorio del usuario (idempotente).

`run.py` resuelve `ROOT` distinto según el modo (`getattr(sys, "frozen", False)`):
dentro del `.exe` usa `Path(sys.executable).parent` (no `__file__`, que dentro de
un onefile de PyInstaller apunta a la carpeta temporal de extracción, no a donde
vive el `.exe`) — así `Input/`/`Output/` siguen resolviendo junto al ejecutable,
siempre que el `.exe` se genere con `--distpath` apuntando a esta carpeta (así lo
hace `Generar_Exe.bat`).

Nota: al no estar firmado digitalmente, Windows SmartScreen puede advertir la
primera vez que se ejecuta en una máquina — es un falso positivo común con
ejecutables de PyInstaller sin firmar ("Más información" → "Ejecutar de todas
formas").

### Llevar el programa a otra PC: `Instalador_Distribuidor_Fruver.exe`

`Generar_Exe.bat` también genera `../Exe-Accesos-Directos/Instalador_Distribuidor_Fruver.exe`
— un segundo `.exe`, sin consola (GUI con `tkinter`), que es lo que se copia a
una máquina nueva. Al abrirlo: pregunta una carpeta destino, crea ahí una
subcarpeta `Distribuidor Fruver/` con el `.exe` del programa + `Input/` (con los
archivos de ejemplo vigentes al momento del build) + `Output/` vacía, y crea su
propio acceso directo en el Escritorio de esa máquina. El usuario final debe
reemplazar los archivos de `Input/` con los datos reales del día antes de
correrlo. Ver `../Exe-Accesos-Directos/instalador.py` — los datos que empaqueta
(el `.exe` del programa + `Input/`) se embeben vía `--add-data` de PyInstaller
en el momento del build, así que **el instalador solo se regenera corriendo
`Generar_Exe.bat` de nuevo** (no queda desactualizado silenciosamente, pero
tampoco se actualiza solo).

## Estructura de esta carpeta

```
Distribucion-Fruver/
├── run.py                        ← CLI batch: lee Input/, escribe Output/
├── Distribuidor_Fruver_Proyecto.exe  ← generado, no versionado (ver arriba)
├── Input/                        ← archivos Excel de entrada (no versionados)
├── Output/                       ← Excel generados (no versionados)
└── distribuidor-fruver/          ← paquete Python (código fuente + UI)
    ├── app.py                    ← entry point Streamlit
    ├── core/                     ← pipeline: loader → preprocessor → algorithm → exporter
    └── tests/
```

`run.py` agrega `distribuidor-fruver/` a `sys.path` e importa directamente de
`core/`. Es el único puente entre esta carpeta y el paquete.

## Arquitectura del pipeline

```
6 Excel obligatorios + 1 opcional (UploadedFile o file objects)
  → loader.py       valida columnas requeridas por archivo
  → preprocessor.py normaliza claves, merge → (df_merged, alertas de calidad de datos)
  → algorithm.py    distribuye en 3 fases con prioridades → (df_output, alertas)
  → exporter.py     genera bytes del Excel final (6 hojas, incluida Alertas)
```

Todo el procesamiento ocurre en memoria. Ningún módulo del paquete escribe a disco; solo `run.py` y el botón de descarga de `app.py` persisten archivos.

## Detalles de implementación — ver `distribuidor-fruver/CLAUDE.md`

El CLAUDE.md interno documenta exhaustivamente:

- **Normalización de claves de cruce** (CRÍTICO para que los merges funcionen): prefijo `BO` en tiendas, texto vs entero en ítems, fila totalizadora a eliminar, comparación exacta (no `contains`) de estado activo.
- **Parámetros del algoritmo**: `TARGET_DAYS`, `MIN_STOCK_AGOTADO`, `MIN_STOCK_SAFETY`, `MIN_CAJAS_INICIAL`, `MAX_CAJAS_POR_ITEM`, `TOPE_EXCEDENTE`.
- **Lógica de prioridades y distribución del sobrante** (por qué el órden de tiendas importa).
- **Grupos de productos espejo** y validación cruzada con Portafolio Fruver.
- **Tope `MAX` por tienda × ítem**: columna calculada de
  `DB_Tiendas Por itmes y portafolio.xlsx` (XLOOKUP contra su hoja `BD_MAX`)
  que saca del reparto normal a las tienda×ítem cuyas **Existencias** ya
  igualan o superan ese máximo. Se compara contra Existencias, no contra el
  inventario efectivo (el tránsito no bloquea), y `MAX = 0` significa "sin
  tope definido".
- **Reglas no negociables**: cero residuo, solo cajas completas, no escribir a disco, alertas visibles y persistidas.

## Resuelto: contradicción sobre "Aplica para Distribución Automática?"

Hasta la auditoría técnica de 2026-07, `distribuidor-fruver/README.md` afirmaba que
solo se distribuía a tiendas con `Aplica para DistribuciónAutomática? = SI`, mientras
que `distribuidor-fruver/CLAUDE.md` decía que el campo se ignora. Se verificó contra
`core/preprocessor.py` y contra datos reales (96.6% de las filas de Celes tenían `NO`
en ese campo): **el código lo ignora por completo** — ni siquiera está entre las
columnas que `loader.py` exige de Celes. El README ya se corrigió; si vuelve a
aparecer una discrepancia de este tipo, `core/preprocessor.py` es la fuente de verdad.
