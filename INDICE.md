# Índice del repositorio

Este repositorio agrupa **tres proyectos independientes**, cada uno en su propia
carpeta, más un `.exe` de conveniencia para correr el proyecto principal sin
terminal ni IDE. Ver `CLAUDE.md` para el resumen narrativo; este archivo detalla
la ruta y el propósito de cada archivo/carpeta.

```
Distribucion de Fruver/                          (raíz del repo)
├── CLAUDE.md                                     Mapa del repo para Claude Code
├── INDICE.md                                      Este archivo
├── .gitignore                                     Reglas de git (caches, venv, *.exe, build/, *.spec)
│
├── Distribucion-Fruver/                          ① Proyecto principal — ver detalle abajo
├── Actualizacion-Portafolio/                     ② Herramienta de portafolio — ver detalle abajo
└── Exe-Accesos-Directos/                         ③ Empaquetado del .exe y acceso directo — ver detalle abajo
```

## ① `Distribucion-Fruver/` — distribución automática de inventario

El proyecto principal. Reparte el inventario del CEDI entre las tiendas Fruver
activas. Todo lo necesario para que funcione (datos, código, ejecutable) vive
aquí dentro.

```
Distribucion-Fruver/
├── CLAUDE.md                                     Comandos, arquitectura del pipeline, reglas de negocio
├── Distribuidor Fruver — Descripción del Proyecto.md   Documento de negocio (no técnico)
├── run.py                                        CLI batch: lee Input/, escribe Output/
├── Distribuidor_Fruver_Proyecto.exe               Ejecutable generado (PyInstaller) — no versionado
│
├── Input/                                        Excel de entrada (6 obligatorios + 1 opcional), no versionados salvo excepción histórica (ver nota)
│   ├── Stock.xlsx                                Existencias del CEDI (Siesa)
│   ├── Celes.xlsx                                Consumos por tienda × ítem
│   ├── DB_Portafolio Fruver.xlsx                 Catálogo y estado de ítems (validación cruzada)
│   ├── DB_Tiendas.xlsx                           Maestro de tiendas activas
│   ├── DB_Tiendas Por itmes y portafolio.xlsx    Elegibilidad tienda × ítem
│   ├── DB_ProductosEspejo.xlsx                   Grupos de SKUs espejo
│   └── Tiendas_No generar pedido.xlsx            Tiendas excluidas (opcional)
│
├── Output/                                       Excel generados, uno por corrida: distribucion_fruver_YYYYMMDD.xlsx
│
└── distribuidor-fruver/                          Paquete Python (código fuente + UI)
    ├── CLAUDE.md                                 Detalle de implementación: normalización de claves, parámetros del algoritmo, fases 1/2/3
    ├── README.md                                 Descripción funcional del paquete
    ├── requirements.txt                          Dependencias (streamlit, pandas, openpyxl, xlrd, pytest)
    ├── .gitignore                                Ignora venv/, __pycache__/, *.xlsx, etc. (self-contenido)
    ├── app.py                                    Entry point de la app web Streamlit
    ├── venv/                                     Entorno virtual (generado, no versionado)
    ├── core/                                     Pipeline de distribución
    │   ├── loader.py                             Carga y valida columnas requeridas de cada Excel
    │   ├── preprocessor.py                       Normaliza claves y hace merge → (df_merged, alertas)
    │   ├── algorithm.py                          Motor de distribución (3 fases, prioridades, cero residuo)
    │   └── exporter.py                           Genera el Excel final (5 hojas)
    └── tests/                                    Suite pytest (67 tests)
        ├── test_loader.py
        ├── test_preprocessor.py
        ├── test_algorithm.py
        └── test_exporter.py
```

**Nota sobre `Input/`/`Output/` y versionado**: 3 archivos de `Output/` (los de
junio 20260601–20260603) y los 6 `Input/*.xlsx` actuales quedaron versionados en
git de corridas antiguas, aunque la intención documentada es que ambas carpetas
sean datos locales no versionados. Se dejó como está en esta reorganización
(no se tocó el tracking de git); es una limpieza aparte si se quiere hacer.

## ② `Actualizacion-Portafolio/` — transformación de portafolio (sin relación con ①)

Herramienta standalone: convierte el Excel de segmentación de portafolio a
formato largo (una fila por tienda × ítem marcado con X).

```
Actualizacion-Portafolio/
├── CLAUDE.md                                     Formato del Excel fuente, funciones clave, qué ajustar si algo falla
├── README.md                                     Descripción funcional
├── EJECUTAR_PORTAFOLIO.bat                       Doble clic: corre transformar_portafolio.py y abre el resultado
├── transformar_portafolio.py                     Lógica de transformación (ejecutable o importable)
├── watcher.py                                    Vigila el Excel fuente y re-ejecuta al detectar cambios
├── Input/
│   └── Segmentación_Portafolio.xlsx              Excel fuente (se reemplaza con cada actualización)
└── Output/
    ├── Antes.xlsx                                Snapshot de referencia
    └── Portafolio_Fruver_Centro_Largo.xlsx       Resultado de la transformación
```

## ③ `Exe-Accesos-Directos/` — generación del .exe, del instalador y del acceso directo

No es un proyecto en sí: son los scripts que **construyen** dos ejecutables y
crean accesos directos. `Generar_Exe.bat` genera, en este orden:

1. `Distribucion-Fruver/Distribuidor_Fruver_Proyecto.exe` — el programa en sí
   (vive allá porque necesita estar junto a `Input/`/`Output/`).
2. `Instalador_Distribuidor_Fruver.exe` (aquí mismo) — el que se copia a **otra
   PC**: al abrirlo pregunta una carpeta destino, instala ahí el programa +
   `Input/` de ejemplo + `Output/` vacía, y crea su propio acceso directo de
   Escritorio en esa máquina. Es una GUI sin consola (`tkinter`); embebe el
   `.exe` del programa y los `Input/*.xlsx` vigentes al momento del build como
   datos empaquetados (`--add-data` de PyInstaller) — por eso depende de que el
   paso 1 ya haya corrido.

```
Exe-Accesos-Directos/
├── Generar_Exe.bat                               Doble clic: construye 1) y 2), y llama a Crear_Acceso_Directo.ps1
├── Crear_Acceso_Directo.ps1                       Crea/actualiza "Distribuidor Fruver.lnk" en el Escritorio de ESTA PC
│                                                  (idempotente — se puede correr varias veces)
├── instalador.py                                 Código fuente del instalador: pide carpeta destino (tkinter),
│                                                  copia el payload embebido, crea el acceso directo
├── Instalador_Distribuidor_Fruver.exe             El .exe para llevar a otra PC (generado, no versionado)
├── assets/
│   └── manzana.ico                               Ícono de manzana (multi-resolución) para ambos .exe y accesos directos
├── build/                                        Artefactos intermedios de PyInstaller (generado, no versionado)
└── *.spec                                        Specs de PyInstaller, uno por cada .exe (generados, no versionados)
```

## Pendiente de decisión del usuario (no se tocó en esta reorganización)

- `Distribucion-Fruver/distribuidor-fruver/Distribucion de Fruver - Acceso
  directo.lnk` — acceso directo viejo que solo abría la carpeta en el
  Explorador (sin ícono, sin ejecutar nada), reemplazado por el acceso directo
  nuevo en el Escritorio.

Si querés que lo borre, decímelo explícitamente y lo hago.

(La carpeta duplicada `Distribucion de Fruver/` que aparecía aquí se borró el
2026-07-15 a pedido del usuario.)
