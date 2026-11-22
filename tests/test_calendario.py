"""Tests del calendario de compras (llegadas, cobertura, festivos y puentes)."""

from datetime import date, timedelta

import pytest

from core.calendario import CalendarioCompras, festivos_colombia


def test_festivos_colombia_2026_incluyen_traslados_ley_emiliani_y_pascua():
    f = festivos_colombia(2026)
    esperados = {
        date(2026, 1, 1), date(2026, 1, 12), date(2026, 3, 23), date(2026, 4, 2), date(2026, 4, 3),
        date(2026, 5, 1), date(2026, 5, 18), date(2026, 6, 8), date(2026, 6, 15), date(2026, 6, 29),
        date(2026, 7, 20), date(2026, 8, 7), date(2026, 8, 17), date(2026, 10, 12), date(2026, 11, 2),
        date(2026, 11, 16), date(2026, 12, 8), date(2026, 12, 25),
    }
    assert set(f) == esperados


@pytest.mark.parametrize('compra, cedi, tienda, dias_venta, cobertura', [
    (date(2026, 11, 23), date(2026, 11, 25), date(2026, 11, 26), 3, 1),   # lun → mié → jue
    (date(2026, 11, 24), date(2026, 11, 26), date(2026, 11, 27), 3, 1),   # mar → jue → vie
    (date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 28), 3, 1),   # mié → vie → sáb
    (date(2026, 11, 26), date(2026, 11, 28), date(2026, 11, 29), 3, 2),   # jue → sáb → dom (cubre dom+lun)
    (date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1), 4, 1),    # vie → lun (salta domingo, LT 3)
    (date(2026, 11, 28), date(2026, 12, 1), date(2026, 12, 2), 4, 1),     # sáb → mar (LT 3)
])
def test_semana_normal_sigue_la_tabla_acordada(compra, cedi, tienda, dias_venta, cobertura):
    plan = CalendarioCompras().plan(compra)
    assert plan.llegada_cedi == cedi
    assert plan.llegada_tienda == tienda
    assert plan.n_dias_hasta_llegada == dias_venta
    assert plan.n_dias_cobertura == cobertura
    assert plan.peso_cobertura == cobertura
    assert plan.capacidad_llegada == 1.0


def test_la_cobertura_de_la_semana_suma_siete_dias():
    cal = CalendarioCompras()
    lunes = date(2026, 11, 23)
    total = sum(cal.plan(lunes + timedelta(days=i)).n_dias_cobertura for i in range(6))
    assert total == 7


def test_domingo_no_es_dia_de_compra_y_se_planea_la_del_lunes():
    plan = CalendarioCompras().plan(date(2026, 11, 22))  # domingo
    assert not plan.es_dia_compra_hoy
    assert plan.fecha_compra == date(2026, 11, 23)
    assert plan.llegada_tienda == date(2026, 11, 26)
    # La venta hasta la llegada se cuenta desde el domingo (fecha de los datos): dom, lun, mar, mié.
    assert plan.n_dias_hasta_llegada == 4


def test_puente_festivo_anticipa_lo_que_no_traen_las_llegadas_al_40():
    """Lunes festivo 16-nov-2026: el CEDI recibe dom 15 y lun 16 al 40 %. La compra del jueves 12
    (llega sáb 14, completa) cubre dom 15 y además el 60 % de lo que debían traer las del viernes
    (cubre lun 16) y el sábado (cubre mar 17 y mié 18): 1 + 0,6 + 1,2 = 2,8 días."""
    cal = CalendarioCompras()
    jue = cal.plan(date(2026, 11, 12))
    assert jue.llegada_cedi == date(2026, 11, 14)
    assert jue.dias_cobertura == (date(2026, 11, 15),)
    assert jue.peso_cobertura == pytest.approx(2.8)
    assert len(jue.anticipaciones) == 2

    vie = cal.plan(date(2026, 11, 13))
    assert vie.llegada_cedi == date(2026, 11, 15)        # el domingo de puente sí recibe
    assert vie.capacidad_llegada == pytest.approx(0.4)
    assert vie.peso_cobertura == pytest.approx(0.4)      # solo trae su 40 %

    sab = cal.plan(date(2026, 11, 14))
    assert sab.llegada_cedi == date(2026, 11, 16)
    assert sab.peso_cobertura == pytest.approx(0.8)      # 40 % de mar 17 + mié 18

    # Lo comprado para dom 15 – mié 18 suma exactamente los 4 días.
    assert jue.peso_cobertura + vie.peso_cobertura + sab.peso_cobertura == pytest.approx(4.0)


def test_capacidad_del_festivo_es_configurable():
    cal = CalendarioCompras(capacidad_festivo=1.0)
    plan = cal.plan(date(2026, 11, 12))
    assert plan.peso_cobertura == pytest.approx(1.0)
    assert plan.anticipaciones == ()


def test_excepcion_sin_recepcion_corre_la_llegada_al_siguiente_dia():
    cal = CalendarioCompras(excepciones={date(2026, 11, 25): {'capacidad': 0.0, 'motivo': 'Inventario'}})
    plan = cal.plan(date(2026, 11, 23))                  # lunes: normalmente llega el miércoles 25
    assert plan.llegada_cedi == date(2026, 11, 26)
    assert cal.motivo(date(2026, 11, 25)) == 'Inventario'


def test_factor_venta_por_fecha_pondera_la_cobertura():
    cal = CalendarioCompras(excepciones={date(2026, 11, 29): {'factor_venta': 1.5}})
    plan = cal.plan(date(2026, 11, 26))                  # jueves: cubre dom 29 y lun 30
    assert plan.peso_cobertura == pytest.approx(2.5)


def test_factor_por_dia_de_semana():
    cal = CalendarioCompras(factor_dia_semana={6: 1.3})  # domingos +30 %
    plan = cal.plan(date(2026, 11, 26))
    assert plan.peso_cobertura == pytest.approx(2.3)


def test_sin_venta_de_hoy_se_cuenta_un_dia_menos():
    plan = CalendarioCompras(incluye_venta_hoy=False).plan(date(2026, 11, 19))
    assert plan.n_dias_hasta_llegada == 2


def test_lead_time_por_dia_de_compra():
    cal = CalendarioCompras(lt_por_dia={d: 3 if d == 0 else 2 for d in range(7)})
    assert cal.llegada_cedi(date(2026, 11, 23)) == date(2026, 11, 26)   # lunes con LT 3 → jueves


def test_calendario_sin_recepcion_lanza_error_claro():
    cal = CalendarioCompras(dias_sin_recepcion=frozenset(range(7)), capacidad_festivo=0)
    with pytest.raises(ValueError, match='recepción'):
        cal.plan(date(2026, 11, 19))
