"""Distribuidor Fruver — Isimo.

Lee los Excel de Input/, ejecuta la distribución y guarda el resultado en
Output/distribucion_fruver_YYYYMMDD.xlsx.

Uso:
    source distribuidor-fruver/venv/bin/activate
    python run.py
"""

import logging
import sys
from contextlib import ExitStack
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
INPUT = ROOT / "Input"
OUTPUT = ROOT / "Output"

sys.path.insert(0, str(ROOT / "distribuidor-fruver"))

from core.algorithm import run_distribution
from core.exporter import generate_excel
from core.loader import load_files
from core.preprocessor import build_distribution_df

FILES = {
    "stock":        INPUT / "Stock.xlsx",
    "celes":        INPUT / "Celes.xlsx",
    "portafolio":   INPUT / "DB_Portafolio Fruver.xlsx",
    "tiendas":      INPUT / "DB_Tiendas.xlsx",
    "tiendas_item": INPUT / "DB_Tiendas Por itmes y portafolio.xlsx",
    "espejo":       INPUT / "DB_ProductosEspejo.xlsx",
}

EXCLUIDAS_PATH = INPUT / "Tiendas_No generar pedido.xlsx"

log = logging.getLogger("run")


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    _setup_logging()
    log.info("Cargando archivos...")
    for key, path in FILES.items():
        if not path.exists():
            log.error(f"No se encontró {path}")
            sys.exit(1)

    try:
        with ExitStack() as stack:
            archivos = [stack.enter_context(open(p, "rb")) for p in FILES.values()]
            excluidas_file = (
                stack.enter_context(open(EXCLUIDAS_PATH, "rb")) if EXCLUIDAS_PATH.exists() else None
            )
            dfs = load_files(*archivos, tiendas_excluidas=excluidas_file)

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
    log.info(f"  Total cajas        : {total_cajas:,}")
    log.info(f"  Existencia CEDI    : {'0 OK' if cajas_no_dist == 0 else f'ADVERTENCIA: {cajas_no_dist} cajas sin distribuir'}")

    if alertas:
        log.info(f"Alertas ({len(alertas)}):")
        for a in alertas:
            log.info(f"  [{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    OUTPUT.mkdir(exist_ok=True)
    filename = OUTPUT / f"distribucion_fruver_{date.today().strftime('%Y%m%d')}.xlsx"
    filename.write_bytes(generate_excel(df_output, alertas, df_merged))
    log.info(f"Archivo generado: {filename}")


if __name__ == "__main__":
    main()
