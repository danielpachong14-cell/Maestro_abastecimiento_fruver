"""Calendario de compras: cuándo llega una compra (lead time en días con recepción en el CEDI; sin
recepción el domingo), cuántos días cubre y el ajuste por festivos de Colombia. Funciones puras.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

DIAS_SEMANA = ('Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo')

_GUARDA_DIAS = 60  # búsqueda máxima hacia adelante; un calendario sin recepción en 60 días es un error


def _pascua(anio):
    """Domingo de Pascua (calendario gregoriano, algoritmo anónimo de Meeus/Jones/Butcher)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(anio, mes, dia + 1)


def _lunes_siguiente(d):
    """Ley Emiliani: el festivo se corre al lunes siguiente (si ya es lunes, se queda)."""
    return d + timedelta(days=(7 - d.weekday()) % 7)


def festivos_colombia(anio):
    """Festivos nacionales de Colombia del año: {fecha: nombre}.

    Fijos, trasladables al lunes (Ley 51 de 1983) y los que dependen de la Pascua."""
    p = _pascua(anio)
    fest = {
        date(anio, 1, 1): 'Año Nuevo',
        date(anio, 5, 1): 'Día del Trabajo',
        date(anio, 7, 20): 'Independencia',
        date(anio, 8, 7): 'Batalla de Boyacá',
        date(anio, 12, 8): 'Inmaculada Concepción',
        date(anio, 12, 25): 'Navidad',
        p - timedelta(days=3): 'Jueves Santo',
        p - timedelta(days=2): 'Viernes Santo',
        p + timedelta(days=43): 'Ascensión del Señor',
        p + timedelta(days=64): 'Corpus Christi',
        p + timedelta(days=71): 'Sagrado Corazón',
    }
    for mes, dia, nombre in (
        (1, 6, 'Reyes Magos'), (3, 19, 'San José'), (6, 29, 'San Pedro y San Pablo'),
        (8, 15, 'Asunción de la Virgen'), (10, 12, 'Día de la Raza'),
        (11, 1, 'Todos los Santos'), (11, 11, 'Independencia de Cartagena'),
    ):
        fest[_lunes_siguiente(date(anio, mes, dia))] = nombre
    return fest


@dataclass
class PlanCompra:
    """Fechas y pesos de demanda de la compra que se genera hoy.

    `peso_hasta_llegada` y `peso_cobertura` son días ponderados por el factor de venta de cada fecha:
    multiplicados por el consumo diario dan la venta esperada. `peso_cobertura` ya incluye la
    capacidad reducida de la propia llegada y lo que se anticipa de las llegadas reducidas siguientes.
    """
    fecha_datos: date
    fecha_compra: date
    es_dia_compra_hoy: bool
    llegada_cedi: date
    llegada_tienda: date
    capacidad_llegada: float
    dias_hasta_llegada: tuple
    dias_cobertura: tuple
    siguiente_compra: date
    siguiente_llegada_tienda: date
    peso_hasta_llegada: float
    peso_cobertura_base: float
    peso_cobertura: float
    anticipaciones: tuple = field(default_factory=tuple)
    factores_hasta_llegada: tuple = field(default_factory=tuple)   # factor de venta de cada día hasta la llegada

    def peso_desde(self, fecha):
        """Días de venta ponderados desde `fecha` (incluida) hasta el día antes de la llegada: la venta
        que se come lo que llega a la tienda en `fecha` (OC en tránsito) antes de que llegue la compra."""
        fecha = fecha.date() if hasattr(fecha, 'date') and callable(fecha.date) else fecha
        factores = self.factores_hasta_llegada or (1.0,) * len(self.dias_hasta_llegada)
        return float(sum(f for d, f in zip(self.dias_hasta_llegada, factores) if d >= fecha))

    @property
    def n_dias_hasta_llegada(self):
        return len(self.dias_hasta_llegada)

    @property
    def n_dias_cobertura(self):
        return len(self.dias_cobertura)


@dataclass
class CalendarioCompras:
    """Calendario de compra y recepción.

    lt_por_dia: lead time proveedor → CEDI (en días con recepción) según el día de la semana en
    que se compra (0 = lunes). dias_compra: días de la semana en que se compra.
    excepciones: {fecha: {'capacidad': float|None, 'compra': bool|None,
    'factor_venta': float|None, 'motivo': str}}; None = sin cambio para ese campo.
    """
    lt_por_dia: dict = field(default_factory=lambda: {d: 2 for d in range(7)})
    dias_compra: frozenset = frozenset({0, 1, 2, 3, 4, 5})
    lt_cedi_tienda: int = 1
    capacidad_festivo: float = 0.4
    dias_sin_recepcion: frozenset = frozenset({6})
    factor_dia_semana: dict = field(default_factory=dict)
    excepciones: dict = field(default_factory=dict)
    incluye_venta_hoy: bool = True

    def __post_init__(self):
        self._festivos_cache = {}

    # ── Propiedades de una fecha ─────────────────────────────────────────────
    def festivo(self, d):
        """Nombre del festivo de esa fecha, o None."""
        if d.year not in self._festivos_cache:
            self._festivos_cache[d.year] = festivos_colombia(d.year)
        return self._festivos_cache[d.year].get(d)

    def _domingo_de_puente(self, d):
        return d.weekday() == 6 and self.festivo(d + timedelta(days=1)) is not None

    def _excepcion(self, d, campo):
        return self.excepciones.get(d, {}).get(campo)

    def capacidad(self, d):
        """Fracción del volumen normal que el CEDI puede recibir ese día (0 = no recibe)."""
        exc = self._excepcion(d, 'capacidad')
        if exc is not None:
            return float(exc)
        if self.festivo(d) is not None or self._domingo_de_puente(d):
            return float(self.capacidad_festivo)
        return 0.0 if d.weekday() in self.dias_sin_recepcion else 1.0

    def es_dia_compra(self, d):
        exc = self._excepcion(d, 'compra')
        if exc is not None:
            return bool(exc)
        return d.weekday() in self.dias_compra

    def factor_venta(self, d):
        """Multiplicador del consumo diario ese día (día de semana × excepción de la fecha)."""
        base = float(self.factor_dia_semana.get(d.weekday(), 1.0))
        exc = self._excepcion(d, 'factor_venta')
        return base * (float(exc) if exc is not None else 1.0)

    def motivo(self, d):
        """Por qué la fecha no es normal (para la hoja Parámetros); '' si es normal."""
        exc = self._excepcion(d, 'motivo')
        if exc:
            return str(exc)
        nombre = self.festivo(d)
        if nombre:
            return f'Festivo: {nombre}'
        if self._domingo_de_puente(d):
            return 'Domingo de puente festivo'
        return ''

    # ── Llegadas ─────────────────────────────────────────────────────────────
    def llegada_cedi(self, fecha_compra):
        """Fecha en que llega al CEDI una compra hecha en `fecha_compra`."""
        lt = int(self.lt_por_dia.get(fecha_compra.weekday(), 2))
        d, contados = fecha_compra, 0
        for _ in range(_GUARDA_DIAS):
            if lt == 0 and self.capacidad(d) > 0:
                return d
            d = d + timedelta(days=1)
            if self.capacidad(d) > 0:
                contados += 1
                if contados >= lt:
                    return d
        raise ValueError(f'El calendario no tiene días con recepción en el CEDI después del '
                         f'{fecha_compra:%Y-%m-%d}: revisar la hoja «Calendario» de Parámetros.')

    def llegada_tienda(self, fecha_compra):
        return self.llegada_cedi(fecha_compra) + timedelta(days=int(self.lt_cedi_tienda))

    def siguiente_compra(self, d):
        """Próximo día de compra estrictamente posterior a `d`."""
        e = d
        for _ in range(_GUARDA_DIAS):
            e = e + timedelta(days=1)
            if self.es_dia_compra(e):
                return e
        raise ValueError(f'No hay días de compra en los {_GUARDA_DIAS} días siguientes al '
                         f'{d:%Y-%m-%d}: revisar la hoja «Compra por día» de Parámetros.')

    def _siguiente_compra_que_llega_despues(self, fecha_compra):
        """Primera compra futura que llega a tienda DESPUÉS que la de `fecha_compra` (si dos compras
        llegan el mismo día, la segunda no cierra la cobertura de la primera)."""
        llegada = self.llegada_tienda(fecha_compra)
        k = self.siguiente_compra(fecha_compra)
        for _ in range(_GUARDA_DIAS):
            if self.llegada_tienda(k) > llegada:
                return k
            k = self.siguiente_compra(k)
        raise ValueError('No se encontró una compra posterior con llegada distinta: revisar el calendario.')

    def _rango(self, desde, hasta):
        """Fechas desde `desde` (incluida) hasta `hasta` (excluida)."""
        return tuple(desde + timedelta(days=i) for i in range(max(0, (hasta - desde).days)))

    def _peso(self, fechas):
        return float(sum(self.factor_venta(d) for d in fechas))

    # ── Plan de la compra de hoy ─────────────────────────────────────────────
    def plan(self, fecha_datos):
        """Plan de la compra que se genera con los datos de `fecha_datos`.

        Si ese día no es de compra, se planea la del siguiente día de compra (la venta hasta la
        llegada se sigue contando desde `fecha_datos`)."""
        es_compra = self.es_dia_compra(fecha_datos)
        fecha_compra = fecha_datos if es_compra else self.siguiente_compra(fecha_datos)
        llegada_cedi = self.llegada_cedi(fecha_compra)
        llegada = self.llegada_tienda(fecha_compra)
        inicio_venta = fecha_datos if self.incluye_venta_hoy else fecha_datos + timedelta(days=1)
        dias_hasta = self._rango(inicio_venta, llegada)

        sig = self._siguiente_compra_que_llega_despues(fecha_compra)
        sig_llegada = self.llegada_tienda(sig)
        cobertura = self._rango(llegada, sig_llegada)
        peso_base = self._peso(cobertura)
        capacidad = self.capacidad(llegada_cedi)

        anticipaciones = []
        if capacidad < 1:
            peso = capacidad * peso_base
        else:
            # Anticipa lo que no alcanzan a traer las llegadas reducidas que siguen (festivos),
            # hasta la próxima llegada con capacidad completa.
            peso, j = peso_base, sig
            for _ in range(_GUARDA_DIAS):
                cedi_j = self.llegada_cedi(j)
                cap_j = self.capacidad(cedi_j)
                if cap_j >= 1:
                    break
                j_sig = self._siguiente_compra_que_llega_despues(j)
                cob_j = self._rango(self.llegada_tienda(j), self.llegada_tienda(j_sig))
                extra = (1 - cap_j) * self._peso(cob_j)
                peso += extra
                anticipaciones.append({
                    'fecha_compra': j, 'llegada_cedi': cedi_j, 'capacidad': cap_j,
                    'dias': cob_j, 'peso_anticipado': extra,
                })
                j = j_sig

        return PlanCompra(
            fecha_datos=fecha_datos, fecha_compra=fecha_compra, es_dia_compra_hoy=es_compra,
            llegada_cedi=llegada_cedi, llegada_tienda=llegada, capacidad_llegada=capacidad,
            dias_hasta_llegada=dias_hasta, dias_cobertura=cobertura,
            siguiente_compra=sig, siguiente_llegada_tienda=sig_llegada,
            peso_hasta_llegada=self._peso(dias_hasta), peso_cobertura_base=peso_base,
            peso_cobertura=peso, anticipaciones=tuple(anticipaciones),
            factores_hasta_llegada=tuple(self.factor_venta(d) for d in dias_hasta),
        )


_DIA_POR_PREFIJO = {'lun': 0, 'mar': 1, 'mie': 2, 'jue': 3, 'vie': 4, 'sab': 5, 'dom': 6}


def interpretar_dia_compra(texto):
    """Lee la columna «DIA DE COMPRA» del Portafolio (tabla STATUS).

    'Todos los dias' / vacío      → ('todos', None)          sigue el calendario general
    'LUNES-MIERCOLES-VIERNES'     → ('dias', frozenset({0, 2, 4}))
    'CADA 15 DIAS'                → ('cada', 15)
    'DESCONTINUADO'               → ('descontinuado', None)
    cualquier otro texto          → ('desconocido', texto)
    """
    import re
    import unicodedata

    if texto is None or (isinstance(texto, float) and texto != texto) or not str(texto).strip():
        return ('todos', None)
    s = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode().lower().strip()
    s = ' '.join(s.split())
    if s in ('todos los dias', 'todos', 'diario', 'todos los dia', 'lunes a sabado'):
        return ('todos', None)
    if s.startswith('descontinuad'):
        return ('descontinuado', None)
    m = re.fullmatch(r'cada\s+(\d+)\s*dias?', s)
    if m:
        n = int(m.group(1))
        return ('cada', n) if n > 0 else ('desconocido', str(texto))
    partes = [p for p in re.split(r'[-,/;]|\sy\s', s) if p.strip()]
    dias = set()
    for p in partes:
        d = _DIA_POR_PREFIJO.get(p.strip()[:3])
        if d is None:
            return ('desconocido', str(texto))
        dias.add(d)
    return ('dias', frozenset(dias)) if dias else ('desconocido', str(texto))
