"""Tests del generador de Excel — foco en bugs de agregación de datos."""

import io

import pandas as pd

from core.exporter import generate_excel


def _row(item_code, item_desc, store_code, consumo, existencias, cajas_asignadas,
         cajas_cedi):
    return {
        'item_code': item_code,
        'item_desc': item_desc,
        'store_code': store_code,
        'store_name': f'Tienda {store_code}',
        'zona': 'Zona 1',
        'um': 'UND',
        'consumo_diario': consumo,
        'inventario_tienda': existencias,
        'inventario_transito': 0.0,
        'inventario_efectivo': existencias,
        'dias_proyectados': existencias / consumo,
        'cajas_asignadas': cajas_asignadas,
        'cajas_fase1': 0,
        'cajas_fase2a': 0,
        'cajas_fase2b': cajas_asignadas,
        'cajas_disponibles_cedi': cajas_cedi,
        'vida_util': 10,
    }


def test_descripcion_inconsistente_no_duplica_fila_en_analisis_comprador():
    """Si el mismo item_code llega con `item_desc` ligeramente distinto entre
    tiendas (p.ej. 'MANZANA ROYAL GALA UND' vs 'MANZANA  ROYAL GALA UND' — con
    doble espacio, tal como llega de `DB_Portafolio_Fruver.DESCRIPCION` en el
    preprocesador) "Análisis Comprador" no debe generar una fila por cada
    variante de descripción ni inflar los porcentajes de riesgo.

    Bug real detectado en producción (Output/distribucion_fruver_20260608.xlsx,
    ítem 1671 "MANZANA ROYAL GALA"): `item_base` agrupaba por
    `(item_code, item_desc)` mientras que `item_merma` agrupaba solo por
    `item_code` (exporter.py), así que el merge ('left' sobre item_code)
    pegaba las MISMAS estadísticas de merma (calculadas sobre TODAS las
    tiendas del ítem) a cada fila duplicada — la fila con menos tiendas
    terminaba con `% Tiendas con Riesgo = 2200%`, un valor imposible.
    El arreglo agrupa ambos por `item_code` únicamente, tomando
    `item_desc` con 'first' — igual que ya se hace con `vida_util`.
    """
    rows_normales = [
        _row(1671, 'MANZANA ROYAL GALA UND', f'S{i:02d}', consumo=1.0,
             existencias=20.0, cajas_asignadas=1, cajas_cedi=100)
        for i in range(1, 21)
    ]
    # Una sola tienda con descripción "sucia" (doble espacio) para el mismo ítem.
    rows_sucia = [
        _row(1671, 'MANZANA  ROYAL GALA UND', 'S99', consumo=1.0,
             existencias=20.0, cajas_asignadas=1, cajas_cedi=100)
    ]
    df_output = pd.DataFrame(rows_normales + rows_sucia)
    df_merged = df_output.copy()

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    comprador = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Análisis Comprador')

    filas_item = comprador[comprador['Código Ítem'] == 1671]
    assert len(filas_item) == 1, (
        "El ítem 1671 generó más de una fila en 'Análisis Comprador' — "
        "descripciones con espacios distintos no deben duplicar el ítem "
        f"(filas encontradas: {len(filas_item)})"
    )

    fila = filas_item.iloc[0]
    assert fila['# Tiendas Activas'] == 21
    assert 0 <= fila['% Tiendas con Riesgo'] <= 100, (
        f"% Tiendas con Riesgo imposible: {fila['% Tiendas con Riesgo']}"
    )

    # El mismo patrón de agrupación existe en "Resumen Ítems" — debe estar igual de protegido.
    resumen_items = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen Ítems', skiprows=4)
    filas_resumen = resumen_items[resumen_items['Código Ítem'] == 1671]
    assert len(filas_resumen) == 1, (
        "El ítem 1671 generó más de una fila en 'Resumen Ítems' por "
        f"descripciones inconsistentes (filas encontradas: {len(filas_resumen)})"
    )
