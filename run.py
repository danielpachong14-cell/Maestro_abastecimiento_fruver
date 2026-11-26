"""Distribuidor Fruver (CLI): lee los Excel de Input/ y escribe en Output/ la distribución del CEDI
(distribucion_fruver_YYYYMMDD.xlsx) y la compra al proveedor (compra_fruver_YYYYMMDD.xlsx).
Uso: python run.py   (si la compra falla, la distribución igual queda guardada)
"""

import logging
import sys
from contextlib import ExitStack
from datetime import date
from pathlib import Path

from core.algorithm import run_distribution
from core.compras import ejecutar_compra
from core.compras_exporter import generate_purchase_excel, resumen_compra_texto
from core.exporter import generate_excel, resumen_renovacion_texto
from core.loader import load_inputs
from core.maestros import ARCHIVO as ARCHIVO_MAESTROS
from core.preprocessor import build_distribution_df

ROOT = Path(__file__).parent
INPUT = ROOT / "Input"
OUTPUT = ROOT / "Output"

OBLIGATORIOS = [INPUT / "Stock.xlsx", INPUT / "Celes.xlsx", INPUT / "Inventario Tiendas.xlsx",
                INPUT / ARCHIVO_MAESTROS]
COMPRAS_TRANSITO_PATH = INPUT / "Compras_Transito.xlsx"  # opcional

log = logging.getLogger("run")


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _cargar():
    """(dfs, extra, alertas_maestros). `extra` trae parámetros y OC para la compra."""
    faltan = [p for p in OBLIGATORIOS if not p.exists()]
    if faltan:
        for p in faltan:
            log.error(f"No se encontró {p}")
        sys.exit(1)
    with ExitStack() as stack:
        abiertos = [stack.enter_context(open(p, "rb")) for p in OBLIGATORIOS]
        oc = stack.enter_context(open(COMPRAS_TRANSITO_PATH, "rb")) if COMPRAS_TRANSITO_PATH.exists() else None
        return load_inputs(*abiertos, compras_transito=oc)


def main():
    _setup_logging()
    log.info("Cargando archivos...")
    try:
        dfs, extra, alertas_maestros = _cargar()

        log.info("Preprocesando datos...")
        df_merged, alertas_preproc = build_distribution_df(dfs)
        if 'excluidas' in dfs:
            log.info(f"  Tiendas excluidas  : {len(dfs['excluidas'])}")
        log.info(f"  {df_merged['item_code'].nunique()} ítems × {df_merged['store_code'].nunique()} tiendas elegibles")

        log.info("Ejecutando distribución...")
        df_output, alertas = run_distribution(df_merged, alertas_extra=alertas_preproc)
    except ValueError as e:
        log.error(f"Error de datos de entrada: {e}")
        sys.exit(1)
    except Exception:
        log.exception("Error inesperado ejecutando la distribución")
        sys.exit(1)

    total_cajas = int(df_output["cajas_asignadas"].sum()) if len(df_output) else 0
    items_dist  = df_output["item_code"].nunique() if len(df_output) else 0
    total_items = df_merged["item_code"].nunique()
    cajas_no_dist = sum(a.get("cajas_sin_distribuir", 0) for a in alertas if a["tipo"] in ("CRÍTICO", "ERROR"))

    log.info(f"  Ítems distribuidos : {items_dist} / {total_items}")
    if len(df_output) and "cajas_fase3" in df_output.columns:
        log.info(f"  Renovación vida útil (Fase 3): {int(df_output['cajas_fase3'].sum())} caja(s)")
        resumen_renov = resumen_renovacion_texto(df_merged, df_output)
        if resumen_renov:
            log.info(f"  Candidatas con ingreso vencido: {resumen_renov}")
    log.info(f"  Total cajas        : {total_cajas:,}")
    log.info(f"  Existencia CEDI    : {'0 OK' if cajas_no_dist == 0 else f'ADVERTENCIA: {cajas_no_dist} cajas sin distribuir'}")

    if alertas:
        log.info(f"Alertas ({len(alertas)}):")
        for a in alertas:
            log.info(f"  [{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    OUTPUT.mkdir(exist_ok=True)
    filename = OUTPUT / f"distribucion_fruver_{date.today().strftime('%Y%m%d')}.xlsx"
    _guardar(filename, generate_excel(df_output, alertas, df_merged))
    log.info(f"Archivo generado: {filename}")

    _generar_compra(dfs, df_output, extra, alertas_maestros)


def _guardar(filename, contenido, nota=""):
    """Escribe el Excel; si está abierto (p. ej. en Excel) muestra un mensaje claro y termina."""
    try:
        filename.write_bytes(contenido)
    except PermissionError:
        log.error(f"No se pudo guardar {filename.name}: el archivo está abierto (¿en Excel?). "
                  f"Ciérralo y vuelve a correr.{' ' + nota if nota else ''}")
        sys.exit(1)


def _generar_compra(dfs, df_output, extra, alertas_maestros):
    """Compra al proveedor. Aislada: un error aquí no afecta la distribución ya guardada."""
    log.info("Calculando compra...")
    try:
        res = ejecutar_compra(dfs, df_output,
                              hojas_parametros=extra.get("parametros_compra"),
                              compras_transito=extra.get("compras_transito"),
                              alertas_maestros=alertas_maestros)
        contenido = generate_purchase_excel(res)
    except ValueError as e:
        log.error(f"Compra NO generada (error de datos): {e}")
        return
    except Exception:
        log.exception("Compra NO generada por un error inesperado (la distribución sí quedó guardada)")
        return

    log.info(f"  Compra: {resumen_compra_texto(res)}")
    if res.alertas:
        log.info(f"Alertas de compra ({len(res.alertas)}):")
        for a in res.alertas:
            log.info(f"  [{a['tipo']}] {a['mensaje']}")
    filename = OUTPUT / f"compra_fruver_{date.today().strftime('%Y%m%d')}.xlsx"
    _guardar(filename, contenido, nota="La distribución sí quedó guardada.")
    log.info(f"Archivo generado: {filename}")


if __name__ == "__main__":
    main()
