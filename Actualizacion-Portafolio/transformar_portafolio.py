"""
transformar_portafolio.py
─────────────────────────
Lee el archivo fuente de Segmentación Fruver Centro y genera
la tabla en formato largo (una fila por tienda × ítem marcado con X).

Uso:
    python transformar_portafolio.py
    python transformar_portafolio.py --origen "C:/ruta/archivo.xlsx" --destino "C:/ruta/salida.xlsx"
"""

import argparse
import sys
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── CONFIG POR DEFECTO ────────────────────────────────────────
# Ajusta estas rutas si no usas argumentos por línea de comandos
ORIGEN_DEFAULT  = Path(__file__).parent / "Input"  / "Segmentación_Portafolio.xlsx"
DESTINO_DEFAULT = Path(__file__).parent / "Output" / "Portafolio_Fruver_Centro_Largo.xlsx"
HOJA_FUENTE     = "Segmentación CENTRO"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


# ── LECTURA Y TRANSFORMACIÓN ──────────────────────────────────

def leer_fuente(ruta: Path) -> pd.DataFrame:
    log.info(f"Leyendo: {ruta}")
    df = pd.read_excel(ruta, sheet_name=HOJA_FUENTE, header=None)
    log.info(f"Hoja leída: {df.shape[0]} filas × {df.shape[1]} columnas")
    return df


def construir_dim_tiendas(df: pd.DataFrame) -> pd.DataFrame:
    store_cols = list(range(13, df.shape[1]))
    dim = pd.DataFrame({
        "_ColIdx":            store_cols,
        "Zona":               df.iloc[0, store_cols].values,
        "Tienda":             df.iloc[1, store_cols].values,
        "Nombre tienda":      df.iloc[2, store_cols].values,
        "N S E":              df.iloc[3, store_cols].values,
        "TIPO DE PORTAFOLIO": df.iloc[7, store_cols].values,
        "N° Muebles Fruver":  df.iloc[5, store_cols].values,
    })
    dim = dim[dim["Tienda"].astype(str).str.match(r"^S[\dA-Z]")].copy()
    dim["_ColIdx"] = dim["_ColIdx"].astype(int)
    log.info(f"Tiendas identificadas: {len(dim)}")
    return dim


def construir_items(df: pd.DataFrame) -> pd.DataFrame:
    store_cols = list(range(13, df.shape[1]))
    raw = df.iloc[9:, :].copy().reset_index(drop=True)
    raw.columns = (
        ["ITEM", "DESCRIPCION", "CLUSTERIZACIÓN", "CLUSTERIZACIÓN 2", "ESTADO",
         "$ MERMA", "% MERMA", "VENTA", "EXHIBICION",
         "CARAS BSC", "CARAS BSC+EXT", "CARAS BSC+EXT+NEV", "U.M."]
        + [f"_C{i}" for i in store_cols]
    )
    raw = raw[pd.to_numeric(raw["ITEM"], errors="coerce").notna()].copy()
    raw["ITEM"] = raw["ITEM"].astype(int).astype(str)
    log.info(f"Ítems en portafolio: {len(raw)}")
    return raw, store_cols


def transformar(df: pd.DataFrame) -> pd.DataFrame:
    dim        = construir_dim_tiendas(df)
    items, sc  = construir_items(df)

    cols_fijas = ["ITEM", "DESCRIPCION", "CLUSTERIZACIÓN", "CLUSTERIZACIÓN 2", "ESTADO",
                  "EXHIBICION", "CARAS BSC", "CARAS BSC+EXT", "CARAS BSC+EXT+NEV", "U.M."]

    melted = items[cols_fijas + [f"_C{i}" for i in sc]].melt(
        id_vars=cols_fijas, var_name="_ColKey", value_name="_Val"
    )
    melted = melted[melted["_Val"] == "X"].copy()
    melted["_ColIdx"] = melted["_ColKey"].str.replace("_C", "", regex=False).astype(int)

    resultado = melted.merge(dim, on="_ColIdx", how="left")

    final_cols = [
        "Zona", "Tienda", "Nombre tienda", "N S E", "TIPO DE PORTAFOLIO",
        "ITEM", "DESCRIPCION", "CLUSTERIZACIÓN", "CLUSTERIZACIÓN 2", "ESTADO",
        "EXHIBICION", "CARAS BSC", "CARAS BSC+EXT", "CARAS BSC+EXT+NEV", "U.M."
    ]
    resultado = (resultado[final_cols]
                 .sort_values(["Zona", "Tienda", "CLUSTERIZACIÓN", "ITEM"])
                 .reset_index(drop=True))

    log.info(f"Filas generadas: {len(resultado)} "
             f"({resultado['Tienda'].nunique()} tiendas × ítems)")
    return resultado


# ── ESCRITURA EXCEL ───────────────────────────────────────────

def escribir_excel(resultado: pd.DataFrame, destino: Path):
    wb  = Workbook()
    ws  = wb.active
    ws.title = "Portafolio largo"

    # Estilos
    h_font   = Font(name="Arial", bold=True, size=10, color="FFFFFF")
    b_font   = Font(name="Arial", size=10)
    h_tienda = PatternFill("solid", fgColor="1F4E79")
    h_item   = PatternFill("solid", fgColor="375623")
    f_basico = PatternFill("solid", fgColor="DEEBF7")
    f_ext    = PatternFill("solid", fgColor="E2EFDA")
    f_nev    = PatternFill("solid", fgColor="FFF2CC")
    f_act    = PatternFill("solid", fgColor="E2EFDA")
    f_inact  = PatternFill("solid", fgColor="FFDDC1")
    thin     = Side(style="thin",   color="BFBFBF")
    medium   = Side(style="medium", color="1F4E79")
    brd      = Border(left=thin, right=thin, top=thin, bottom=thin)
    c_alin   = Alignment(horizontal="center", vertical="center", wrap_text=True)
    l_alin   = Alignment(horizontal="left",   vertical="center")

    cols_center = {"Zona","Tienda","N S E","TIPO DE PORTAFOLIO",
                   "CLUSTERIZACIÓN","CLUSTERIZACIÓN 2","ESTADO","EXHIBICION","U.M."}
    widths = {
        "Zona": 10, "Tienda": 8, "Nombre tienda": 26, "N S E": 10,
        "TIPO DE PORTAFOLIO": 16, "ITEM": 8, "DESCRIPCION": 38,
        "CLUSTERIZACIÓN": 13, "CLUSTERIZACIÓN 2": 13, "ESTADO": 11,
        "EXHIBICION": 10, "CARAS BSC": 11, "CARAS BSC+EXT": 13,
        "CARAS BSC+EXT+NEV": 16, "U.M.": 8
    }

    headers = resultado.columns.tolist()
    ws.row_dimensions[1].height = 32

    # Encabezado
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font      = h_font
        cell.alignment = c_alin
        cell.border    = brd
        cell.fill      = h_tienda if c <= 5 else h_item

    # Datos
    prev_tienda = None
    for r, row in resultado.iterrows():
        er      = r + 2
        cluster = row["CLUSTERIZACIÓN"]
        estado  = row["ESTADO"]
        tienda  = row["Tienda"]

        row_fill = f_basico if cluster == "BASICO" else (f_ext if cluster == "EXTENDIDO" else f_nev)
        brd_row  = Border(
            left=thin, right=thin, bottom=thin,
            top=(medium if (prev_tienda and tienda != prev_tienda) else thin)
        )

        for c, col in enumerate(headers, 1):
            val  = row[col]
            val  = "" if pd.isna(val) else val
            cell = ws.cell(row=er, column=c, value=val)
            cell.font      = b_font
            cell.border    = brd_row
            cell.fill      = row_fill
            cell.alignment = c_alin if col in cols_center else l_alin

            if col == "ESTADO":
                cell.fill = f_act if estado == "ACTIVO" else f_inact
                cell.font = Font(name="Arial", size=10,
                                 color="375623" if estado == "ACTIVO" else "833C00",
                                 bold=True)
        prev_tienda = tienda

    # Anchos y freeze
    for c, col in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(c)].width = widths.get(col, 12)
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    # Pestaña resumen
    ws2 = wb.create_sheet("Resumen")
    ts  = datetime.now().strftime("%d/%m/%Y %H:%M")
    ws2["A1"] = "Generado el"
    ws2["B1"] = ts
    ws2["A2"] = "Total filas"
    ws2["B2"] = len(resultado)
    ws2["A3"] = "Tiendas"
    ws2["B3"] = resultado["Tienda"].nunique()
    ws2["A4"] = "Ítems únicos"
    ws2["B4"] = resultado["ITEM"].nunique()
    for row in ws2.iter_rows(min_row=1, max_row=4, max_col=2):
        for cell in row:
            cell.font = Font(name="Arial", size=10)

    wb.save(destino)
    log.info(f"Archivo guardado: {destino}")


# ── MAIN ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Transforma portafolio Fruver a formato largo")
    parser.add_argument("--origen",  default=str(ORIGEN_DEFAULT),  help="Ruta del Excel fuente")
    parser.add_argument("--destino", default=str(DESTINO_DEFAULT), help="Ruta del Excel de salida")
    args = parser.parse_args()

    origen  = Path(args.origen)
    destino = Path(args.destino)

    if not origen.exists():
        log.error(f"Archivo no encontrado: {origen}")
        sys.exit(1)

    destino.parent.mkdir(parents=True, exist_ok=True)

    df        = leer_fuente(origen)
    resultado = transformar(df)
    escribir_excel(resultado, destino)
    log.info("✓ Proceso completado")


if __name__ == "__main__":
    main()
