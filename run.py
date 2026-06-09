"""Distribuidor Fruver — Isimo.

Lee los 5 Excel de Input/, ejecuta la distribución y guarda el resultado
en Output/distribucion_fruver_YYYYMMDD.xlsx.

Uso:
    source distribuidor-fruver/venv/bin/activate
    python run.py
"""

import sys
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
}

EXCLUIDAS_PATH = INPUT / "Tiendas_No generar pedido.xlsx"


def main():
    print("Cargando archivos...")
    for key, path in FILES.items():
        if not path.exists():
            print(f"ERROR: no se encontró {path}")
            sys.exit(1)

    excluidas_file = open(EXCLUIDAS_PATH, "rb") if EXCLUIDAS_PATH.exists() else None
    dfs = load_files(*[open(p, "rb") for p in FILES.values()], tiendas_excluidas=excluidas_file)

    print("Preprocesando datos...")
    df_merged = build_distribution_df(dfs)
    if 'excluidas' in dfs:
        print(f"  Tiendas excluidas  : {len(dfs['excluidas'])}")
    print(f"  {df_merged['item_code'].nunique()} ítems × {df_merged['store_code'].nunique()} tiendas elegibles")

    print("Ejecutando distribución...")
    df_output, alertas = run_distribution(df_merged)

    total_cajas = int(df_output["cajas_asignadas"].sum()) if len(df_output) else 0
    items_dist  = df_output["item_code"].nunique() if len(df_output) else 0
    total_items = df_merged["item_code"].nunique()
    cajas_no_dist = sum(a.get("cajas_sin_distribuir", 0) for a in alertas if a["tipo"] in ("CRÍTICO", "ERROR"))

    print(f"  Ítems distribuidos : {items_dist} / {total_items}")
    print(f"  Total cajas        : {total_cajas:,}")
    print(f"  Existencia CEDI    : {'0 OK' if cajas_no_dist == 0 else f'ADVERTENCIA: {cajas_no_dist} cajas sin distribuir'}")

    if alertas:
        print(f"\nAlertas ({len(alertas)}):")
        for a in alertas:
            print(f"  [{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    OUTPUT.mkdir(exist_ok=True)
    filename = OUTPUT / f"distribucion_fruver_{date.today().strftime('%Y%m%d')}.xlsx"
    filename.write_bytes(generate_excel(df_output, alertas, df_merged))
    print(f"\nArchivo generado: {filename}")


if __name__ == "__main__":
    main()
