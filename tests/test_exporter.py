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


def test_descripcion_inconsistente_no_duplica_fila_en_resumen_items():
    """Si el mismo item_code llega con `item_desc` ligeramente distinto entre tiendas (p. ej. doble
    espacio, como llega de `DB_Portafolio_Fruver.DESCRIPCION`) «Resumen Ítems» no debe generar una
    fila por variante de descripción (bug real del ítem 1671, 2026-06-08)."""
    rows_normales = [
        _row(1671, 'MANZANA ROYAL GALA UND', f'S{i:02d}', consumo=1.0,
             existencias=20.0, cajas_asignadas=1, cajas_cedi=100)
        for i in range(1, 21)
    ]
    rows_sucia = [
        _row(1671, 'MANZANA  ROYAL GALA UND', 'S99', consumo=1.0,
             existencias=20.0, cajas_asignadas=1, cajas_cedi=100)
    ]
    df_output = pd.DataFrame(rows_normales + rows_sucia)
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_output.copy())

    resumen_items = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen Ítems', skiprows=2)
    filas_resumen = resumen_items[resumen_items['Código Ítem'] == 1671]
    assert len(filas_resumen) == 1, f'filas encontradas: {len(filas_resumen)}'
    assert filas_resumen.iloc[0]['Total Cajas'] == 21


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


def test_el_libro_tiene_solo_las_cinco_hojas_y_ninguna_del_comprador_ni_las_tres_listas_viejas():
    """Sin análisis para el comprador y con las tres listas unidas en «Novedades»."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1, vida_util=30),
    ])
    hojas = _hojas(generate_excel(df_output, alertas=[], df_merged=df_output.copy()))
    assert hojas == ['Distribución', 'Resumen', 'Resumen Ítems', 'Novedades', 'Alertas']


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


def test_generate_excel_sin_df_merged_omite_novedades():
    """Sin df_merged no hay universo pre-algoritmo: no se genera «Novedades»."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    hojas = _hojas(generate_excel(df_output, alertas=[], df_merged=None))
    assert hojas == ['Distribución', 'Resumen', 'Resumen Ítems', 'Alertas']


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
    assert list(alertas_sheet.columns[:3]) == ['Nivel', 'Ítem', 'Qué pasó']
    # Crítico debe ir antes que Advertencia (orden por severidad).
    assert list(alertas_sheet['Nivel']) == ['Crítico', 'Advertencia']


def test_hoja_alertas_vacia_muestra_ok_sin_alertas():
    """Sin alertas, la hoja debe mostrar una fila 'OK · Todo en orden' en vez de quedar vacía."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    alertas_sheet = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Alertas')
    assert len(alertas_sheet) == 1
    assert alertas_sheet.iloc[0]['Nivel'] == 'OK'
    assert 'Todo en orden' in alertas_sheet.iloc[0]['Qué pasó']


# ---------------------------------------------------------------------------
# Hoja "Novedades": Sin datos Celes y Bloqueadas por MAX
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


def _novedades(excel_bytes):
    return pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Novedades')


def test_novedades_lista_las_combinaciones_sin_match_en_celes():
    """Las filas sin match en Celes no reciben cajas, así que no están en df_output —
    la hoja debe salir de df_merged o quedaría vacía."""
    recibe = _row(100, 'Con datos', 'S01', consumo=2.0, existencias=1.0,
                  cajas_asignadas=5, cajas_cedi=5)
    sin_datos = _row(100, 'Con datos', 'S02', consumo=0.0, existencias=0.0,
                     cajas_asignadas=0, cajas_cedi=5)
    df_merged = _merged_con_exclusiones([
        (recibe, False, '', 0, False),
        (sin_datos, True, 'Par tienda-ítem faltante', 0, False),
    ])
    hoja = _novedades(generate_excel(pd.DataFrame([recibe]), alertas=[], df_merged=df_merged))

    assert len(hoja) == 1
    fila = hoja.iloc[0]
    assert fila['Situación'] == 'Sin datos Celes'
    assert fila['Tienda'] == 'S02' and fila['Código Ítem'] == 100
    assert fila['Detalle'] == 'Par tienda-ítem faltante'
    assert fila['Cajas Enviadas'] == 0


def test_novedades_bloqueada_por_max_explica_existencias_transito_y_maximo():
    bloqueada = _row(200, 'Producto B', 'S05', consumo=1.0, existencias=4.0,
                     cajas_asignadas=0, cajas_cedi=3)
    bloqueada['inventario_transito'] = 3.0
    bloqueada['inventario_efectivo'] = 7.0
    normal = _row(200, 'Producto B', 'S06', consumo=1.0, existencias=0.0,
                  cajas_asignadas=3, cajas_cedi=3)
    df_merged = _merged_con_exclusiones([
        (bloqueada, False, '', 6, True),
        (normal, False, '', 0, False),
    ])
    hoja = _novedades(generate_excel(pd.DataFrame([normal]), alertas=[], df_merged=df_merged))

    assert len(hoja) == 1
    fila = hoja.iloc[0]
    assert fila['Situación'] == 'Bloqueada por MAX' and fila['Tienda'] == 'S05'
    assert fila['Detalle'] == 'Existencias 4 + tránsito 3 = 7 ≥ MAX 6'
    assert fila['Cajas Enviadas'] == 0


def test_novedades_reporta_las_cajas_que_el_cero_residuo_les_dio():
    """Una combinación excluida SÍ puede recibir cajas cuando no quedaba otro destino."""
    forzada = _row(300, 'Producto C', 'S09', consumo=0.0, existencias=0.0,
                   cajas_asignadas=4, cajas_cedi=4)
    df_merged = _merged_con_exclusiones([(forzada, True, 'Tienda ausente de Celes', 0, False)])
    hoja = _novedades(generate_excel(pd.DataFrame([forzada]), alertas=[], df_merged=df_merged))
    assert hoja.iloc[0]['Cajas Enviadas'] == 4


def test_novedades_existe_vacia_con_encabezado_si_no_hay_casos():
    normal = _row(400, 'Producto D', 'S01', consumo=1.0, existencias=1.0,
                  cajas_asignadas=2, cajas_cedi=2)
    df_merged = _merged_con_exclusiones([(normal, False, '', 0, False)])
    hoja = _novedades(generate_excel(pd.DataFrame([normal]), alertas=[], df_merged=df_merged))
    assert len(hoja) == 0
    assert list(hoja.columns) == ['Situación', 'Tienda', 'Nombre Tienda', 'Código Ítem',
                                  'Nombre Ítem', 'Detalle', 'Cajas Enviadas']


def test_novedades_tolera_df_merged_sin_columnas_nuevas():
    """Un df_merged de una corrida anterior (sin las columnas de exclusión) no debe romper el Excel."""
    normal = _row(600, 'Producto F', 'S01', consumo=1.0, existencias=1.0,
                  cajas_asignadas=2, cajas_cedi=2)
    excel_bytes = generate_excel(pd.DataFrame([normal]), alertas=[], df_merged=pd.DataFrame([normal]))
    assert 'Novedades' in _hojas(excel_bytes)
    assert len(_novedades(excel_bytes)) == 0


def test_novedades_une_las_tres_situaciones_en_una_tabla_ordenada():
    sin = _fila_renovacion(100, 'S02', 0.0, 0.0, dias=1, vu=5)
    mx = _fila_renovacion(100, 'S03', 1.0, 4.0, dias=1, vu=5)
    mx['inventario_transito'] = 3.0
    ren = _fila_renovacion(100, 'S01', 0.3, 1.5, dias=8, vu=5, fase3=1)
    df_merged = _merged_con_exclusiones([
        (ren, False, '', 0, False),
        (mx, False, '', 6, True),
        (sin, True, 'Tienda ausente de Celes', 0, False),
    ])
    hoja = _novedades(generate_excel(pd.DataFrame([ren]), alertas=[], df_merged=df_merged))
    assert list(hoja['Situación']) == ['Sin datos Celes', 'Bloqueada por MAX', 'Renovación vida útil']
    ultima = hoja.iloc[2]
    assert ultima['Detalle'] == 'Último ingreso 2026-11-04 (8 d) · vida útil 5 d · Renovada'
    assert ultima['Cajas Enviadas'] == 1


# ---------------------------------------------------------------------------
# «Novedades» (Renovación vida útil) y columna Cajas Fase 3
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


def test_novedades_renovacion_lista_candidatos_con_su_motivo(monkeypatch):
    monkeypatch.setattr('core.exporter.UMBRAL_VENTA_RENOVACION', 0.7)
    renovada = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=8, vu=5, fase3=1)
    transito = _fila_renovacion(1, 'S02', 0.3, 1.5, dias=8, vu=5, transito=2.0)
    poca_venta = _fila_renovacion(1, 'S03', 0.1, 1.5, dias=8, vu=5)
    vigente = _fila_renovacion(1, 'S04', 0.3, 1.5, dias=3, vu=5)       # no vencido: no aparece
    sin_vida = _fila_renovacion(2, 'S01', 0.3, 1.5, dias=8, vu=float('nan'))  # sin vida útil: no aparece
    df_merged = pd.DataFrame([renovada, transito, poca_venta, vigente, sin_vida])
    df_output = pd.DataFrame([renovada])

    hoja = _novedades(generate_excel(df_output, alertas=[], df_merged=df_merged))

    # Primero las que recibieron caja (Cajas Enviadas ↓), luego por ítem y tienda.
    assert list(hoja['Tienda']) == ['S01', 'S02', 'S03']
    assert list(hoja['Cajas Enviadas']) == [1, 0, 0]
    assert set(hoja['Situación']) == {'Renovación vida útil'}
    assert hoja.loc[0, 'Detalle'].endswith('· Renovada') and '(8 d)' in hoja.loc[0, 'Detalle']
    assert 'tránsito' in hoja.loc[1, 'Detalle']
    assert 'Venta esperada <' in hoja.loc[2, 'Detalle']


def test_resumen_items_trae_cajas_fase_3_y_novedades_vacia_sin_candidatas():
    r = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=2, vu=5, fase3=0)
    r['cajas_asignadas'] = 3
    r['cajas_fase4'] = 3
    df_merged = pd.DataFrame([r])
    excel_bytes = generate_excel(pd.DataFrame([r]), alertas=[], df_merged=df_merged)
    assert len(_novedades(excel_bytes)) == 0
    ri = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen Ítems', header=2)
    assert 'F3 Renovación' in ri.columns and ri.loc[0, 'F4 Excedente'] == 3


# ---------------------------------------------------------------------------
# Hojas compactas (nov-2026): alertas de 3 columnas, leyenda única, filtros
# ---------------------------------------------------------------------------

def test_hoja_alertas_solo_trae_nivel_item_y_que_paso():
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
    assert list(hoja.columns) == ['Nivel', 'Ítem', 'Qué pasó']


def test_resumen_items_lleva_leyenda_de_una_linea_y_sin_columnas_de_sobreenvio():
    r = _row(1, 'Producto A', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=4, cajas_cedi=4)
    r['cajas_fase4'], r['cajas_fase5'] = 1, 3
    excel = generate_excel(pd.DataFrame([r]), alertas=[])
    crudo = pd.read_excel(io.BytesIO(excel), sheet_name='Resumen Ítems', header=None)
    assert 'F4 Excedente voluntario' in crudo.iloc[0, 0] and crudo.iloc[1].isna().all()
    assert 'F5' not in crudo.iloc[0, 0] and 'sobre-compra' not in crudo.iloc[0, 0]
    ri = pd.read_excel(io.BytesIO(excel), sheet_name='Resumen Ítems', header=2)
    assert not any('Forzado' in c for c in ri.columns)
    assert list(ri.columns) == ['Código Ítem', 'Nombre Ítem', 'Total Cajas', 'F1 Urgentes',
                                'F2 Cobertura 3d', 'F3 Renovación', 'F4 Excedente']


def test_hojas_de_analisis_tienen_encabezado_fijo_y_filtros():
    from openpyxl import load_workbook
    r = _row(1, 'Producto A', 'S01', consumo=1.0, existencias=1.0, cajas_asignadas=2, cajas_cedi=2)
    excel = generate_excel(pd.DataFrame([r]), alertas=[], df_merged=pd.DataFrame([r]))
    wb = load_workbook(io.BytesIO(excel))
    for nombre in ('Distribución', 'Novedades', 'Alertas'):
        assert wb[nombre].freeze_panes == 'A2', nombre
        assert wb[nombre].auto_filter.ref, nombre
    assert wb['Resumen Ítems'].freeze_panes == 'A4'


def test_alertas_y_novedades_llevan_color_por_nivel_y_situacion():
    from openpyxl import load_workbook
    ren = _fila_renovacion(1, 'S01', 0.3, 1.5, dias=8, vu=5, fase3=1)
    alertas = [{'tipo': 'ADVERTENCIA', 'item': '-', 'mensaje': 'x'}, {'tipo': 'CRÍTICO', 'item': 9, 'mensaje': 'y'}]
    wb = load_workbook(io.BytesIO(generate_excel(pd.DataFrame([ren]), alertas=alertas,
                                                 df_merged=pd.DataFrame([ren]))))
    assert wb['Alertas']['A2'].value == 'Crítico' and wb['Alertas']['A2'].fill.fgColor.rgb.endswith('F4A6A6')
    assert wb['Alertas']['A3'].fill.fgColor.rgb.endswith('FBE0A6')
    assert wb['Alertas']['C2'].alignment.wrap_text
    assert wb['Novedades']['A2'].fill.fgColor.rgb.endswith('BDD7EE')


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
