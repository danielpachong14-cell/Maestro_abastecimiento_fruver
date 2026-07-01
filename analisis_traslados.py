"""
Plan de Traslados Nacional — Análisis de excesos de inventario entre tiendas
=============================================================================
Lee los archivos Excel de la carpeta Input/, aplica el algoritmo greedy de
traslados sobre los cuadrantes nacionales activos y genera un archivo de salida
en Output/ con el plan detallado por tienda.

Archivos de entrada requeridos (Input/):
  - DB_Tiendas.xlsx                                           → maestro de tiendas (94 bodegas únicas)
  - DB_CELES.xlsx  hoja "Items"                              → inventario nacional (~117 000 filas)
  - DB_PORTAFOLIO_FRUVER.xlsx                                → portafolio autorizado FRUVER (Q16)
  - DB_POLITICA_NACIONAL_DIAS_DE_INVENTARIO_POR_CUADRANTE.xlsx → umbrales máximos por cuadrante

Archivo de salida:
  Output/Plan_Traslados_YYYYMMDD.xlsx  (3 hojas: Resumen Ejecutivo,
  Traslados Misma Zona, Resumen por Tienda)

Ejecución:
    python analisis_traslados.py
"""

import math
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

# ===========================================================================
# PARÁMETROS CONFIGURABLES
# Aquí están todos los valores que puedes ajustar sin tocar el resto del código.
# ===========================================================================

# Fallback de días máximos de inventario para cuadrantes que NO estén en el
# archivo DB_POLITICA_NACIONAL_DIAS_DE_INVENTARIO_POR_CUADRANTE.xlsx.
# En condiciones normales este valor no se usa porque la política cubre todos
# los cuadrantes activos.
# ↓ MODIFICABLE
DIAS_EXCESO_ORIGEN = 20

# Días de inventario MÍNIMOS que debe conservar la tienda origen después del traslado.
# Se aplica a todos los cuadrantes por igual.
# Si el stock no alcanza para conservar este mínimo, no se realiza el traslado.
# ↓ MODIFICABLE
DIAS_MINIMO_ORIGEN = 10

# Cantidad mínima de cajas que debe quedar en la tienda origen, independientemente
# de los días. Se aplica el máximo entre este valor y DIAS_MINIMO_ORIGEN × consumo.
# Valor general (fallback) — ver CAJAS_MINIMO_POR_CUADRANTE para excepciones.
# ↓ MODIFICABLE
CAJAS_MINIMO_ORIGEN = 1

# Excepciones al mínimo de cajas por cuadrante (se superponen a CAJAS_MINIMO_ORIGEN).
# Q17 y Q18 son productos refrigerados/congelados con mayor rotación; la política
# permite retener menos stock en origen antes de transferir.
CAJAS_MINIMO_POR_CUADRANTE = {
    "Q17 - CAVA REFRIGERADO": 1,
    "Q18 - CAVA CONGELADO":   0.8,
}

# Cuadrantes excluidos del análisis de traslados.
# Basta con el código del cuadrante (ej. "Q01", "Q12") — no hace falta el nombre completo.
# Dejar vacío para analizar todos los cuadrantes.
# Ejemplo: {"Q01", "Q12"} excluye ESTIBADOS y CUIDADO PERSONAL-LIMPIEZA HOGAR.
# ↓ MODIFICABLE
CUADRANTES_EXCLUIDOS: set = {"Q01","Q17","Q18","Q25"}

# Cantidad mínima de cajas por traslado individual.
# Transferencias que resulten en menos de este número de cajas enteras se descartan.
# ↓ MODIFICABLE
CAJAS_MINIMO_TRASLADO = 1

# Respaldo para el Filtro 1 (anti sobre-stock combinado) cuando un item no tiene
# cuadrante mapeado en la política nacional. En el caso normal, el Filtro 1 usa
# el máximo de días del cuadrante propio del item, no este valor.
# ↓ MODIFICABLE
DIAS_MAXIMO_DESTINO = 15

# Mínimo de cajas TOTALES que debe enviar una tienda origen para justificar
# el operativo logístico. Si la suma de todas sus cajas es menor a este valor
# se eliminan todos sus traslados en la verificación final.
# ↓ MODIFICABLE
CAJAS_MINIMO_TOTAL_ORIGEN = 15

# ===========================================================================
# FIN DE PARÁMETROS CONFIGURABLES
# ===========================================================================

BASE   = Path(__file__).parent
INPUT  = BASE / "Input"
OUTPUT = BASE / "Output"
OUTPUT.mkdir(exist_ok=True)

hoy = date.today().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Helpers de formato Excel (sin lógica de negocio)
# ---------------------------------------------------------------------------

def _apply_header_style(ws):
    """Aplica fondo azul oscuro y texto blanco en negrita a la fila de encabezados."""
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)


def _autofit(ws):
    """Ajusta el ancho de cada columna al contenido más largo (máximo 35 caracteres)."""
    for col in ws.columns:
        max_len = max((len(str(c.value)) if c.value else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 35)


def _write_resumen_ejecutivo(ws, df_t, df_excluidos=None, df_resumen_zona=None, df_resumen_cuadrante=None, df_resumen_tienda=None):
    """Escribe la hoja Resumen Ejecutivo con celdas openpyxl directas (no DataFrame.to_excel), para controlar colores y layout por sección."""
    from openpyxl.styles import Border, Side, numbers
    from openpyxl.utils import get_column_letter

    # Paleta de colores por tipo de celda
    C_TITULO   = PatternFill("solid", fgColor="1F4E79")  # Azul oscuro — título principal
    C_SECCION  = PatternFill("solid", fgColor="2E75B6")  # Azul medio  — encabezado de sección
    C_LABEL    = PatternFill("solid", fgColor="D6E4F0")  # Azul claro  — etiqueta de fila
    C_VALOR    = PatternFill("solid", fgColor="EBF5FB")  # Blanco azulado — valor
    C_ALERTA   = PatternFill("solid", fgColor="FCE4D6")  # Salmón — indicadores negativos
    C_POSITIVO = PatternFill("solid", fgColor="E2EFDA")  # Verde claro — indicadores positivos

    F_TITULO  = Font(color="FFFFFF", bold=True, size=14)
    F_SEC     = Font(color="FFFFFF", bold=True, size=11)
    F_LABEL   = Font(bold=True, size=10)
    F_VAL     = Font(size=10)
    F_NOTE    = Font(color="595959", italic=True, size=9)

    thin  = Side(style="thin", color="BFBFBF")
    borde = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 22

    def _merge_title(row, text, fill, font, cols="A:D"):
        c1, c2 = cols.split(":")
        ws.merge_cells(f"{c1}{row}:{c2}{row}")
        cell = ws[f"{c1}{row}"]
        cell.value = text
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 22

    def _row(row, label, *vals, lbl_fill=C_LABEL, val_fill=C_VALOR, lbl_font=F_LABEL, val_font=F_VAL):
        cols = ["A", "B", "C", "D"]
        cell = ws[f"A{row}"]
        cell.value = label
        cell.fill = lbl_fill
        cell.font = lbl_font
        cell.alignment = Alignment(vertical="center")
        cell.border = borde
        for i, v in enumerate(vals):
            c = ws[f"{cols[i+1]}{row}"]
            c.value = v
            c.fill = val_fill
            c.font = val_font
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = borde
        ws.row_dimensions[row].height = 18

    def _blank(row):
        ws.row_dimensions[row].height = 6

    # ── Calcular métricas para el resumen ────────────────────────────────────
    if not df_t.empty:
        tot_traslados   = len(df_t)
        traslados_globales = df_t.groupby(["Bodega Origen", "Bodega Destino"]).ngroups
        tot_cajas       = int(df_t["Cajas a Trasladar"].sum())
        tiendas_origen  = df_t["Bodega Origen"].nunique()
        tiendas_destino = df_t["Bodega Destino"].nunique()
        zonas_total     = len(set(df_t["Zona Origen"]) | set(df_t["Zona Destino"]))
        items_unicos    = df_t["Item"].nunique()

        avg_dias_orig_antes  = df_t["Dias Inv Origen (antes)"].mean()
        avg_dias_orig_desp   = df_t["Dias Inv Origen (despues)"].mean()
        avg_dias_dest_antes  = df_t["Dias Inv Destino (antes)"].mean()
        avg_dias_dest_desp   = df_t["Dias Inv Destino (despues)"].mean()
        reduccion_dias       = avg_dias_orig_antes - avg_dias_orig_desp
        pct_reduccion        = reduccion_dias / avg_dias_orig_antes * 100 if avg_dias_orig_antes else 0
        incremento_dias      = avg_dias_dest_desp - avg_dias_dest_antes

        top_items = (
            df_t.groupby(["Item", "Producto"])["Cajas a Trasladar"]
            .sum().reset_index()
            .sort_values("Cajas a Trasladar", ascending=False)
            .head(5)
        )
        top_origen = (
            df_t.groupby(["Bodega Origen", "Tienda Origen"])["Cajas a Trasladar"]
            .sum().reset_index()
            .sort_values("Cajas a Trasladar", ascending=False)
            .head(5)
        )
    else:
        tot_traslados = traslados_globales = tot_cajas = 0
        tiendas_origen = tiendas_destino = zonas_total = items_unicos = 0
        avg_dias_orig_antes = avg_dias_orig_desp = 0
        avg_dias_dest_antes = avg_dias_dest_desp = 0
        reduccion_dias = pct_reduccion = incremento_dias = 0
        top_items = top_origen = pd.DataFrame()

    df_excluidos = df_excluidos if df_excluidos is not None else pd.DataFrame()
    excl_total      = len(df_excluidos)
    excl_cajas      = int(df_excluidos["Cajas a Trasladar"].sum()) if not df_excluidos.empty else 0
    excl_sobrestock = (
        int((df_excluidos["Filtro"] == "sobrestock_combinado").sum())
        if not df_excluidos.empty else 0
    )
    excl_volumen = (
        int((df_excluidos["Filtro"] == "volumen_minimo").sum())
        if not df_excluidos.empty else 0
    )

    # ── Escribir celdas en la hoja ────────────────────────────────────────────
    r = 1
    _merge_title(r, f"RESUMEN EJECUTIVO — PLAN DE TRASLADOS NACIONAL   ({hoy[:4]}-{hoy[4:6]}-{hoy[6:]})", C_TITULO, F_TITULO)

    r += 1; _blank(r)

    r += 1; _merge_title(r, "TOTALES GENERALES", C_SECCION, F_SEC)
    r += 1; _row(r, "Líneas de traslado (item×origen×destino)",  tot_traslados, "", "")
    r += 1; _row(r, "Traslados globales (rutas origen→destino)", traslados_globales, "", "")
    r += 1; _row(r, "Total cajas a trasladar",       tot_cajas,  "", "")
    r += 1; _row(r, "Items únicos involucrados",         items_unicos, "", "")
    r += 1; _row(r, "Tiendas que envían (origen)",       tiendas_origen,  "", "")
    r += 1; _row(r, "Tiendas que reciben (destino)",     tiendas_destino, "", "")
    r += 1; _row(r, "Zonas involucradas",                zonas_total, "", "")

    r += 1; _blank(r)

    r += 1; _merge_title(r, "IMPACTO EN INVENTARIO — TIENDA ORIGEN", C_SECCION, F_SEC)
    r += 1; _row(r, "", "Antes", "Después", "Reducción",
                    lbl_fill=C_SECCION, val_fill=C_SECCION, lbl_font=F_SEC,
                    val_font=Font(color="FFFFFF", bold=True, size=10))
    r += 1; _row(r, "Días de inventario promedio",
                    f"{avg_dias_orig_antes:.1f} días",
                    f"{avg_dias_orig_desp:.1f} días",
                    f"-{reduccion_dias:.1f} días  ({pct_reduccion:.0f}%)",
                    val_fill=C_POSITIVO)

    r += 1; _blank(r)

    r += 1; _merge_title(r, "IMPACTO EN INVENTARIO — TIENDA DESTINO", C_SECCION, F_SEC)
    r += 1; _row(r, "", "Antes", "Después", "Incremento",
                    lbl_fill=C_SECCION, val_fill=C_SECCION, lbl_font=F_SEC,
                    val_font=Font(color="FFFFFF", bold=True, size=10))
    r += 1; _row(r, "Días de inventario promedio",
                    f"{avg_dias_dest_antes:.1f} días",
                    f"{avg_dias_dest_desp:.1f} días",
                    f"+{incremento_dias:.1f} días",
                    val_fill=C_POSITIVO)

    r += 1; _blank(r)

    r += 1; _merge_title(r, "TOP 5 ITEMS POR CAJAS TRASLADADAS", C_SECCION, F_SEC)
    r += 1; _row(r, "Item — Producto", "Cajas", "", "",
                    lbl_fill=C_SECCION, val_fill=C_SECCION, lbl_font=F_SEC,
                    val_font=Font(color="FFFFFF", bold=True, size=10))
    if not top_items.empty:
        for _, ti in top_items.iterrows():
            r += 1
            _row(r, f"  {ti['Item']}  {ti['Producto']}", int(ti["Cajas a Trasladar"]), "", "")

    r += 1; _blank(r)

    r += 1; _merge_title(r, "TOP 5 TIENDAS ORIGEN (MÁS CAJAS ENVIADAS)", C_SECCION, F_SEC)
    r += 1; _row(r, "Bodega — Tienda", "Cajas enviadas", "", "",
                    lbl_fill=C_SECCION, val_fill=C_SECCION, lbl_font=F_SEC,
                    val_font=Font(color="FFFFFF", bold=True, size=10))
    if not top_origen.empty:
        for _, to in top_origen.iterrows():
            r += 1
            _row(r, f"  {to['Bodega Origen']}  {to['Tienda Origen']}", int(to["Cajas a Trasladar"]), "", "")

    r += 1; _blank(r)

    # ── Todas las tiendas origen con cajas a enviar ──────────────────────────
    if df_resumen_tienda is not None and not df_resumen_tienda.empty:
        df_envia = df_resumen_tienda[df_resumen_tienda["Cajas Enviadas"] > 0].sort_values(
            "Cajas Enviadas", ascending=False
        )
        if not df_envia.empty:
            ws.column_dimensions["E"].width = 22

            r += 1; _merge_title(r, f"TODAS LAS TIENDAS ORIGEN — CAJAS A ENVIAR ({len(df_envia)} tiendas)", C_SECCION, F_SEC, cols="A:E")
            r += 1
            for ci, h in enumerate(["Bodega", "Tienda", "Zona", "Traslados Enviados", "Cajas a Enviar"]):
                col_letra = ["A", "B", "C", "D", "E"][ci]
                c = ws[f"{col_letra}{r}"]
                c.value = h
                c.fill = C_SECCION
                c.font = Font(color="FFFFFF", bold=True, size=10)
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                c.border = borde
            ws.row_dimensions[r].height = 18

            for _, fila in df_envia.iterrows():
                r += 1
                for ci, (key, alin) in enumerate([
                    ("Bodega", "center"), ("Tienda", "left"), ("Zona", "center"),
                    ("Traslados Enviados", "center"), ("Cajas Enviadas", "center"),
                ]):
                    col_letra = ["A", "B", "C", "D", "E"][ci]
                    c = ws[f"{col_letra}{r}"]
                    c.value = fila[key]
                    c.fill = C_LABEL if ci == 0 else C_VALOR
                    c.font = F_LABEL if ci == 0 else F_VAL
                    c.alignment = Alignment(horizontal=alin, vertical="center")
                    c.border = borde
                ws.row_dimensions[r].height = 18

            r += 1; _blank(r)

    r += 1; _merge_title(r, "VERIFICACIÓN — TRASLADOS ELIMINADOS", C_SECCION, F_SEC)
    r += 1; _row(r, "Traslados eliminados en verificación", excl_total, "", "",
                    val_fill=C_ALERTA if excl_total > 0 else C_VALOR)
    r += 1; _row(r, "  → Destino ya alcanzaba el máximo con traslados previos",
                    excl_sobrestock, "", "",
                    val_fill=C_ALERTA if excl_sobrestock > 0 else C_VALOR)
    r += 1; _row(r, "  → Origen con volumen total insuficiente",
                    excl_volumen, "", "",
                    val_fill=C_ALERTA if excl_volumen > 0 else C_VALOR)
    r += 1; _row(r, "Cajas no trasladadas (retenidas por verificación)", excl_cajas, "", "",
                    val_fill=C_ALERTA if excl_cajas > 0 else C_VALOR)
    if excl_total > 0:
        r += 1
        cell = ws[f"A{r}"]
        cell.value = "  * Los traslados eliminados no se incluyen en el conteo final."
        cell.font = F_NOTE
        ws.row_dimensions[r].height = 14

    # ── Resumen por Zona ─────────────────────────────────────────────────────
    if df_resumen_zona is not None and not df_resumen_zona.empty:
        ws.column_dimensions["E"].width = 22
        ws.column_dimensions["F"].width = 22

        r += 1; _blank(r)
        r += 1
        ws.merge_cells(f"A{r}:F{r}")
        cell = ws[f"A{r}"]
        cell.value = "RESUMEN POR ZONA"
        cell.fill = C_SECCION
        cell.font = F_SEC
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 22

        r += 1
        encabezados_zona = [
            "Zona", "Traslados Realizados", "Cajas Enviadas",
            "Traslados Recibidos", "Cajas Recibidas", "Traslados Globales (rutas)",
        ]
        for ci, h in enumerate(encabezados_zona):
            col_letra = ["A", "B", "C", "D", "E", "F"][ci]
            c = ws[f"{col_letra}{r}"]
            c.value = h
            c.fill = C_SECCION
            c.font = Font(color="FFFFFF", bold=True, size=10)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = borde
        ws.row_dimensions[r].height = 18

        col_keys = [
            "Zona", "Traslados_Realizados", "Cajas_Enviadas",
            "Traslados_Recibidos", "Cajas_Recibidas", "Traslados_Globales",
        ]
        for _, fila in df_resumen_zona.sort_values("Zona").iterrows():
            r += 1
            for ci, key in enumerate(col_keys):
                col_letra = ["A", "B", "C", "D", "E", "F"][ci]
                c = ws[f"{col_letra}{r}"]
                c.value = fila[key]
                c.fill = C_LABEL if ci == 0 else C_VALOR
                c.font = F_LABEL if ci == 0 else F_VAL
                c.alignment = Alignment(
                    horizontal="left" if ci == 0 else "center",
                    vertical="center",
                )
                c.border = borde
            ws.row_dimensions[r].height = 18

    # ── Resumen por Cuadrante ────────────────────────────────────────────────
    if df_resumen_cuadrante is not None and not df_resumen_cuadrante.empty:
        r += 1; _blank(r)
        r += 1; _merge_title(r, "RESUMEN POR CUADRANTE", C_SECCION, F_SEC)
        r += 1
        for ci, h in enumerate(["Cuadrante", "Traslados Realizados", "Cajas Enviadas"]):
            col_letra = ["A", "B", "C"][ci]
            c = ws[f"{col_letra}{r}"]
            c.value = h
            c.fill = C_SECCION
            c.font = Font(color="FFFFFF", bold=True, size=10)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = borde
        ws.row_dimensions[r].height = 18

        col_keys_cdt = ["Cuadrante", "Traslados_Realizados", "Cajas_Enviadas"]
        for _, fila in df_resumen_cuadrante.sort_values("Cuadrante").iterrows():
            r += 1
            for ci, key in enumerate(col_keys_cdt):
                col_letra = ["A", "B", "C"][ci]
                c = ws[f"{col_letra}{r}"]
                c.value = fila[key]
                c.fill = C_LABEL if ci == 0 else C_VALOR
                c.font = F_LABEL if ci == 0 else F_VAL
                c.alignment = Alignment(
                    horizontal="left" if ci == 0 else "center",
                    vertical="center",
                )
                c.border = borde
            ws.row_dimensions[r].height = 18


# ---------------------------------------------------------------------------
# SECCIÓN 1 — Cargar archivos de entrada
# Lee los Excel fuente. Si cambian los nombres de archivo, actualizarlos aquí.
# ---------------------------------------------------------------------------

print("Cargando archivos de entrada...")

# Maestro de tiendas: contiene código de bodega, zona y nombre de tienda
tiendas = pd.read_excel(INPUT / "DB_Tiendas.xlsx")
print(f"  DB_Tiendas          : {len(tiendas):>6} tiendas")

# Inventario actual por tienda-producto: stock, consumo diario, días de inventario
inventario = pd.read_excel(
    INPUT / "DB_CELES.xlsx",
    sheet_name="Items",
    usecols=[
        "Código de Bodega",
        "Código de Producto",
        "Nombre de Producto",
        "Cuadrante de Producto",
        "(=) Inventario Total",
        "Consumo Diario (Unidades de Distribución)",
    ],
)
print(f"  DB_CELES (Items)    : {len(inventario):>6} filas")

# Normalizar nombres de columnas del nuevo archivo DB_CELES al estándar interno
inventario = inventario.rename(columns={
    "Código de Bodega":                           "C BODEGA",
    "Código de Producto":                         "Item",
    "Consumo Diario (Unidades de Distribución)":  "Consumo diario",
})

# Portafolio autorizado FRUVER: define qué productos puede recibir cada tienda (solo Q16)
portafolio = pd.read_excel(INPUT / "DB_PORTAFOLIO_FRUVER.xlsx")
print(f"  Portafolio FRUVER   : {len(portafolio):>6} pares tienda-item")

# Limpiar espacios en nombres de columnas para evitar errores de lectura
tiendas.columns    = tiendas.columns.str.strip()
inventario.columns = inventario.columns.str.strip()
portafolio.columns = portafolio.columns.str.strip()

# Política nacional de días de inventario máximos por cuadrante
politica = pd.read_excel(INPUT / "DB_POLITICA_NACIONAL_DIAS_DE_INVENTARIO_POR_CUADRANTE.xlsx")
politica.columns = politica.columns.str.strip()

_dias_faltantes = politica.loc[politica["Dias de Inventario Maximos"].isna(), "CUADRANTE"].tolist()
if _dias_faltantes:
    raise ValueError(
        f"DB_POLITICA_NACIONAL: falta 'Dias de Inventario Maximos' para: {_dias_faltantes}"
    )

dias_max_cuadrante: dict[str, int] = dict(
    zip(politica["CUADRANTE"].str.strip(),
        politica["Dias de Inventario Maximos"].astype(int))
)
print(f"  Política cuadrantes : {len(dias_max_cuadrante):>6} cuadrantes configurados")


# ---------------------------------------------------------------------------
# SECCIÓN 2 — Preparar cruces entre archivos
# Une los tres archivos usando las claves comunes entre ellos.
# ---------------------------------------------------------------------------

# Mapa BODEGA → {Zona, NOMBRE DE TIENDA}
# Permite enriquecer el inventario con zona y nombre de cada tienda
zona_map = tiendas.set_index("BODEGA")[["Zona", "NOMBRE DE TIENDA"]].to_dict("index")

inventario["Zona"] = inventario["C BODEGA"].map(
    lambda b: zona_map.get(b, {}).get("Zona", "Sin Zona")
)
inventario["Nombre Tienda"] = inventario["C BODEGA"].map(
    lambda b: zona_map.get(b, {}).get("NOMBRE DE TIENDA", b)
)

# Mapa C.O → BODEGA: convierte el código de portafolio al código de bodega
co_map = tiendas.set_index("C.O")["BODEGA"].to_dict()
portafolio["BODEGA"] = portafolio["COD SIESA"].map(co_map)
portafolio_item_col  = "DB_Portafolio_Fruver.ITEM"

# Set de pares (BODEGA, ITEM) autorizados — la consulta es O(1) por ser un set
# Esta es la restricción de portafolio: ningún traslado puede ir a una tienda
# que no tenga el par (BODEGA, ITEM) en este conjunto.
portafolio_valido = set(
    zip(
        portafolio["BODEGA"].astype(str),
        portafolio[portafolio_item_col].astype(str),
    )
)


# ---------------------------------------------------------------------------
# SECCIÓN 3 — Limpiar y preparar el inventario
# Convierte columnas a tipos numéricos y calcula los días de inventario reales.
# ---------------------------------------------------------------------------

inv = inventario.copy()
inv["Item"]            = inv["Item"].astype(str)
inv["Inventario Total"] = pd.to_numeric(inv["(=) Inventario Total"], errors="coerce").fillna(0)
inv["Consumo diario"]   = pd.to_numeric(inv["Consumo diario"],        errors="coerce").fillna(0)

_mask_consumo = inv["Consumo diario"] > 0
inv["Dias Calc"] = 0.0
inv.loc[_mask_consumo, "Dias Calc"] = (
    inv.loc[_mask_consumo, "Inventario Total"] / inv.loc[_mask_consumo, "Consumo diario"]
)

# Umbral de días máximos por cuadrante para cada fila (lookup sobre la política)
inv["Dias Max"] = inv["Cuadrante de Producto"].map(
    lambda c: dias_max_cuadrante.get(str(c).strip(), DIAS_EXCESO_ORIGEN)
)

# Garantizar índice posicional 0…N-1 para que los arrays NumPy sean indexables directamente
inv = inv.reset_index(drop=True)

# Pre-extraer columnas como arrays Python/NumPy — elimina el overhead de pandas
# en el loop greedy que accede a estas columnas millones de veces
arr_bodega    = inv["C BODEGA"].tolist()
arr_item      = inv["Item"].tolist()
arr_zona      = inv["Zona"].tolist()
arr_nombre    = inv["Nombre Tienda"].tolist()
arr_consumo   = inv["Consumo diario"].to_numpy()
arr_cuadrante = inv["Cuadrante de Producto"].tolist()
arr_producto  = inv["Nombre de Producto"].tolist()
arr_inventario = inv["Inventario Total"].to_numpy()

# Índice item → lista de filas del inventario — para encontrar destinos rápidamente
dest_por_item: dict[str, list[int]] = {}
for i, it in enumerate(arr_item):
    dest_por_item.setdefault(it, []).append(i)


# ---------------------------------------------------------------------------
# SECCIÓN 4 — Algoritmo principal de traslados
#
# Lógica general:
#   Por cada tienda-producto con exceso de días, busca destinos que necesiten
#   ese mismo producto dentro de la misma zona, respetando portafolio y
#   restricciones de stock.
#
# stock_simulado: copia mutable del inventario. Se actualiza después de cada
#   traslado para que los siguientes cálculos reflejen el estado real acumulado.
# ---------------------------------------------------------------------------

traslados: list[dict] = []   # Traslados viables encontrados

stock_simulado = arr_inventario.copy()

# Filtrar solo tiendas-producto que son candidatas a enviar stock (fuentes):
#   - Consumo diario > 0 (el producto se vende; sin consumo no aplica)
#   - Inventario Total > 0 (tiene stock físico)
#   - Días de inventario > umbral del cuadrante según política nacional
fuentes = inv[
    (inv["Dias Calc"] > inv["Dias Max"]) &
    (inv["Consumo diario"] > 0) &
    (inv["Inventario Total"] > 0) &
    (~inv["Cuadrante de Producto"].apply(
        lambda c: any(str(c).startswith(e) for e in CUADRANTES_EXCLUIDOS)
    ))
].copy()

# Procesar primero las tiendas-producto con más días de inventario acumulado:
# son las de mayor riesgo de merma y deben tener prioridad sobre la capacidad
# disponible en los destinos.
fuentes = fuentes.sort_values("Dias Calc", ascending=False)

ya_fue_origen: set[tuple] = set()   # (bodega, item) que ya enviaron en este run

_total_fuentes   = len(fuentes)
_hitos_progreso  = {int(_total_fuentes * p) for p in (0.25, 0.5, 0.75)} if _total_fuentes else set()
print(f"\nIniciando algoritmo de traslados: {_total_fuentes} fuentes con exceso de inventario...")

fuente_indices = fuentes.index.tolist()
for _idx_prog, src_idx in enumerate(fuente_indices):
    if _idx_prog in _hitos_progreso:
        pct = _idx_prog * 100 // _total_fuentes
        print(f"  Procesando... {pct}%  ({_idx_prog}/{_total_fuentes})", flush=True)
    bodega_src    = arr_bodega[src_idx]
    item          = arr_item[src_idx]
    zona_src      = arr_zona[src_idx]
    nombre_src    = arr_nombre[src_idx]
    consumo_src   = arr_consumo[src_idx]
    cuadrante_src    = str(arr_cuadrante[src_idx]).strip()
    dias_max_src     = dias_max_cuadrante.get(cuadrante_src, DIAS_EXCESO_ORIGEN)
    es_fruver        = cuadrante_src == "Q16 - FRUVER"
    cajas_minimo_src = CAJAS_MINIMO_POR_CUADRANTE.get(cuadrante_src, CAJAS_MINIMO_ORIGEN)

    stock_actual_src = stock_simulado[src_idx]

    # Calcular cuántas cajas puede enviar el origen sin quedarse por debajo del mínimo.
    # Se toma el mayor valor entre el mínimo en cajas y el mínimo en días × consumo.
    # ↓ Controlado por CAJAS_MINIMO_POR_CUADRANTE / CAJAS_MINIMO_ORIGEN y DIAS_MINIMO_ORIGEN
    minimo_origen = max(cajas_minimo_src, consumo_src * DIAS_MINIMO_ORIGEN)
    disponible    = math.floor(stock_actual_src - minimo_origen)

    if disponible < CAJAS_MINIMO_TRASLADO:
        continue

    ya_fue_origen.add((bodega_src, item))
    dias_src_antes = stock_actual_src / consumo_src

    # Buscar destinos elegibles para este item (solo misma zona)
    candidatos_mismo: list[tuple] = []

    for dst_idx in dest_por_item.get(item, []):
        bodega_dst = arr_bodega[dst_idx]

        if bodega_dst == bodega_src:
            continue
        if (bodega_dst, item) in ya_fue_origen:
            continue
        if es_fruver and (bodega_dst, item) not in portafolio_valido:
            continue

        consumo_dst = arr_consumo[dst_idx]
        if consumo_dst <= 0:
            continue

        stock_dst_sim = stock_simulado[dst_idx]
        dias_dst      = stock_dst_sim / consumo_dst

        if dias_dst >= dias_max_src:
            continue

        if arr_zona[dst_idx] == zona_src:
            candidatos_mismo.append((dst_idx, dias_dst))

    # Ordenar por urgencia: primero los destinos con menos días de inventario
    candidatos_mismo.sort(key=lambda x: x[1])

    tipo = "Misma Zona"
    for dst_idx, dias_dst_antes in candidatos_mismo:
        if disponible < CAJAS_MINIMO_TRASLADO:
            break

        bodega_dst    = arr_bodega[dst_idx]
        consumo_dst   = arr_consumo[dst_idx]
        stock_dst_sim = stock_simulado[dst_idx]

        necesidad = math.ceil((dias_max_src - dias_dst_antes) * consumo_dst)
        cajas = min(necesidad, disponible)
        cajas_max_dst = math.floor(dias_max_src * consumo_dst - stock_dst_sim)
        cajas = min(cajas, cajas_max_dst)

        if cajas < CAJAS_MINIMO_TRASLADO:
            continue

        stock_src_despues = stock_simulado[src_idx] - cajas
        stock_dst_despues = stock_dst_sim + cajas
        dias_src_despues  = stock_src_despues / consumo_src
        dias_dst_despues  = stock_dst_despues / consumo_dst

        traslados.append(
            {
                "Tienda Origen":                nombre_src,
                "Zona Origen":                  zona_src,
                "Bodega Origen":                bodega_src,
                "Tienda Destino":               arr_nombre[dst_idx],
                "Zona Destino":                 arr_zona[dst_idx],
                "Bodega Destino":               bodega_dst,
                "Item":                         item,
                "Producto":                     arr_producto[src_idx],
                "Cuadrante":                    cuadrante_src,
                "Cajas a Trasladar":            cajas,
                "Stock Actual Origen":          round(stock_simulado[src_idx], 3),
                "Stock Despues Traslado Origen": round(stock_src_despues, 3),
                "Dias Inv Origen (antes)":      round(dias_src_antes, 1),
                "Dias Inv Origen (despues)":    round(dias_src_despues, 1),
                "Stock Actual Destino":         round(stock_dst_sim, 3),
                "Stock Despues Traslado Destino": round(stock_dst_despues, 3),
                "Dias Inv Destino (antes)":     round(dias_dst_antes, 1),
                "Dias Inv Destino (despues)":   round(dias_dst_despues, 1),
                "Consumo Diario Origen":        round(consumo_src, 4),
                "Consumo Diario Destino":       round(consumo_dst, 4),
                "Tipo":                         tipo,
            }
        )

        # Actualizar stock simulado para reflejar el traslado en iteraciones futuras
        stock_simulado[src_idx] = stock_src_despues
        stock_simulado[dst_idx] = stock_dst_despues
        disponible -= cajas


print(f"  Algoritmo completado: {len(traslados)} traslados encontrados")
print("Aplicando filtros de verificación...")

# ---------------------------------------------------------------------------
# SECCIÓN 5 — Resumen agregado por zona
# Consolida totales de cajas enviadas, recibidas e items sin solución por zona.
# ---------------------------------------------------------------------------

df_traslados = pd.DataFrame(traslados)
if not df_traslados.empty:
    df_traslados["Zona"] = df_traslados["Zona Origen"]

# ---------------------------------------------------------------------------
# VERIFICACIÓN — Sobre-stock combinado en destinos y eficiencia de origen
#
# Filtro 1 — Sobre-stock combinado:
#   Si un destino ya alcanzó el máximo de días de SU cuadrante gracias a un
#   traslado previo en la misma corrida, cualquier traslado posterior a ese
#   mismo (Item, Destino) se elimina. El primer traslado siempre se acepta
#   aunque redondee levemente por encima del umbral. DIAS_MAXIMO_DESTINO solo
#   se usa como respaldo si el item no tiene cuadrante mapeado en la política.
#
# Filtro 2 — Volumen mínimo por origen:
#   Si la suma de todas las cajas que envía una tienda origen es menor a
#   CAJAS_MINIMO_TOTAL_ORIGEN, sus traslados se eliminan porque no justifican
#   el operativo logístico.
# ---------------------------------------------------------------------------

excluidos_verificacion: list[dict] = []

if not df_traslados.empty:
    # ── Filtro 1: sobre-stock combinado ─────────────────────────────────────
    indices_eliminar: list[int] = []

    grupos_destino: dict = {}
    for i, row in df_traslados.iterrows():
        key = (row["Item"], row["Bodega Destino"])
        grupos_destino.setdefault(key, []).append(i)

    for _key, indices in grupos_destino.items():
        if len(indices) < 2:
            continue  # un único traslado: siempre aceptado

        consumo_dst     = df_traslados.loc[indices[0], "Consumo Diario Destino"]
        stock_corriente = df_traslados.loc[indices[0], "Stock Actual Destino"]
        cuadrante_grupo = df_traslados.loc[indices[0], "Cuadrante"]
        limite_grupo    = dias_max_cuadrante.get(cuadrante_grupo, DIAS_MAXIMO_DESTINO)

        for n, i in enumerate(indices):
            cajas         = df_traslados.loc[i, "Cajas a Trasladar"]
            dias_actuales = stock_corriente / consumo_dst if consumo_dst > 0 else 0

            if n > 0 and dias_actuales >= limite_grupo:
                row_dict = df_traslados.loc[i].to_dict()
                row_dict["Filtro"] = "sobrestock_combinado"
                row_dict["Motivo Exclusion"] = (
                    f"Destino ya tiene {round(dias_actuales, 1)} dias por traslados "
                    f"previos (limite: {limite_grupo})"
                )
                excluidos_verificacion.append(row_dict)
                indices_eliminar.append(i)
            else:
                stock_corriente += cajas

    df_traslados = df_traslados.drop(index=indices_eliminar).reset_index(drop=True)

    # ── Filtro 2: volumen insuficiente en origen ─────────────────────────────
    # Si un origen no llega a CAJAS_MINIMO_TOTAL_ORIGEN en total, se eliminan
    # todos sus traslados.
    if not df_traslados.empty:
        totales = (
            df_traslados.groupby("Bodega Origen")["Cajas a Trasladar"]
            .sum()
            .reset_index()
            .rename(columns={"Cajas a Trasladar": "_total_origen"})
        )
        df_merged  = df_traslados.merge(totales, on="Bodega Origen")
        mask_insuf = df_merged["_total_origen"] < CAJAS_MINIMO_TOTAL_ORIGEN

        if mask_insuf.any():
            for _, row in df_merged[mask_insuf].iterrows():
                row_dict = {k: v for k, v in row.items() if k != "_total_origen"}
                row_dict["Filtro"] = "volumen_minimo"
                row_dict["Motivo Exclusion"] = (
                    f"Origen envia solo {int(row['_total_origen'])} cajas en total "
                    f"— minimo requerido: {CAJAS_MINIMO_TOTAL_ORIGEN}"
                )
                excluidos_verificacion.append(row_dict)
            df_traslados = (
                df_merged[~mask_insuf]
                .drop(columns=["_total_origen"])
                .reset_index(drop=True)
            )

df_excluidos_verificacion = pd.DataFrame(excluidos_verificacion)

if not df_traslados.empty:
    res_origen = (
        df_traslados.groupby("Zona Origen")
        .agg(Traslados_Realizados=("Cajas a Trasladar", "count"),
             Cajas_Enviadas=("Cajas a Trasladar", "sum"))
        .rename_axis("Zona").reset_index()
    )
    res_destino = (
        df_traslados.groupby("Zona Destino")
        .agg(Traslados_Recibidos=("Cajas a Trasladar", "count"),
             Cajas_Recibidas=("Cajas a Trasladar", "sum"))
        .rename_axis("Zona").reset_index()
    )
    resumen = (
        res_origen.merge(res_destino, on="Zona", how="outer")
        .fillna(0)
    )
    for col in ["Traslados_Realizados", "Cajas_Enviadas", "Traslados_Recibidos", "Cajas_Recibidas"]:
        resumen[col] = resumen[col].astype(int)

    # Traslados globales por zona: rutas Bodega Origen → Bodega Destino distintas,
    # sin importar cuántos items (líneas) viajen en cada una.
    rutas_zona = (
        df_traslados.drop_duplicates(["Bodega Origen", "Bodega Destino"])
        .groupby("Zona Origen").size()
        .rename_axis("Zona").reset_index(name="Traslados_Globales")
    )
    resumen = resumen.merge(rutas_zona, on="Zona", how="left").fillna(0)
    resumen["Traslados_Globales"] = resumen["Traslados_Globales"].astype(int)
else:
    resumen = pd.DataFrame()

if not df_traslados.empty:
    res_cdt = (
        df_traslados.groupby("Cuadrante")
        .agg(Traslados_Realizados=("Cajas a Trasladar", "count"),
             Cajas_Enviadas=("Cajas a Trasladar", "sum"))
        .reset_index()
    )
    resumen_cuadrante = res_cdt.copy()
    for col in ["Traslados_Realizados", "Cajas_Enviadas"]:
        resumen_cuadrante[col] = resumen_cuadrante[col].astype(int)
else:
    resumen_cuadrante = pd.DataFrame()

if not df_traslados.empty:
    _res_envia = (
        df_traslados.groupby(["Bodega Origen", "Tienda Origen", "Zona Origen"])
        .agg(Traslados_Enviados=("Cajas a Trasladar", "count"),
             Cajas_Enviadas=("Cajas a Trasladar", "sum"))
        .reset_index()
        .rename(columns={
            "Bodega Origen": "Bodega", "Tienda Origen": "Tienda", "Zona Origen": "Zona",
            "Traslados_Enviados": "Traslados Enviados", "Cajas_Enviadas": "Cajas Enviadas",
        })
    )
    _res_recibe = (
        df_traslados.groupby(["Bodega Destino", "Tienda Destino", "Zona Destino"])
        .agg(Traslados_Recibidos=("Cajas a Trasladar", "count"),
             Cajas_Recibidas=("Cajas a Trasladar", "sum"))
        .reset_index()
        .rename(columns={
            "Bodega Destino": "Bodega", "Tienda Destino": "Tienda", "Zona Destino": "Zona",
            "Traslados_Recibidos": "Traslados Recibidos", "Cajas_Recibidas": "Cajas Recibidas",
        })
    )
    resumen_tienda = (
        _res_envia.merge(_res_recibe, on=["Bodega", "Tienda", "Zona"], how="outer")
        .fillna(0)
        .sort_values(["Zona", "Tienda"])
        .reset_index(drop=True)
    )
    for col in ["Traslados Enviados", "Cajas Enviadas", "Traslados Recibidos", "Cajas Recibidas"]:
        resumen_tienda[col] = resumen_tienda[col].astype(int)
else:
    resumen_tienda = pd.DataFrame()


# ---------------------------------------------------------------------------
# SECCIÓN 6 — Exportar resultados a Excel
# Genera el archivo de salida con 5 hojas ordenadas.
# ---------------------------------------------------------------------------

print("\nGenerando archivo Excel de salida...")
out_path = OUTPUT / f"Plan_Traslados_{hoy}.xlsx"

# Orden de columnas en las hojas de traslados:
# primero info del item, luego bloque origen, luego bloque destino
COLS_H1 = [
    "Item", "Producto", "Cuadrante", "Zona", "Cajas a Trasladar", "Tipo",
    # — Bloque Origen —
    "Bodega Origen", "Tienda Origen", "Zona Origen",
    "Stock Actual Origen", "Stock Despues Traslado Origen",
    "Consumo Diario Origen",
    "Dias Inv Origen (antes)", "Dias Inv Origen (despues)",
    # — Bloque Destino —
    "Bodega Destino", "Tienda Destino", "Zona Destino",
    "Stock Actual Destino", "Stock Despues Traslado Destino",
    "Consumo Diario Destino",
    "Dias Inv Destino (antes)", "Dias Inv Destino (despues)",
]

with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
    df_misma = (df_traslados[COLS_H1] if not df_traslados.empty else pd.DataFrame(columns=COLS_H1))

    # Hoja 1: Resumen Ejecutivo (se escribe con openpyxl, no con DataFrame)
    pd.DataFrame().to_excel(writer, sheet_name="Resumen Ejecutivo", index=False)
    _write_resumen_ejecutivo(
        writer.sheets["Resumen Ejecutivo"], df_traslados,
        df_excluidos_verificacion,
        resumen if not resumen.empty else None,
        resumen_cuadrante if not resumen_cuadrante.empty else None,
        resumen_tienda if not resumen_tienda.empty else None,
    )

    # Hoja 2: Traslados dentro de la misma zona
    df_misma.to_excel(writer, sheet_name="Traslados Misma Zona", index=False)
    _apply_header_style(writer.sheets["Traslados Misma Zona"])
    _autofit(writer.sheets["Traslados Misma Zona"])

    # Hoja 3: Resumen por Tienda
    if not resumen_tienda.empty:
        cols_tienda = [
            "Bodega", "Tienda", "Zona",
            "Traslados Enviados", "Cajas Enviadas",
            "Traslados Recibidos", "Cajas Recibidas",
        ]
        resumen_tienda[cols_tienda].to_excel(writer, sheet_name="Resumen por Tienda", index=False)
        _apply_header_style(writer.sheets["Resumen por Tienda"])
        _autofit(writer.sheets["Resumen por Tienda"])


print(f"\n{'='*60}")
print(f"  RESULTADO GENERADO EXITOSAMENTE")
print(f"{'='*60}")
print(f"  Archivo          : {out_path.name}")
print(f"  Carpeta          : {out_path.parent}")
print(f"  Traslados        : {len(df_traslados):>6}")
print(f"  Eliminados (verif): {len(df_excluidos_verificacion):>5}")
print(f"{'='*60}")
