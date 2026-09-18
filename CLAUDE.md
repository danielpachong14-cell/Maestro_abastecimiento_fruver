# CLAUDE.md

Mapa de este repositorio para Claude Code. Ver [`INDICE.md`](INDICE.md) para el
detalle de rutas de cada archivo/carpeta.

Este repo agrupa tres proyectos independientes, cada uno en su propia carpeta
con su propio `CLAUDE.md`:

## `Distribucion-Fruver/`

El proyecto principal: distribución automática del inventario del CEDI a las
tiendas Fruver activas. CLI batch (`run.py`, lee `Input/` → escribe `Output/`) +
app web Streamlit (`distribuidor-fruver/app.py`). Incluye el `.exe` generado
(`Distribuidor_Fruver_Proyecto.exe`), que vive ahí porque necesita estar junto a
`Input/`/`Output/` para funcionar.

Ver `Distribucion-Fruver/CLAUDE.md` para comandos, arquitectura del pipeline y
reglas de negocio, y `Distribucion-Fruver/distribuidor-fruver/CLAUDE.md` para el
detalle de implementación (normalización de claves, parámetros del algoritmo,
fases de distribución).

## `Actualizacion-Portafolio/`

Herramienta independiente y sin relación con la anterior: transforma el Excel de
segmentación de portafolio Fruver a formato largo (tienda × ítem). Ver
`Actualizacion-Portafolio/CLAUDE.md`.

## `Exe-Accesos-Directos/`

No es un proyecto en sí — son los scripts que **generan** dos `.exe`:

- `Distribuidor_Fruver_Proyecto.exe` (queda en `Distribucion-Fruver/`, para usar
  en esta máquina).
- `Instalador_Distribuidor_Fruver.exe` (queda aquí mismo — es el que se copia a
  **otra PC**: pregunta una carpeta destino, instala el programa + Input de
  ejemplo + Output vacía ahí, y crea su propio acceso directo de Escritorio).

Ver la sección "Ejecutable de escritorio" en `Distribucion-Fruver/CLAUDE.md`
para el detalle completo del flujo de build de ambos.
