"""Libro único `Input/Maestros Fruver.xlsx`: lee sus hojas y calcula ESTADO / DIA DE COMPRA (desde STATUS)
y MAX (desde BD_MAX) sin depender de que Excel haya recalculado las fórmulas.
"""

import io
import unicodedata

import numpy as np
import pandas as pd

from .preprocessor import normalize_item

ARCHIVO = 'Maestros Fruver.xlsx'

HOJA_PORTAFOLIO = 'Portafolio'
HOJA_STATUS = 'STATUS'
HOJA_TIENDAS = 'Tiendas'
HOJA_TIENDAS_ITEM = 'Tiendas x Item'
HOJA_BD_MAX = 'BD_MAX'
HOJA_ESPEJO = 'Espejo'
HOJA_NO_PEDIDO = 'No generar pedido'
HOJA_PARAMETROS = 'Parametros'
HOJA_DIAS = 'Dias de compra'
HOJA_CALENDARIO = 'Calendario'
HOJA_INSTRUCCIONES = 'Instrucciones'

OBLIGATORIAS = (HOJA_PORTAFOLIO, HOJA_STATUS, HOJA_TIENDAS, HOJA_TIENDAS_ITEM, HOJA_ESPEJO)

# Clave interna del pipeline (loader.REQUIRED_COLUMNS) → hoja del libro.
HOJA_DE_CLAVE = {
    'portafolio': HOJA_PORTAFOLIO,
    'tiendas': HOJA_TIENDAS,
    'tiendas_item': HOJA_TIENDAS_ITEM,
    'espejo': HOJA_ESPEJO,
    'excluidas': HOJA_NO_PEDIDO,
}

COL_STATUS = ['Código de Producto', 'DESCRIPCION', 'STATUS', 'DIA DE COMPRA', 'CRECIMIENTO VENTA (%)',
              'MULTIPLO COMPRA (cajas)', 'COMPRA MINIMA (cajas)']

# Nombre de la tabla de Excel de cada hoja (las fórmulas usan referencias estructuradas).
TABLAS = {
    HOJA_PORTAFOLIO: 'Portafolio_Fruver', HOJA_STATUS: 'Status_Items', HOJA_TIENDAS: 'Tiendas_Activas',
    HOJA_TIENDAS_ITEM: 'Tiendas_Items', HOJA_BD_MAX: 'Tabla_MAX', HOJA_ESPEJO: 'Productos_Espejo',
    HOJA_NO_PEDIDO: 'No_Generar_Pedido', HOJA_PARAMETROS: 'Parametros_Compra', HOJA_DIAS: 'Dias_Compra',
    HOJA_CALENDARIO: 'Calendario_Compra',
}


def _norm(texto):
    s = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().split())


def _buscar(hojas, nombre):
    objetivo = _norm(nombre)
    for k, df in hojas.items():
        if _norm(k) == objetivo:
            return df
    return None


def _limpiar(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [not c.startswith('Unnamed:') for c in df.columns]]
    return df.dropna(how='all').reset_index(drop=True)


def _clave_item(serie):
    return serie.map(normalize_item)


def leer_maestros(archivo):
    """Lee `Maestros Fruver.xlsx`.

    Retorna (dfs, hojas_parametros, alertas):
    - dfs: {'portafolio', 'tiendas', 'tiendas_item', 'espejo'[, 'excluidas']} con las mismas columnas
      que entregaban los archivos sueltos (el resto del pipeline no cambia).
    - hojas_parametros: {'General', 'Compra por día', 'Calendario', 'Por Ítem'} para
      `compras_params.cargar_parametros` ('Por Ítem' sale de las columnas de ajuste de STATUS).
    Lanza ValueError si falta una hoja obligatoria.
    """
    if hasattr(archivo, 'seek'):
        archivo.seek(0)
    hojas = {str(k).strip(): _limpiar(v) for k, v in pd.read_excel(archivo, sheet_name=None).items()}
    faltan = [h for h in OBLIGATORIAS if _buscar(hojas, h) is None]
    if faltan:
        raise ValueError(f'Al archivo «{ARCHIVO}» le faltan hojas: {", ".join(faltan)}.')
    alertas = []

    # STATUS → ESTADO y DIA DE COMPRA del Portafolio (equivale al XLOOKUP: primera coincidencia).
    status = _buscar(hojas, HOJA_STATUS).copy()
    c_cod = next((c for c in status.columns if _norm(c) in ('codigo de producto', 'item')), None)
    if c_cod is None or 'STATUS' not in status.columns:
        raise ValueError(f'«{ARCHIVO}», hoja «{HOJA_STATUS}»: debe tener las columnas «Código de Producto» y «STATUS».')
    status['_item'] = _clave_item(status[c_cod])
    status = status.dropna(subset=['_item'])
    status['_item'] = status['_item'].astype(int)
    rep = status['_item'][status['_item'].duplicated()]
    if len(rep):
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'Maestros › STATUS: ítem(s) repetido(s) ({", ".join(str(i) for i in sorted(set(rep)))}); '
                                   'vale la primera fila, como el XLOOKUP.'})
    status = status.drop_duplicates('_item')
    estado = dict(zip(status['_item'], status['STATUS'].astype(str).str.strip()))
    dia = dict(zip(status['_item'], status['DIA DE COMPRA'].astype(str).str.strip()
                   if 'DIA DE COMPRA' in status else ['Todos los dias'] * len(status)))

    port = _buscar(hojas, HOJA_PORTAFOLIO).copy()
    port = port.dropna(subset=['ITEM']) if 'ITEM' in port else port
    if 'ITEM' in port:
        claves = _clave_item(port['ITEM'])
        port['ESTADO'] = claves.map(estado).fillna('DESCONTINUADO')
        port['DIA DE COMPRA'] = claves.map(dia).fillna('DESCONTINUADO')
        if {'VIDA UTIL EXHIBICION', 'DIAS CONSERVACION CEDI'} <= set(port.columns):
            port['TOTAL VIDA UTIL'] = (pd.to_numeric(port['VIDA UTIL EXHIBICION'], errors='coerce')
                                       + pd.to_numeric(port['DIAS CONSERVACION CEDI'], errors='coerce'))
        huerfanos = sorted(set(status['_item']) - set(claves.dropna().astype(int)))
        if huerfanos:
            alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                            'mensaje': f'Maestros › STATUS: {len(huerfanos)} código(s) que no existen en la hoja '
                                       f'Portafolio ({", ".join(str(i) for i in huerfanos[:10])}); se ignoran.'})

    # Tiendas x Item: MAX desde BD_MAX (equivale al XLOOKUP sobre LLAVE = tienda & ítem).
    ti = _buscar(hojas, HOJA_TIENDAS_ITEM).copy()
    bd = _buscar(hojas, HOJA_BD_MAX)
    if bd is not None and {'COD SIESA', 'ITEM', 'MAX'} <= set(bd.columns) and {'COD SIESA', 'ITEM'} <= set(ti.columns):
        bd = bd.copy()
        bd['_k'] = bd['COD SIESA'].astype(str).str.strip() + '|' + _clave_item(bd['ITEM']).astype(str)
        mapa = bd.drop_duplicates('_k').set_index('_k')['MAX']
        k = ti['COD SIESA'].astype(str).str.strip() + '|' + _clave_item(ti['ITEM']).astype(str)
        valores = pd.to_numeric(k.map(mapa), errors='coerce').fillna(0)
        ti['MAX'] = valores.astype('int64') if (valores % 1 == 0).all() else valores
    elif 'MAX' not in ti.columns:
        ti['MAX'] = 0

    dfs = {
        'portafolio': port,
        'tiendas': _buscar(hojas, HOJA_TIENDAS),
        'tiendas_item': ti,
        'espejo': _buscar(hojas, HOJA_ESPEJO),
    }
    excl = _buscar(hojas, HOJA_NO_PEDIDO)
    if excl is not None and len(excl) > 0:
        dfs['excluidas'] = excl

    hojas_parametros = {}
    for destino, origen in (('General', HOJA_PARAMETROS), ('Compra por día', HOJA_DIAS),
                            ('Calendario', HOJA_CALENDARIO)):
        df = _buscar(hojas, origen)
        if df is not None:
            hojas_parametros[destino] = df
    hojas_parametros['Por Ítem'] = status.drop(columns=['_item']).rename(columns={c_cod: 'ITEM'})
    return dfs, hojas_parametros, alertas


# ── Escritura ────────────────────────────────────────────────────────────────

def _formulas(hoja, tabla, columnas, fila):
    """Fórmulas (referencias estructuradas) de las columnas calculadas de cada hoja."""
    t = TABLAS
    r = lambda col: f'{tabla}[[#This Row],[{col}]]'  # noqa: E731
    if hoja == HOJA_PORTAFOLIO:
        return {
            'ESTADO': f'=_xlfn.XLOOKUP({r("ITEM")},{t[HOJA_STATUS]}[Código de Producto],'
                      f'{t[HOJA_STATUS]}[STATUS],"DESCONTINUADO",0,1)',
            'TOTAL VIDA UTIL': f'={r("VIDA UTIL EXHIBICION")}+{r("DIAS CONSERVACION CEDI")}',
            'DIA DE COMPRA': f'=_xlfn.XLOOKUP({r("ITEM")},{t[HOJA_STATUS]}[Código de Producto],'
                             f'{t[HOJA_STATUS]}[DIA DE COMPRA],"DESCONTINUADO",0,1)',
        }
    if hoja == HOJA_STATUS:
        return {'DESCRIPCION': f'=_xlfn.XLOOKUP({r("Código de Producto")},{t[HOJA_PORTAFOLIO]}[ITEM],'
                               f'{t[HOJA_PORTAFOLIO]}[DESCRIPCION],"NO ESTÁ EN PORTAFOLIO",0,1)'}
    if hoja == HOJA_TIENDAS_ITEM:
        return {
            'MAX': f'=IFERROR(_xlfn.XLOOKUP({r("LLAVE")},{t[HOJA_BD_MAX]}[LLAVE],{t[HOJA_BD_MAX]}[MAX],,0,1),)',
            'LLAVE': f'=_xlfn.CONCAT({r("COD SIESA")},{r("ITEM")})',
        }
    if hoja == HOJA_BD_MAX:
        return {'LLAVE': f'=_xlfn.CONCAT({r("COD SIESA")},{r("ITEM")})'}
    return {}


INSTRUCCIONES = [
    'Libro único de maestros del Distribuidor Fruver. Cada hoja es una tabla de Excel: agregue filas al final de la tabla.',
    'Portafolio: ítems, costo y vida útil. ESTADO y DIA DE COMPRA se calculan solos desde la hoja STATUS (no escribir encima).',
    'STATUS: lista de ítems que se compran. STATUS = ACTIVO para comprar; DIA DE COMPRA = «Todos los dias», días '
    'separados por guion (LUNES-MIERCOLES-VIERNES) o «CADA N DIAS». Columnas opcionales por ítem: crecimiento de venta (%), '
    'múltiplo y compra mínima del proveedor (vacío = valor general). Un ítem que no está en STATUS queda DESCONTINUADO.',
    'Tiendas: tiendas activas. Tiendas x Item: qué ítem maneja cada tienda (ESTADO), MIN = exhibición en canastillas '
    '(vacío = 1) y MAX (se calcula solo desde BD_MAX; 0 = sin tope).',
    'BD_MAX: máximos por tienda × ítem. Espejo: SKUs que son el mismo producto. No generar pedido: tiendas que hoy no '
    'reciben distribución (la compra sí las incluye).',
    'Parametros, Dias de compra y Calendario: parámetros de la compra. Porcentajes de 0 a 100 (18 = 18 %).',
    'El sistema calcula ESTADO, DIA DE COMPRA y MAX por su cuenta: funciona aunque el libro no se haya recalculado.',
]


def escribir_maestros(hojas):
    """Escribe el libro de maestros. `hojas`: {nombre de hoja: DataFrame} con las columnas de cada hoja
    (las columnas calculadas pueden venir vacías: se escriben como fórmula). Retorna bytes."""
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo

    wb = Workbook()
    wb.remove(wb.active)
    for nombre, df in hojas.items():
        ws = wb.create_sheet(nombre)
        columnas = [str(c) for c in df.columns]
        ws.append(columnas)
        tabla = TABLAS.get(nombre)
        formulas = _formulas(nombre, tabla, columnas, None) if tabla else {}
        for fila in df.itertuples(index=False):
            valores = []
            for col, v in zip(columnas, fila):
                if col in formulas:
                    valores.append(formulas[col])
                elif v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
                    valores.append(None)
                elif isinstance(v, (np.integer,)):
                    valores.append(int(v))
                elif isinstance(v, (np.floating,)):
                    valores.append(float(v))
                elif isinstance(v, pd.Timestamp):
                    valores.append(v.to_pydatetime())
                else:
                    valores.append(v)
            ws.append(valores)
        n_filas = max(len(df), 1)
        if len(df) == 0:
            ws.append([None] * len(columnas))
        if tabla:
            ref = f'A1:{get_column_letter(len(columnas))}{n_filas + 1}'
            t = Table(displayName=tabla, ref=ref)
            t.tableStyleInfo = TableStyleInfo(name='TableStyleMedium7', showRowStripes=True)
            ws.add_table(t)
        ws.freeze_panes = 'A2'
        for i, col in enumerate(columnas, start=1):
            largo = max([len(col)] + [len(str(v)) for v in df.iloc[:, i - 1].head(200) if v is not None
                                      and not (isinstance(v, float) and np.isnan(v))])
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, largo + 2), 45)
        if nombre == HOJA_CALENDARIO:
            for fila in range(2, ws.max_row + 1):
                ws.cell(row=fila, column=1).number_format = 'DD/MM/YYYY'

    ws = wb.create_sheet(HOJA_INSTRUCCIONES)
    ws.append(['Instrucciones'])
    for linea in INSTRUCCIONES:
        ws.append([linea])
    ws.column_dimensions['A'].width = 140

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ── Migración desde los archivos sueltos (formato anterior) ──────────────────

_COL_PORTAFOLIO = ['ITEM', 'DESCRIPCION', 'CLUSTERIZACIÓN', 'ESTADO', 'UM', 'COSTO', 'VIDA UTIL EXHIBICION',
                   'DIAS CONSERVACION CEDI', 'TOTAL VIDA UTIL', 'DIA DE COMPRA']
_COL_TIENDAS_ITEM = ['COD SIESA', 'NOMBRE DE LA TIENDA', 'TIPO DE PORTAFOLIO', 'ITEM', 'DESCRIPCION', 'ESTADO',
                     'MIN', 'MAX', 'LLAVE']
_COL_BD_MAX = ['COD SIESA', 'NOMBRE DE LA TIENDA', 'ITEM', 'DESCRIPCION', 'MAX', 'LLAVE']


def hojas_desde_sueltos(portafolio, tiendas, tiendas_item, espejo, excluidas=None, parametros=None,
                        activar=(), anios=None):
    """Arma las hojas de `Maestros Fruver.xlsx` a partir de los archivos sueltos del formato anterior.

    Solo toma los datos que usa el sistema: las hojas de respaldo o de trabajo (BackUp, PROVI,
    Naranjos, Hoja2…) quedan en los archivos originales. `activar`: ítems a agregar a STATUS como
    ACTIVO / «Todos los dias» si no están. Los parámetros se normalizan (porcentajes en 0–100).
    Retorna {hoja: DataFrame} para `escribir_maestros`.
    """
    from .compras_params import cargar_parametros, tabla_calendario, tabla_dias, tabla_general

    # Portafolio (+ tabla auxiliar STATUS en la misma hoja, si existe).
    crudo = _limpiar(pd.read_excel(portafolio, sheet_name=0))
    port = crudo[[c for c in _COL_PORTAFOLIO if c in crudo.columns]].dropna(subset=['ITEM']).copy()
    for c in _COL_PORTAFOLIO:
        if c not in port:
            port[c] = None
    port = port[_COL_PORTAFOLIO]
    port['ITEM'] = port['ITEM'].map(normalize_item).astype('Int64')
    if 'Código de Producto' in crudo.columns:
        col_dia = 'DIA DE COMPRA.1' if 'DIA DE COMPRA.1' in crudo.columns else 'DIA DE COMPRA'
        st = crudo[['Código de Producto', col_dia, 'STATUS']].dropna(subset=['Código de Producto'])
        st = st.rename(columns={col_dia: 'DIA DE COMPRA'})
    else:  # sin tabla auxiliar: los ACTIVO del Portafolio, con compra diaria
        from .preprocessor import _is_active
        activos = port[_is_active(port['ESTADO'].astype(str))]
        st = pd.DataFrame({'Código de Producto': activos['ITEM'], 'DIA DE COMPRA': 'Todos los dias',
                           'STATUS': 'ACTIVO'})
    st = st.copy()
    st['Código de Producto'] = st['Código de Producto'].map(normalize_item).astype('Int64')
    for code in activar:
        if code not in set(st['Código de Producto'].dropna()):
            st = pd.concat([st, pd.DataFrame([{'Código de Producto': code, 'DIA DE COMPRA': 'Todos los dias',
                                                'STATUS': 'ACTIVO'}])], ignore_index=True)

    # Parámetros: se leen con las reglas vigentes y se reescriben normalizados.
    hojas_par = None
    if parametros is not None:
        hojas_par = {str(k).strip(): _limpiar(v) for k, v in pd.read_excel(parametros, sheet_name=None).items()}
    params, _ = cargar_parametros(hojas_par)
    st['DESCRIPCION'] = None  # fórmula
    st['CRECIMIENTO VENTA (%)'] = st['Código de Producto'].map(
        lambda i: round(params.por_item[i]['crecimiento'] * 100, 4)
        if i in params.por_item and 'crecimiento' in params.por_item[i] else None)
    st['MULTIPLO COMPRA (cajas)'] = st['Código de Producto'].map(lambda i: params.por_item.get(i, {}).get('multiplo'))
    st['COMPRA MINIMA (cajas)'] = st['Código de Producto'].map(lambda i: params.por_item.get(i, {}).get('minimo'))
    st = st[COL_STATUS]

    xls_ti = pd.ExcelFile(tiendas_item)
    hoja_ti = 'Hoja1' if 'Hoja1' in xls_ti.sheet_names else xls_ti.sheet_names[0]
    ti = _limpiar(xls_ti.parse(hoja_ti))
    ti = ti[[c for c in _COL_TIENDAS_ITEM if c in ti.columns]]
    for c in _COL_TIENDAS_ITEM:
        if c not in ti:
            ti[c] = None
    ti = ti[_COL_TIENDAS_ITEM]
    if 'BD_MAX' in xls_ti.sheet_names:
        bd = _limpiar(xls_ti.parse('BD_MAX'))
        for c in _COL_BD_MAX:
            if c not in bd:
                bd[c] = None
        bd = bd[_COL_BD_MAX]
    else:
        bd = pd.DataFrame(columns=_COL_BD_MAX)

    if excluidas is not None:
        ex = pd.read_excel(excluidas, sheet_name=0)
        ex.columns = [str(c).strip() for c in ex.columns]
        if len(ex.columns) > 1 and 'Nombre Tienda' not in ex.columns:   # 2.ª columna sin encabezado = nombre
            ex = ex.rename(columns={ex.columns[1]: 'Nombre Tienda'})
        ex = _limpiar(ex[[c for c in ('Centro Operacional de la Bodega', 'Nombre Tienda') if c in ex.columns]])
    else:
        ex = pd.DataFrame(columns=['Centro Operacional de la Bodega', 'Nombre Tienda'])

    return {
        HOJA_PORTAFOLIO: port,
        HOJA_STATUS: st,
        HOJA_TIENDAS: _limpiar(pd.read_excel(tiendas, sheet_name=0)),
        HOJA_TIENDAS_ITEM: ti,
        HOJA_BD_MAX: bd,
        HOJA_ESPEJO: _limpiar(pd.read_excel(espejo, sheet_name=0)),
        HOJA_NO_PEDIDO: ex,
        HOJA_PARAMETROS: tabla_general(params),
        HOJA_DIAS: tabla_dias(params),
        HOJA_CALENDARIO: tabla_calendario(params, anios),
    }
