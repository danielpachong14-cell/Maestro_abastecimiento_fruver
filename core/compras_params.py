"""Parámetros de la compra: valores por defecto (constantes de abajo, fuente de verdad) y lectura de las
hojas Parametros / Dias de compra / Calendario de Maestros Fruver.xlsx. Porcentajes de 0 a 100.
"""

import io
import unicodedata
from dataclasses import dataclass, field, fields, replace
from datetime import date, timedelta

import pandas as pd

from .calendario import DIAS_SEMANA, CalendarioCompras, festivos_colombia

# =============================================================================
# >>> PARÁMETROS EDITABLES (valores por defecto de la compra)
# =============================================================================
LT_PROVEEDOR_CEDI = 2            # días CON RECEPCIÓN en el CEDI entre la compra y su llegada
LT_CEDI_TIENDA = 1               # días entre la llegada al CEDI y la llegada a la tienda
CAPACIDAD_RECEPCION_FESTIVO = 0.40   # fracción del volumen normal que se recibe en festivos y domingo de puente
CRECIMIENTO_VENTA = 0.0          # fracción: 0.10 = se proyecta 10 % más venta que el consumo de Celes
MIN_EXHIBICION_DEFAULT = 1.0     # cajas (canastillas) de exhibición si la columna MIN está vacía
EXHIBIR_SIN_VENTA = True         # comprar el MIN también para tienda×ítem con consumo 0 (la exhibición manda)
DESCONTAR_INVENTARIO_VENCIDO = False  # True = existencias con último ingreso > vida útil cuentan como 0
INVENTARIO_SEGUN_VIDA_UTIL = True     # True = proyección por lotes: lo que pasa su vida útil antes de la
                                      # llegada de la compra deja de contar (existencias, tránsito, pedido de hoy)
UMBRAL_REDONDEO = 0.5            # fracción de caja desde la que se redondea hacia arriba en cada tienda
ALFA_SUBIDA = 0.5                # suavizado: fracción del hueco (compra > venta) que se cierra en esta compra
ALFA_BAJADA = 1.0                # suavizado: fracción del exceso (compra < venta) que se corrige (1 = completo)
USAR_SS_CELES = True             # sumar el stock de seguridad de Celes (escalado al horizonte de la compra)
INCLUIR_TIENDAS_SIN_PEDIDO = True    # las tiendas de «Tiendas_No generar pedido» igual venden: se incluyen
INCLUYE_VENTA_HOY = True         # el inventario de Celes es de inicio del día: la venta de hoy se descuenta
DIAS_COMPRA = (0, 1, 2, 3, 4, 5)     # lunes a sábado (0 = lunes); el domingo no se compra
DIAS_MAX_ATRASO_OC = 3           # una OC con entrega vencida y sin ingreso en Stock cuenta como «atrasada»
                                 # hasta estos días; más vieja, se toma como vencida (no se cuenta)
# =============================================================================


@dataclass
class ParametrosCompra:
    lt_proveedor_cedi: int = LT_PROVEEDOR_CEDI
    lt_cedi_tienda: int = LT_CEDI_TIENDA
    capacidad_festivo: float = CAPACIDAD_RECEPCION_FESTIVO
    crecimiento_venta: float = CRECIMIENTO_VENTA
    min_exhibicion_default: float = MIN_EXHIBICION_DEFAULT
    exhibir_sin_venta: bool = EXHIBIR_SIN_VENTA
    descontar_inventario_vencido: bool = DESCONTAR_INVENTARIO_VENCIDO
    inventario_segun_vida_util: bool = INVENTARIO_SEGUN_VIDA_UTIL
    umbral_redondeo: float = UMBRAL_REDONDEO
    alfa_subida: float = ALFA_SUBIDA
    alfa_bajada: float = ALFA_BAJADA
    usar_ss_celes: bool = USAR_SS_CELES
    incluir_tiendas_sin_pedido: bool = INCLUIR_TIENDAS_SIN_PEDIDO
    incluye_venta_hoy: bool = INCLUYE_VENTA_HOY
    dias_max_atraso_oc: int = DIAS_MAX_ATRASO_OC
    dias_compra: frozenset = frozenset(DIAS_COMPRA)
    lt_por_dia: dict = field(default_factory=dict)        # {día semana: LT}; vacío = LT_PROVEEDOR_CEDI
    factor_dia_semana: dict = field(default_factory=dict)  # {día semana: factor de venta}
    excepciones: dict = field(default_factory=dict)        # {fecha: {...}} ver CalendarioCompras
    por_item: dict = field(default_factory=dict)           # {ítem: {'crecimiento', 'multiplo', 'minimo'}}

    def calendario(self):
        lt = {d: int(self.lt_por_dia.get(d, self.lt_proveedor_cedi)) for d in range(7)}
        return CalendarioCompras(
            lt_por_dia=lt, dias_compra=frozenset(self.dias_compra),
            lt_cedi_tienda=int(self.lt_cedi_tienda), capacidad_festivo=float(self.capacidad_festivo),
            factor_dia_semana=dict(self.factor_dia_semana), excepciones=dict(self.excepciones),
            incluye_venta_hoy=bool(self.incluye_venta_hoy),
        )

    def _de_item(self, item, campo):
        return self.por_item.get(item, {}).get(campo)

    def crecimiento_item(self, item):
        v = self._de_item(item, 'crecimiento')
        return float(self.crecimiento_venta if v is None else v)

    def multiplo_item(self, item):
        v = self._de_item(item, 'multiplo')
        return max(1, int(v)) if v is not None else 1

    def minimo_item(self, item):
        v = self._de_item(item, 'minimo')
        return max(0, int(v)) if v is not None else 0


# Hoja «General»: (nombre en el Excel, atributo, tipo, descripción). Tipos: int, pct, num, bool.
_GENERAL = [
    ('LT_PROVEEDOR_CEDI', 'lt_proveedor_cedi', 'int',
     'Días con recepción en el CEDI entre la compra y su llegada (el domingo no cuenta)'),
    ('LT_CEDI_TIENDA', 'lt_cedi_tienda', 'int', 'Días entre la llegada al CEDI y la llegada a la tienda'),
    ('CAPACIDAD_RECEPCION_FESTIVO (%)', 'capacidad_festivo', 'pct',
     'Porcentaje del volumen normal que el CEDI recibe en festivos y en el domingo de un puente'),
    ('CRECIMIENTO_VENTA (%)', 'crecimiento_venta', 'pct',
     'Aumento de venta proyectado sobre el consumo de Celes (10 = +10 %). Se puede ajustar por ítem'),
    ('MIN_EXHIBICION_DEFAULT (cajas)', 'min_exhibicion_default', 'num',
     'Canastillas de exhibición cuando la columna MIN de Tiendas × Ítem está vacía'),
    ('EXHIBIR_SIN_VENTA', 'exhibir_sin_venta', 'bool',
     'Sí = también se compra el MIN para tienda×ítem con consumo 0'),
    ('DESCONTAR_INVENTARIO_VENCIDO', 'descontar_inventario_vencido', 'bool',
     'Sí = las existencias con último ingreso más viejo que la vida útil cuentan como 0'),
    ('INVENTARIO_SEGUN_VIDA_UTIL', 'inventario_segun_vida_util', 'bool',
     'Sí = en la tienda solo cuenta lo que se alcanza a vender antes de vencer (por último ingreso y vida útil); '
     'No = inventario de Celes tal cual'),
    ('UMBRAL_REDONDEO (fracción de caja)', 'umbral_redondeo', 'num',
     'Desde qué fracción de caja se redondea hacia arriba en cada tienda (0,5 = redondeo normal)'),
    ('ALFA_SUBIDA', 'alfa_subida', 'num',
     'Suavizado: fracción del faltante que se cierra en esta compra (1 = todo de una vez)'),
    ('ALFA_BAJADA', 'alfa_bajada', 'num',
     'Suavizado: fracción del sobrante que se corrige en esta compra (1 = todo de una vez)'),
    ('USAR_SS_CELES', 'usar_ss_celes', 'bool', 'Sí = suma el stock de seguridad de Celes'),
    ('INCLUIR_TIENDAS_SIN_PEDIDO', 'incluir_tiendas_sin_pedido', 'bool',
     'Sí = las tiendas de «Tiendas_No generar pedido» también entran a la compra'),
    ('INCLUYE_VENTA_HOY', 'incluye_venta_hoy', 'bool',
     'Sí = la venta de hoy se descuenta del inventario de Celes (inventario de inicio del día)'),
    ('DIAS_MAX_ATRASO_OC', 'dias_max_atraso_oc', 'int',
     'Días que una OC puede pasar de su fecha de entrega sin ingreso en Stock y seguir contando como en camino'),
]

_HOJAS = {'general': 'General', 'compra_dia': 'Compra por día', 'calendario': 'Calendario',
          'por_item': 'Por Ítem'}

_COL_DIA = ['Día', 'Se compra (S/N)', 'LT proveedor (días hábiles)', 'Factor venta']
_COL_CAL = ['Fecha', 'Capacidad recepción CEDI (%)', 'Se compra (S/N)', 'Factor venta', 'Motivo']
_COL_ITEM = ['ITEM', 'DESCRIPCION', 'Crecimiento venta (%)', 'Múltiplo compra (cajas)',
             'Compra mínima (cajas)']


def _norm(texto):
    """minúsculas, sin tildes ni espacios sobrantes (para casar nombres de hojas/días/columnas)."""
    s = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode()
    return ' '.join(s.lower().split())


def _vacio(v):
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def _a_bool(v):
    if isinstance(v, bool):
        return v
    s = _norm(v)
    if s in ('si', 's', 'true', 'verdadero', '1', '1.0', 'x', 'yes'):
        return True
    if s in ('no', 'n', 'false', 'falso', '0', '0.0'):
        return False
    raise ValueError(f'«{v}» no es Sí/No')


def _a_pct(v):
    """Porcentaje 0–100 (o fracción 0–1) → fracción."""
    x = float(str(v).replace('%', '').replace(',', '.')) if isinstance(v, str) else float(v)
    return x if abs(x) < 1 else x / 100.0


def _a_num(v):
    return float(str(v).replace(',', '.')) if isinstance(v, str) else float(v)


_CONVERSORES = {'int': lambda v: int(round(_a_num(v))), 'pct': _a_pct, 'num': _a_num, 'bool': _a_bool}


def _validar(params):
    """Rangos válidos; retorna lista de (atributo, mensaje) de los que se salen."""
    errores = []
    reglas = [
        ('lt_proveedor_cedi', lambda v: 0 <= v <= 15, 'entre 0 y 15 días'),
        ('lt_cedi_tienda', lambda v: 0 <= v <= 10, 'entre 0 y 10 días'),
        ('dias_max_atraso_oc', lambda v: 0 <= v <= 60, 'entre 0 y 60 días'),
        ('capacidad_festivo', lambda v: 0 <= v <= 1, 'entre 0 % y 100 %'),
        ('crecimiento_venta', lambda v: -0.9 <= v <= 5, 'entre -90 % y 500 %'),
        ('min_exhibicion_default', lambda v: 0 <= v <= 50, 'entre 0 y 50 cajas'),
        ('umbral_redondeo', lambda v: 0 < v <= 1, 'mayor que 0 y hasta 1'),
        ('alfa_subida', lambda v: 0 <= v <= 1, 'entre 0 y 1'),
        ('alfa_bajada', lambda v: 0 <= v <= 1, 'entre 0 y 1'),
    ]
    for attr, ok, texto in reglas:
        if not ok(getattr(params, attr)):
            errores.append((attr, f'debe estar {texto}'))
    return errores


def _alerta(msg):
    return {'tipo': 'ADVERTENCIA', 'item': '-', 'mensaje': f'Parámetros de compra: {msg}'}


def _hoja(hojas, clave):
    objetivo = _norm(_HOJAS[clave])
    for nombre, df in hojas.items():
        if _norm(nombre) == objetivo:
            df = df.copy()
            df.columns = [str(c).strip() for c in df.columns]
            return df
    return None


def _col(df, nombre):
    """Columna de `df` cuyo nombre normalizado coincide con `nombre` (o None)."""
    objetivo = _norm(nombre)
    for c in df.columns:
        if _norm(c) == objetivo:
            return c
    return None


def _leer_general(df, valores, alertas):
    c_par, c_val = _col(df, 'Parámetro'), _col(df, 'Valor')
    if c_par is None or c_val is None:
        alertas.append(_alerta('la hoja «General» debe tener columnas «Parámetro» y «Valor»; se ignoró.'))
        return
    spec = {_norm(n): (a, t) for n, a, t, _ in _GENERAL}
    # Se acepta también el nombre sin la unidad entre paréntesis.
    spec.update({_norm(n.split(' (')[0]): (a, t) for n, a, t, _ in _GENERAL})
    for _, fila in df.iterrows():
        nombre, valor = fila[c_par], fila[c_val]
        if _vacio(nombre):
            continue
        clave = spec.get(_norm(nombre))
        if clave is None:
            alertas.append(_alerta(f'parámetro «{nombre}» desconocido en la hoja «General»; se ignoró.'))
            continue
        if _vacio(valor):
            continue
        attr, tipo = clave
        try:
            valores[attr] = _CONVERSORES[tipo](valor)
        except (ValueError, TypeError):
            alertas.append(_alerta(f'valor «{valor}» inválido para «{nombre}»; se usa el valor por defecto.'))


def _dia_semana(texto):
    n = _norm(texto)
    for i, nombre in enumerate(DIAS_SEMANA):
        if _norm(nombre) == n or _norm(nombre)[:3] == n[:3]:
            return i
    return None


def _leer_compra_dia(df, valores, alertas):
    c_dia = _col(df, 'Día')
    if c_dia is None:
        alertas.append(_alerta('la hoja «Compra por día» no tiene la columna «Día»; se ignoró.'))
        return
    c_compra, c_lt, c_fac = (_col(df, n) for n in _COL_DIA[1:])
    dias_compra, lt, factor = set(valores.get('dias_compra', DIAS_COMPRA)), {}, {}
    for _, fila in df.iterrows():
        dia = _dia_semana(fila[c_dia]) if not _vacio(fila[c_dia]) else None
        if dia is None:
            continue
        try:
            if c_compra and not _vacio(fila[c_compra]):
                (dias_compra.add if _a_bool(fila[c_compra]) else dias_compra.discard)(dia)
            if c_lt and not _vacio(fila[c_lt]):
                lt[dia] = int(round(_a_num(fila[c_lt])))
            if c_fac and not _vacio(fila[c_fac]):
                factor[dia] = _a_num(fila[c_fac])
        except (ValueError, TypeError):
            alertas.append(_alerta(f'fila «{fila[c_dia]}» de «Compra por día» con valores inválidos; se ignoró.'))
    valores['dias_compra'] = frozenset(dias_compra)
    valores['lt_por_dia'] = lt
    valores['factor_dia_semana'] = factor


def _leer_calendario(df, valores, alertas):
    c_fecha = _col(df, 'Fecha')
    if c_fecha is None:
        alertas.append(_alerta('la hoja «Calendario» no tiene la columna «Fecha»; se ignoró.'))
        return
    c_cap, c_compra, c_fac, c_mot = (_col(df, n) for n in _COL_CAL[1:])
    from .compras_preprocessor import a_fecha  # import diferido: evita ciclo

    fechas = a_fecha(df[c_fecha].reset_index(drop=True))
    excepciones = {}
    for (_, fila), f in zip(df.iterrows(), fechas):
        if pd.isna(f):
            continue
        exc = {}
        try:
            if c_cap and not _vacio(fila[c_cap]):
                exc['capacidad'] = min(1.0, max(0.0, _a_pct(fila[c_cap])))
            if c_compra and not _vacio(fila[c_compra]):
                exc['compra'] = _a_bool(fila[c_compra])
            if c_fac and not _vacio(fila[c_fac]):
                exc['factor_venta'] = _a_num(fila[c_fac])
        except (ValueError, TypeError):
            alertas.append(_alerta(f'fila del {f:%Y-%m-%d} en «Calendario» con valores inválidos; se ignoró.'))
            continue
        if c_mot and not _vacio(fila[c_mot]):
            exc['motivo'] = str(fila[c_mot]).strip()
        if exc:
            excepciones[f.date()] = exc
    valores['excepciones'] = excepciones


def _leer_por_item(df, valores, alertas):
    from .preprocessor import normalize_item  # import diferido: evita ciclo en tiempo de carga

    c_item = _col(df, 'ITEM')
    if c_item is None:
        alertas.append(_alerta('la hoja «Por Ítem» no tiene la columna «ITEM»; se ignoró.'))
        return
    cols = {'crecimiento': (_col(df, _COL_ITEM[2]), _a_pct),
            'multiplo': (_col(df, _COL_ITEM[3]), lambda v: int(round(_a_num(v)))),
            'minimo': (_col(df, _COL_ITEM[4]), lambda v: int(round(_a_num(v))))}
    por_item = {}
    for _, fila in df.iterrows():
        item = normalize_item(fila[c_item])
        if item is None:
            continue
        datos = {}
        for campo, (col, conv) in cols.items():
            if col and not _vacio(fila[col]):
                try:
                    datos[campo] = conv(fila[col])
                except (ValueError, TypeError):
                    alertas.append(_alerta(f'ítem {item}: «{fila[col]}» inválido en «{col}»; se ignoró.'))
        if datos:
            por_item[int(item)] = datos
    valores['por_item'] = por_item


def cargar_parametros(hojas=None, overrides=None):
    """Arma los parámetros de la compra.

    hojas: dict {nombre de hoja: DataFrame} de `Parametros_Compra.xlsx` (o None = valores por defecto).
    overrides: dict {atributo: valor} que se aplica al final (p. ej. desde la app).
    Retorna (ParametrosCompra, alertas).
    """
    alertas, valores = [], {}
    if hojas:
        for clave, lector in (('general', _leer_general), ('compra_dia', _leer_compra_dia),
                              ('calendario', _leer_calendario), ('por_item', _leer_por_item)):
            df = _hoja(hojas, clave)
            if df is not None:
                lector(df, valores, alertas)
    validos = {f.name for f in fields(ParametrosCompra)}
    for k, v in (overrides or {}).items():
        if k in validos and v is not None:
            valores[k] = v
    params = ParametrosCompra(**valores)
    defaults = ParametrosCompra()
    for attr, msg in _validar(params):
        alertas.append(_alerta(f'{attr} = {getattr(params, attr)} {msg}; se usa {getattr(defaults, attr)}.'))
        params = replace(params, **{attr: getattr(defaults, attr)})
    return params, alertas


def _valor_excel(params, attr, tipo):
    v = getattr(params, attr)
    if tipo == 'bool':
        return 'Sí' if v else 'No'
    if tipo == 'pct':
        return round(v * 100, 4)
    return v


def tabla_parametros(params):
    """[(nombre, valor)] de la hoja General, con los valores efectivos (para la hoja «Parámetros»)."""
    filas = [(n, _valor_excel(params, a, t)) for n, a, t, _ in _GENERAL]
    filas.append(('DIAS_COMPRA', ', '.join(DIAS_SEMANA[d] for d in sorted(params.dias_compra))))
    cal = params.calendario()
    filas.append(('LT por día de compra', ', '.join(f'{DIAS_SEMANA[d][:3]} {cal.lt_por_dia[d]}'
                                                    for d in sorted(params.dias_compra))))
    if any(abs(f - 1) > 1e-9 for f in params.factor_dia_semana.values()):
        filas.append(('Factor venta por día', ', '.join(f'{DIAS_SEMANA[d][:3]} {f:g}'
                                                         for d, f in sorted(params.factor_dia_semana.items()))))
    filas.append(('Fechas con excepción en Calendario', len(params.excepciones)))
    filas.append(('Ítems con parámetros propios', len(params.por_item)))
    return filas


def tabla_general(params):
    """Hoja «General» / «Parametros» con los valores de `params` (porcentajes en 0–100)."""
    return pd.DataFrame([(n, _valor_excel(params, a, t), d) for n, a, t, d in _GENERAL],
                        columns=['Parámetro', 'Valor', 'Descripción'])


def tabla_dias(params):
    """Hoja «Compra por día» / «Dias de compra» con los valores de `params`."""
    cal = params.calendario()
    return pd.DataFrame([(DIAS_SEMANA[i], 'Sí' if i in params.dias_compra else 'No', cal.lt_por_dia[i],
                          float(params.factor_dia_semana.get(i, 1.0))) for i in range(7)], columns=_COL_DIA)


def tabla_calendario(params, anios=None):
    """Hoja «Calendario»: festivos de Colombia (y domingos de puente) de `anios` + las excepciones de
    `params` que no son festivos. Los festivos se aplican solos aunque no estén listados."""
    anios = anios or (date.today().year, date.today().year + 1)
    cal = params.calendario()
    filas, vistas = [], set()
    for anio in anios:
        for f, nombre in sorted(festivos_colombia(anio).items()):
            dias = [(f - timedelta(days=1), f'Domingo de puente ({nombre})')] if f.weekday() == 0 else []
            for d, motivo in dias + [(f, f'Festivo: {nombre}')]:
                exc = params.excepciones.get(d, {})
                compra = exc.get('compra')
                filas.append((d, cal.capacidad(d) * 100, None if compra is None else ('Sí' if compra else 'No'),
                              exc.get('factor_venta'), exc.get('motivo') or motivo))
                vistas.add(d)
    for f, exc in sorted(params.excepciones.items()):
        if f in vistas:
            continue
        compra = exc.get('compra')
        filas.append((f, exc['capacidad'] * 100 if exc.get('capacidad') is not None else None,
                      None if compra is None else ('Sí' if compra else 'No'), exc.get('factor_venta'),
                      exc.get('motivo', '')))
    df = pd.DataFrame(filas, columns=_COL_CAL).sort_values('Fecha', kind='stable').reset_index(drop=True)
    df['Fecha'] = pd.to_datetime(df['Fecha']).dt.date
    return df


def plantilla_parametros_bytes(portafolio=None, anios=None):
    """Excel de `Parametros_Compra.xlsx` con los valores por defecto.

    portafolio (opcional): DataFrame de DB_Portafolio Fruver para precargar los ítems ACTIVOS en la
    hoja «Por Ítem» (sin valores: vacío = usa el valor general). anios: años de festivos a listar
    (por defecto el actual y el siguiente). Los festivos se aplican solos aunque no estén listados;
    la lista sirve para verlos y cambiar su capacidad.
    """
    from .exporter import _autofit, _style_header, _tabla  # import diferido: evita ciclo

    p = ParametrosCompra()
    general, dia, calendario = tabla_general(p), tabla_dias(p), tabla_calendario(p, anios)

    if portafolio is not None and len(portafolio) > 0:
        from .preprocessor import _is_active, _normalize_item_series
        port = portafolio.copy()
        port.columns = [str(c).strip() for c in port.columns]
        port = port[_is_active(port['ESTADO'])]
        items = pd.DataFrame({'ITEM': _normalize_item_series(port['ITEM']).values,
                              'DESCRIPCION': port.get('DESCRIPCION', pd.Series('', index=port.index)).values})
        items = items.dropna(subset=['ITEM']).sort_values('ITEM')
        por_item = items.reindex(columns=_COL_ITEM)
    else:
        por_item = pd.DataFrame(columns=_COL_ITEM)

    instrucciones = pd.DataFrame({'Instrucciones': [
        'Parámetros de la compra Fruver. Toda celda vacía usa el valor por defecto del sistema.',
        'Porcentajes: escriba de 0 a 100 (10 = 10 %). Un valor entre 0 y 1 se lee como fracción (0,4 = 40 %).',
        'General: parámetros globales. Compra por día: qué días se compra, lead time (días con recepción en el '
        'CEDI) y factor de venta por día de la semana.',
        'Calendario: excepciones por fecha. Capacidad 0 = el CEDI no recibe ese día; 100 = día normal. '
        'Los festivos de Colombia se aplican solos aunque no estén en la lista; para anular uno, ponga 100.',
        'Factor venta (Compra por día / Calendario): multiplica el consumo de Celes (1,2 = 20 % más ese día).',
        'Por Ítem: ajustes por ítem (crecimiento, múltiplo de compra del proveedor, compra mínima). Vacío = general.',
    ]})

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        for nombre, df in (('General', general), ('Compra por día', dia), ('Calendario', calendario),
                           ('Por Ítem', por_item), ('Instrucciones', instrucciones)):
            df.to_excel(writer, index=False, sheet_name=nombre)
            ws = writer.sheets[nombre]
            _style_header(ws)
            _tabla(ws)
            _autofit(ws)
        writer.sheets['General'].column_dimensions['C'].width = 95
        writer.sheets['Instrucciones'].column_dimensions['A'].width = 120
        for fila in range(2, writer.sheets['Calendario'].max_row + 1):
            writer.sheets['Calendario'].cell(row=fila, column=1).number_format = 'DD/MM/YYYY'
    return buffer.getvalue()


COLUMNAS_COMPRAS_TRANSITO = ['Item', 'Descripción', 'Cantidad (cajas)', 'Fecha esperada CEDI',
                             'Orden de compra', 'Proveedor', 'Fecha orden']


def plantilla_compras_transito_bytes():
    """Excel vacío de `Compras_Transito.xlsx` (OC al proveedor que aún no llegan al CEDI).

    Columnas PROVISIONALES: se ajustarán a la base real de compras cuando se integre. Obligatorias
    para el sistema: Item, Cantidad (cajas) y Fecha esperada CEDI; el resto es referencia."""
    from .exporter import _autofit, _style_header, _tabla  # import diferido: evita ciclo

    instrucciones = pd.DataFrame({'Instrucciones': [
        'Una fila por orden de compra (o por ítem de una orden) que ya se pidió al proveedor y AÚN NO llega al CEDI.',
        'Obligatorias: Item (código Siesa), Cantidad (cajas) y Fecha esperada CEDI. Las demás columnas son referencia.',
        'Cuentan para la compra de hoy las OC que llegan al CEDI hasta el mismo día que llega la compra de hoy.',
        'Una OC con fecha anterior a hoy se sigue contando (se asume que llegará) y genera una alerta: sacarla del '
        'archivo cuando ya se recibió o se canceló.',
        'Sin este archivo, o vacío, la compra se calcula como si no hubiera nada pedido: riesgo de comprar doble.',
        'Formato provisional: se reemplazará por la base de datos de compras.',
    ]})
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        for nombre, df in (('OC en tránsito', pd.DataFrame(columns=COLUMNAS_COMPRAS_TRANSITO)),
                           ('Instrucciones', instrucciones)):
            df.to_excel(writer, index=False, sheet_name=nombre)
            ws = writer.sheets[nombre]
            _style_header(ws)
            _tabla(ws)
            _autofit(ws)
        writer.sheets['Instrucciones'].column_dimensions['A'].width = 120
        for col in 'ABCDEFG':
            writer.sheets['OC en tránsito'].column_dimensions[col].width = 22
    return buffer.getvalue()
