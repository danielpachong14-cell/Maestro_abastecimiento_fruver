# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Comandos

Todos los comandos se ejecutan desde `distribuidor-fruver/` con el venv activo, **excepto** el CLI raíz:

```bash
# Setup (una sola vez)
cd distribuidor-fruver
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Aplicación web (modo principal)
streamlit run app.py           # http://localhost:8501

# CLI batch (lee Input/, escribe Output/)
cd ..                          # volver a la raíz del repo
venv\Scripts\activate
python run.py

# Tests
cd distribuidor-fruver
PYTHONPATH=. pytest tests/ -v  # todos los tests
PYTHONPATH=. pytest tests/test_algorithm.py::test_distribucion_total -v  # un test
```

## Estructura raíz vs paquete

```
Distribucion de Fruver/
├── run.py               ← CLI batch: lee Input/, escribe Output/
├── Input/               ← archivos Excel de entrada (no versionados)
├── Output/              ← Excel generados (no versionados)
└── distribuidor-fruver/ ← paquete Python (código fuente + UI)
    ├── app.py           ← entry point Streamlit
    ├── core/            ← pipeline: loader → preprocessor → algorithm → exporter
    └── tests/
```

`run.py` en la raíz agrega `distribuidor-fruver/` al `sys.path` e importa directamente de `core/`. Es el único puente entre la raíz y el paquete.

## Arquitectura del pipeline

```
5 Excel (UploadedFile o file objects)
  → loader.py       valida columnas requeridas por archivo
  → preprocessor.py normaliza claves, merge → DataFrame tienda × ítem
  → algorithm.py    distribuye en 2 fases con prioridades → (df_output, alertas)
  → exporter.py     genera bytes del Excel final (3 hojas)
```

Todo el procesamiento ocurre en memoria. Ningún módulo del paquete escribe a disco; solo `run.py` y el botón de descarga de `app.py` persisten archivos.

## Detalles de implementación — ver `distribuidor-fruver/CLAUDE.md`

El CLAUDE.md interno documenta exhaustivamente:

- **Normalización de claves de cruce** (CRÍTICO para que los merges funcionen): prefijo `BO` en tiendas, texto vs entero en ítems, fila `'Gran total'` a eliminar.
- **Parámetros del algoritmo**: `TARGET_DAYS`, `MIN_STOCK_AGOTADO`, `MIN_STOCK_SAFETY`, `MIN_CAJAS_INICIAL`.
- **Lógica de prioridades y distribución del sobrante** (por qué el órden de tiendas importa).
- **Reglas no negociables**: cero residuo, solo cajas completas, no escribir a disco.

## Contradicción conocida entre docs

`distribuidor-fruver/CLAUDE.md` dice que el campo `Aplica para DistribuciónAutomática?` **se ignora** (se distribuye a todas las tiendas en Celes para ese ítem). `README.md` dice que solo se distribuye a tiendas con ese campo en `SI`. El CLAUDE.md es la fuente autoritativa — verificar contra `core/preprocessor.py` antes de cambiar la lógica.
