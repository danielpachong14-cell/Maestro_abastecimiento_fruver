"""Tests del generador de Excel — foco en bugs de agregación de datos."""

import io

import pandas as pd

from core.exporter import generate_excel


def _row(item_code, item_desc, store_code, consumo, existencias, cajas_asignadas,
         cajas_cedi, zona='Zona 1', vida_util=10):
    return {
        'item_code': item_code,
        'item_desc': item_desc,
        'store_code': store_code,
        'store_name': f'Tienda {store_code}',
        'zona': zona,
        'um': 'UND',
        'consumo_diario': consumo,
        'inventario_tienda': existencias,
        'inventario_transito': 0.0,
        'inventario_efectivo': existencias,
        'dias_proyectados': existencias / consumo if consumo > 0 else float('inf'),
        'cajas_asignadas': cajas_asignadas,
        'cajas_fase1': 0,
        'cajas_fase2': 0,
        'cajas_fase4': cajas_asignadas,
        'cajas_disponibles_cedi': cajas_cedi,
        'vida_util': vida_util,
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
    assert fila['Tiendas Activas'] == 21
    assert 0 <= fila['% Tiendas Riesgo Merma'] <= 100, (
        f"% Tiendas Riesgo Merma imposible: {fila['% Tiendas Riesgo Merma']}"
    )

    # El mismo patrón de agrupación existe en "Resumen Ítems" — debe estar igual de protegido.
    resumen_items = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen Ítems', skiprows=2)
    filas_resumen = resumen_items[resumen_items['Código Ítem'] == 1671]
    assert len(filas_resumen) == 1, (
        "El ítem 1671 generó más de una fila en 'Resumen Ítems' por "
        f"descripciones inconsistentes (filas encontradas: {len(filas_resumen)})"
    )


def test_dias_inventario_actual_no_contiene_literal_inf():
    """Bug de auditoría: una fila con consumo_diario=0 (dias_proyectados=inf
    antes del pedido) escribía el string literal 'inf' en la columna 'Días
    Inventario Actual' del Excel, a diferencia de su columna hermana 'Días
    Inventario Proyectado' que sí sanitizaba a vacío. Debe quedar vacía
    (NaN) en ambas, no un texto no numérico."""
    df_output = pd.DataFrame([
        _row(99, 'Producto sin consumo', 'S01', consumo=0.0,
             existencias=5.0, cajas_asignadas=2, cajas_cedi=2),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    dist = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Distribución')

    valor = dist.loc[0, 'Días Inventario Actual']
    assert pd.isna(valor), f"Se esperaba celda vacía, se obtuvo {valor!r} (tipo {type(valor)})"
    valor_proy = dist.loc[0, 'Días Inventario Proyectado']
    assert pd.isna(valor_proy), f"Se esperaba celda vacía, se obtuvo {valor_proy!r}"


def test_hoja_distribucion_columnas_y_totales():
    """La hoja Distribución debe traer todas las columnas esperadas y el
    total de 'Pedido Final' debe coincidir con las cajas asignadas."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=3, cajas_cedi=3),
        _row(1, 'Producto A', 'S02', consumo=1.0, existencias=4.0, cajas_asignadas=0, cajas_cedi=3),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    dist = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Distribución')

    columnas_esperadas = [
        'Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona',
        'Código de Producto', 'Nombre Ítem', 'UM', 'Consumo Diario',
        'Stock Antes Pedido', 'Días Inventario Actual', 'Pedido Final',
        'Stock Después Pedido', 'Días Inventario Proyectado',
    ]
    assert list(dist.columns) == columnas_esperadas
    assert int(dist['Pedido Final'].sum()) == 3


def test_hoja_resumen_agrega_por_tienda():
    """La hoja Resumen debe sumar 'Total Cajas' por tienda, no por fila."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=2, cajas_cedi=5),
        _row(2, 'Producto B', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=1, cajas_cedi=1),
        _row(1, 'Producto A', 'S02', consumo=1.0, existencias=4.0, cajas_asignadas=0, cajas_cedi=5),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    resumen = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen')

    fila_s01 = resumen[resumen['Centro Operacional de la Bodega'] == 'S01'].iloc[0]
    assert int(fila_s01['Total Cajas']) == 3


def test_hoja_riesgo_merma_ya_no_se_genera():
    """La hoja 'Riesgo Merma' se eliminó a pedido del usuario — el Excel no
    debe traerla, aunque el cálculo de riesgo de merma por ítem se mantenga
    dentro de 'Análisis Comprador' (columna '% Tiendas Riesgo Merma')."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1,
             cajas_cedi=1, vida_util=30),
    ])
    df_merged = df_output.copy()
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hojas = pd.ExcelFile(io.BytesIO(excel_bytes)).sheet_names
    assert 'Riesgo Merma' not in hojas

    comprador = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Análisis Comprador')
    assert '% Tiendas Riesgo Merma' in comprador.columns
    assert comprador.loc[0, '% Tiendas Riesgo Merma'] == 0


def test_riesgo_sobrestock_usa_umbral_propio_desacoplado_del_algoritmo():
    """UMBRAL_RIESGO_SOBRESTOCK (10 días, exporter.py) es un umbral de reporte
    independiente de TOPE_EXCEDENTE (6 días, algorithm.py) — el tope real que
    usa el algoritmo en Fase 4 para dejar de asignar más sobrante a una
    tienda. Una tienda con 8 días proyectados tras el pedido ya superaría el
    tope real del algoritmo, pero NO debe marcarse en '% Tiendas Sobrestock'
    porque sigue por debajo del umbral de reporte (10 días) —
    prueba que el reporte quedó desacoplado del comportamiento real."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=8, cajas_cedi=20),
        _row(1, 'Producto A', 'S02', consumo=1.0, existencias=0.0, cajas_asignadas=12, cajas_cedi=20),
    ])
    df_merged = df_output.copy()
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    comprador = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Análisis Comprador')

    col_pct = [c for c in comprador.columns if c.startswith('% Tiendas Sobrestock')]
    assert col_pct == ['% Tiendas Sobrestock (>10d)'], list(comprador.columns)

    fila = comprador[comprador['Código Ítem'] == 1].iloc[0]
    # Solo S02 (12 días > 10) cuenta: 1 de 2 tiendas = 50 %; S01 (8 días) queda fuera
    # aunque supere el tope real del algoritmo (6 días).
    assert fila['% Tiendas Sobrestock (>10d)'] == 50


def test_generate_excel_has_data_false_no_rompe():
    """df_output vacío/None no debe lanzar excepción; cada hoja debe quedar
    con sus columnas esperadas pero sin filas."""
    excel_bytes = generate_excel(pd.DataFrame(), alertas=[])
    dist = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Distribución')
    assert len(dist) == 0
    assert 'Pedido Final' in dist.columns

    excel_bytes_none = generate_excel(None, alertas=[])
    dist_none = pd.read_excel(io.BytesIO(excel_bytes_none), sheet_name='Distribución')
    assert len(dist_none) == 0


def test_generate_excel_sin_df_merged_omite_analisis_comprador():
    """Sin df_merged no debe generarse la hoja 'Análisis Comprador'."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=None)
    hojas = pd.ExcelFile(io.BytesIO(excel_bytes)).sheet_names
    assert 'Análisis Comprador' not in hojas


def test_hoja_alertas_contiene_alertas_heterogeneas_sin_error():
    """Alertas de distintos tipos (con distintas claves extra) no deben
    romper la escritura del Excel — pandas rellena con NaN lo que falte."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    alertas = [
        {'tipo': 'CRÍTICO', 'item': 99, 'mensaje': 'Ítem huérfano', 'cajas_sin_distribuir': 5},
        {'tipo': 'ADVERTENCIA', 'item': '-', 'mensaje': 'Sin match en Celes', 'combos_sin_match': 12},
    ]
    excel_bytes = generate_excel(df_output, alertas=alertas)
    alertas_sheet = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Alertas')

    assert len(alertas_sheet) == 2
    assert list(alertas_sheet.columns[:3]) == ['Tipo', 'Ítem', 'Mensaje']
    # CRÍTICO debe ir antes que ADVERTENCIA (orden por severidad).
    assert alertas_sheet.iloc[0]['Tipo'] == 'CRÍTICO'


def test_hoja_alertas_vacia_muestra_ok_sin_alertas():
    """Sin alertas, la hoja debe mostrar una fila 'OK — Sin alertas' en vez
    de quedar completamente vacía."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    alertas_sheet = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Alertas')
    assert len(alertas_sheet) == 1
    assert alertas_sheet.iloc[0]['Tipo'] == 'OK'


# ---------------------------------------------------------------------------
# Hojas de trazabilidad: "Sin Datos Celes" y "Bloqueadas por MAX"
# ---------------------------------------------------------------------------

def _merged_con_exclusiones(filas):
    """df_merged con las columnas que el preprocessor agrega para las hojas de
    trazabilidad. `filas` es una lista de (row_dict, sin_match, causa, max_ex,
    bloqueado)."""
    rows = []
    for row, sin_match, causa, max_ex, bloqueado in filas:
        r = dict(row)
        r['sin_match_celes'] = sin_match
        r['causa_sin_match'] = causa
        r['max_existencias'] = max_ex
        r['bloqueado_por_max'] = bloqueado
        r['excluida_del_reparto'] = sin_match or bloqueado
        rows.append(r)
    return pd.DataFrame(rows)


def _hojas(excel_bytes):
    return pd.ExcelFile(io.BytesIO(excel_bytes)).sheet_names


def test_hoja_sin_datos_celes_lista_las_combinaciones_sin_match():
    """Las filas sin match en Celes no reciben cajas, así que no están en
    df_output — la hoja debe salir de df_merged o quedaría vacía."""
    recibe = _row(100, 'Con datos', 'S01', consumo=2.0, existencias=1.0,
                  cajas_asignadas=5, cajas_cedi=5)
    sin_datos = _row(100, 'Con datos', 'S02', consumo=0.0, existencias=0.0,
                     cajas_asignadas=0, cajas_cedi=5)
    df_output = pd.DataFrame([recibe])          # S02 no recibió nada
    df_merged = _merged_con_exclusiones([
        (recibe, False, '', 0, False),
        (sin_datos, True, 'Par tienda-ítem faltante', 0, False),
    ])

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hoja = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Sin Datos Celes')

    assert len(hoja) == 1
    fila = hoja.iloc[0]
    assert fila['Tienda'] == 'S02'
    assert fila['Código Ítem'] == 100
    assert fila['Causa'] == 'Par tienda-ítem faltante'
    assert fila['Cajas Forzadas'] == 0


def test_hoja_bloqueadas_por_max_muestra_stock_y_maximo():
    """Las columnas que responden 'por qué no se le envió': Existencias +
    Tránsito debe salir >= MAX."""
    bloqueada = _row(200, 'Producto B', 'S05', consumo=1.0, existencias=4.0,
                     cajas_asignadas=0, cajas_cedi=3)
    bloqueada['inventario_transito'] = 3.0
    bloqueada['inventario_efectivo'] = 7.0
    normal = _row(200, 'Producto B', 'S06', consumo=1.0, existencias=0.0,
                  cajas_asignadas=3, cajas_cedi=3)
    df_output = pd.DataFrame([normal])
    df_merged = _merged_con_exclusiones([
        (bloqueada, False, '', 6, True),
        (normal, False, '', 0, False),
    ])

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hoja = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Bloqueadas por MAX')

    assert len(hoja) == 1
    fila = hoja.iloc[0]
    assert fila['Tienda'] == 'S05'
    assert fila['Existencias'] == 4.0
    assert fila['Tránsito'] == 3.0
    assert fila['Existencias + Tránsito'] == 7.0
    assert fila['MAX'] == 6
    # Existencias solas (4) no llegan al MAX; es la suma con el tránsito la que bloquea.
    assert fila['Existencias'] < fila['MAX']
    assert fila['Existencias + Tránsito'] >= fila['MAX']
    assert fila['Cajas Forzadas'] == 0


def test_hojas_de_trazabilidad_reportan_cajas_forzadas():
    """Una combinación excluida SÍ puede recibir cajas cuando no quedaba otro
    destino (cero residuo). La hoja no debe decir 'no se le envió' sobre una
    tienda que sí recibió."""
    forzada = _row(300, 'Producto C', 'S09', consumo=0.0, existencias=0.0,
                   cajas_asignadas=4, cajas_cedi=4)
    df_output = pd.DataFrame([forzada])
    df_merged = _merged_con_exclusiones([
        (forzada, True, 'Tienda ausente de Celes', 0, False),
    ])

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hoja = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Sin Datos Celes')

    assert hoja.iloc[0]['Cajas Forzadas'] == 4


def test_hojas_de_trazabilidad_existen_vacias_con_encabezado():
    """Sin exclusiones las hojas igual se generan, con encabezado y 0 filas —
    así el usuario distingue 'no hubo ninguna' de 'la hoja no se generó'."""
    normal = _row(400, 'Producto D', 'S01', consumo=1.0, existencias=1.0,
                  cajas_asignadas=2, cajas_cedi=2)
    df_output = pd.DataFrame([normal])
    df_merged = _merged_con_exclusiones([(normal, False, '', 0, False)])

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)

    for nombre, col in (('Sin Datos Celes', 'Causa'),
                        ('Bloqueadas por MAX', 'MAX')):
        hoja = pd.read_excel(io.BytesIO(excel_bytes), sheet_name=nombre)
        assert len(hoja) == 0
        assert col in hoja.columns


def test_hojas_de_trazabilidad_no_se_generan_sin_df_merged():
    """Mismo criterio que 'Análisis Comprador': necesitan el universo
    pre-algoritmo, así que sin df_merged no se generan."""
    df_output = pd.DataFrame([
        _row(500, 'Producto E', 'S01', consumo=1.0, existencias=1.0,
             cajas_asignadas=2, cajas_cedi=2)
    ])
    hojas = _hojas(generate_excel(df_output, alertas=[], df_merged=None))
    assert 'Sin Datos Celes' not in hojas
    assert 'Bloqueadas por MAX' not in hojas


def test_hojas_de_trazabilidad_toleran_df_merged_sin_columnas_nuevas():
    """Un df_merged de una corrida anterior (sin las columnas de exclusión) no
    debe romper la generación del Excel."""
    normal = _row(600, 'Producto F', 'S01', consumo=1.0, existencias=1.0,
                  cajas_asignadas=2, cajas_cedi=2)
    df_output = pd.DataFrame([normal])
    df_merged = pd.DataFrame([normal])   # sin sin_match_celes ni bloqueado_por_max

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hojas = _hojas(excel_bytes)
    assert 'Sin Datos Celes' in hojas
    assert len(pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Sin Datos Celes')) == 0


# ---------------------------------------------------------------------------
# Hoja "Renovación Vida Útil" y columna Cajas Fase 3
# ---------------------------------------------------------------------------

def _fila_renovacion(item, store, consumo, existencias, dias, vu, fase3=0, transito=0.0, fresco=False):
    r = _row(item, f'Prod {item}', store, consumo=consumo, existencias=existencias,
             cajas_asignadas=fase3, cajas_cedi=5)
    r['cajas_fase4'] = 0
    r['cajas_fase3'] = fase3
    r['inventario_transito'] = transito
    r['grupo_id'] = f'ITEM_{item}'
    r['vida_util_portafolio'] = vu
    r['dias_desde_ingreso'] = dias
    r['fecha_ultimo_ingreso'] = pd.Timestamp('2026-11-12') - pd.Timedelta(days=dias)
    r['venta_esperada_vida_util'] = consumo * vu
    r['grupo_con_ingreso_fresco'] = fresco
    r['excluida_del_reparto'] = False
    return r


def test_hoja_renovacion_lista_candidatos_con_su_motivo(monkeypatch):
    monkeypatch.setattr('core.exporter.UMBRAL_VENTA_RENOVACION', 0.7)
    renovada = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=8, vu=5, fase3=1)
    transito = _fila_renovacion(1, 'S02', 0.3, 1.5, dias=8, vu=5, transito=2.0)
    poca_venta = _fila_renovacion(1, 'S03', 0.1, 1.5, dias=8, vu=5)
    vigente = _fila_renovacion(1, 'S04', 0.3, 1.5, dias=3, vu=5)       # no vencido: no aparece
    sin_vida = _fila_renovacion(2, 'S01', 0.3, 1.5, dias=8, vu=float('nan'))  # sin vida útil: no aparece
    df_merged = pd.DataFrame([renovada, transito, poca_venta, vigente, sin_vida])
    df_output = pd.DataFrame([renovada])

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hoja = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Renovación Vida Útil')

    # Primero las que recibieron caja (Cajas Enviadas ↓), luego por ítem y tienda.
    assert list(hoja['Tienda']) == ['S01', 'S02', 'S03']
    assert list(hoja['Cajas Enviadas']) == [1, 0, 0]
    assert hoja.loc[0, 'Motivo'] == 'Renovada'
    assert 'tránsito' in hoja.loc[1, 'Motivo']
    assert 'Venta esperada <' in hoja.loc[2, 'Motivo']
    assert hoja.loc[0, 'Días Desde Ingreso'] == 8 and hoja.loc[0, 'Vida Útil (días)'] == 5


def test_hoja_renovacion_existe_vacia_y_resumen_items_trae_cajas_fase_3():
    r = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=2, vu=5, fase3=0)
    r['cajas_asignadas'] = 3
    r['cajas_fase4'] = 3
    df_merged = pd.DataFrame([r])
    excel_bytes = generate_excel(pd.DataFrame([r]), alertas=[], df_merged=df_merged)
    assert 'Renovación Vida Útil' in _hojas(excel_bytes)
    assert len(pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Renovación Vida Útil')) == 0
    ri = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen Ítems', header=2)
    assert 'F3 Renovación' in ri.columns and ri.loc[0, 'F4 Excedente'] == 3


def test_hoja_renovacion_no_se_genera_sin_df_merged():
    r = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=8, vu=5, fase3=1)
    assert 'Renovación Vida Útil' not in _hojas(generate_excel(pd.DataFrame([r]), alertas=[]))


# ---------------------------------------------------------------------------
# Hojas compactas (nov-2026): alertas de 3 columnas, leyenda única, filtros
# ---------------------------------------------------------------------------

def test_hoja_alertas_solo_trae_tipo_item_y_mensaje():
    """Los datos estructurados extra de cada alerta ya van en el mensaje: la hoja no
    debe arrastrar columnas casi vacías (Cajas En Cedi, Combos Bloqueados, ...)."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    alertas = [
        {'tipo': 'CRÍTICO', 'item': 99, 'mensaje': 'Ítem huérfano', 'cajas_sin_distribuir': 5},
        {'tipo': 'ADVERTENCIA', 'item': '-', 'mensaje': 'Sin match', 'combos_sin_match': 12},
    ]
    hoja = pd.read_excel(io.BytesIO(generate_excel(df_output, alertas=alertas)), sheet_name='Alertas')
    assert list(hoja.columns) == ['Tipo', 'Ítem', 'Mensaje']


def test_resumen_items_lleva_leyenda_de_una_linea_y_porcentaje_forzado():
    r = _row(1, 'Producto A', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=4, cajas_cedi=4)
    r['cajas_fase4'], r['cajas_fase5'] = 1, 3
    excel = generate_excel(pd.DataFrame([r]), alertas=[])
    crudo = pd.read_excel(io.BytesIO(excel), sheet_name='Resumen Ítems', header=None)
    assert 'F5 Cero residuo forzado' in crudo.iloc[0, 0] and crudo.iloc[1].isna().all()
    ri = pd.read_excel(io.BytesIO(excel), sheet_name='Resumen Ítems', header=2)
    assert ri.loc[0, 'F5 Forzado'] == 3 and ri.loc[0, '% Forzado'] == 75


def test_hojas_de_analisis_tienen_encabezado_fijo_y_filtros():
    from openpyxl import load_workbook
    r = _row(1, 'Producto A', 'S01', consumo=1.0, existencias=1.0, cajas_asignadas=2, cajas_cedi=2)
    excel = generate_excel(pd.DataFrame([r]), alertas=[], df_merged=pd.DataFrame([r]))
    wb = load_workbook(io.BytesIO(excel))
    for nombre in ('Distribución', 'Análisis Comprador', 'Sin Datos Celes', 'Alertas'):
        assert wb[nombre].freeze_panes == 'A2', nombre
        assert wb[nombre].auto_filter.ref, nombre
    assert wb['Resumen Ítems'].freeze_panes == 'A4'


def test_riesgo_de_merma_usa_la_vida_util_del_portafolio_con_respaldo_en_celes():
    """El umbral de merma es min(10, VIDA UTIL EXHIBICION); sin ella, la vida útil de Celes."""
    def fila(item, vu_portafolio, vu_celes):
        r = _row(item, f'P{item}', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=4,
                 cajas_cedi=4, vida_util=vu_celes)
        r['vida_util_portafolio'] = vu_portafolio
        return r
    # 4 días tras el pedido: supera una vida útil de 3 (merma) y no supera una de 8.
    filas = [fila(1, 3.0, 99), fila(2, 8.0, 1), fila(3, float('nan'), 3)]
    df = pd.DataFrame(filas)
    comprador = pd.read_excel(io.BytesIO(generate_excel(df, alertas=[], df_merged=df)), sheet_name='Análisis Comprador')
    pct = dict(zip(comprador['Código Ítem'], comprador['% Tiendas Riesgo Merma']))
    assert pct == {1: 100.0, 2: 0.0, 3: 100.0}, pct     # 1: Portafolio manda; 2: ignora la 1 de Celes; 3: respaldo Celes


def test_resumen_renovacion_cuenta_los_motivos_y_deja_visibles_las_omitidas_por_transito(monkeypatch):
    from core.exporter import resumen_renovacion, resumen_renovacion_texto
    monkeypatch.setattr('core.exporter.UMBRAL_VENTA_RENOVACION', 0.7)
    renovada = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=8, vu=5, fase3=1)
    transito = _fila_renovacion(1, 'S02', 0.3, 1.5, dias=8, vu=5, transito=2.0)
    poca_venta = _fila_renovacion(1, 'S03', 0.1, 1.5, dias=8, vu=5)
    vigente = _fila_renovacion(1, 'S04', 0.3, 1.5, dias=3, vu=5)             # no candidata
    df_merged = pd.DataFrame([renovada, transito, poca_venta, vigente])
    df_output = pd.DataFrame([renovada])

    assert resumen_renovacion(df_merged, df_output) == {
        'Renovada': 1, 'Tiene tránsito': 1, 'Venta esperada < 0.7 caja': 1,
    }
    assert resumen_renovacion_texto(df_merged, df_output).startswith(
        '1 renovadas · 1 omitidas por tránsito · 0 sin stock en CEDI · 1 otras')


def test_resumen_renovacion_sin_candidatas_es_vacio():
    from core.exporter import resumen_renovacion, resumen_renovacion_texto
    r = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=2, vu=5)
    df = pd.DataFrame([r])
    assert resumen_renovacion(df, df) == {} and resumen_renovacion_texto(df, df) == ''
