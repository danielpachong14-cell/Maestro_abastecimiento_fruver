"""Distribuidor Fruver (UI web): `streamlit run app.py`. Una corrida = distribución + compra; los resultados
viven en `st.session_state` para que descargar un archivo no borre el otro botón.
"""

import streamlit as st
from datetime import date

from core.algorithm import run_distribution
from core.compras import ejecutar_compra
from core.compras_exporter import generate_purchase_excel, resumen_plan
from core.compras_params import ParametrosCompra
from core.exporter import generate_excel, resumen_renovacion_texto
from core.loader import load_inputs
from core.maestros import ARCHIVO as ARCHIVO_MAESTROS
from core.preprocessor import build_distribution_df

st.set_page_config(page_title="Distribuidor Fruver", page_icon="🥬", layout="wide")
st.title("Distribuidor Fruver — Isimo")
st.caption("Distribuye el inventario CEDI a las tiendas activas y calcula la compra al proveedor. "
           f"Sube los 3 archivos del día, el libro «{ARCHIVO_MAESTROS}» y las OC en tránsito.")

_DEF = ParametrosCompra()

with st.form("upload_form"):
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Archivos del día**")
        stock_file      = st.file_uploader("Stock CEDI (Siesa)", type=["xlsx", "xls"])
        celes_file      = st.file_uploader("Celes (consumos)", type=["xlsx", "xls"])
        inventario_file = st.file_uploader("Inventario Tiendas (Siesa)", type=["xlsx", "xls"])
    with col2:
        st.markdown("**Maestros y compras**")
        maestros_file   = st.file_uploader(f"{ARCHIVO_MAESTROS} (portafolio, tiendas, parámetros…)",
                                           type=["xlsx", "xls"])
        oc_file         = st.file_uploader("Compras en tránsito — OC de Siesa (opcional)", type=["xlsx", "xls"])

    with st.expander("Ajustar parámetros de compra en pantalla"):
        usar_pantalla = st.checkbox("Usar estos valores (reemplazan los de la hoja «Parametros» de los maestros)",
                                    value=False)
        p1, p2, p3 = st.columns(3)
        with p1:
            crecimiento = st.number_input("Crecimiento de venta (%)", -90.0, 500.0,
                                          _DEF.crecimiento_venta * 100, step=1.0)
            min_default = st.number_input("MIN de exhibición por defecto (cajas)", 0.0, 50.0,
                                          _DEF.min_exhibicion_default, step=0.5)
            umbral = st.number_input("Umbral de redondeo (fracción de caja)", 0.05, 1.0,
                                     _DEF.umbral_redondeo, step=0.05)
        with p2:
            alfa_sube = st.number_input("Suavizado al subir (α↑)", 0.0, 1.0, _DEF.alfa_subida, step=0.1)
            alfa_baja = st.number_input("Suavizado al bajar (α↓)", 0.0, 1.0, _DEF.alfa_bajada, step=0.1)
            capacidad = st.number_input("Recepción en festivos (%)", 0.0, 100.0,
                                        _DEF.capacidad_festivo * 100, step=5.0)
        with p3:
            lt_tienda = st.number_input("Días CEDI → tienda", 0, 10, _DEF.lt_cedi_tienda)
            exhibir_sin_venta = st.checkbox("Exhibir también sin venta", _DEF.exhibir_sin_venta)
            segun_vida_util = st.checkbox("Inventario según vida útil", _DEF.inventario_segun_vida_util,
                                          help="Solo cuenta lo que se alcanza a vender antes de vencer")
            descontar_vencido = st.checkbox("Descontar inventario vencido", _DEF.descontar_inventario_vencido)
            incluir_sin_pedido = st.checkbox("Incluir tiendas sin pedido", _DEF.incluir_tiendas_sin_pedido)

    submitted = st.form_submit_button("Ejecutar distribución y compra", type="primary", use_container_width=True)

if submitted:
    archivos = {
        "Stock": stock_file,
        "Celes": celes_file,
        "Inventario Tiendas": inventario_file,
        ARCHIVO_MAESTROS: maestros_file,
    }
    faltantes = [nombre for nombre, f in archivos.items() if f is None]
    if faltantes:
        st.error(f"Faltan archivos: {', '.join(faltantes)}")
        st.stop()

    st.session_state.pop("resultado", None)
    with st.spinner("Procesando distribución..."):
        try:
            dfs, extra, alertas_maestros = load_inputs(stock_file, celes_file, inventario_file, maestros_file,
                                                       compras_transito=oc_file)
            df_merged, alertas_preproc = build_distribution_df(dfs)
            df_output, alertas = run_distribution(df_merged, alertas_extra=alertas_preproc)
            excel_bytes = generate_excel(df_output, alertas, df_merged)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(
                "Ocurrió un error inesperado procesando los archivos. Revisa que los "
                "Excel tengan el formato esperado (hojas y columnas) e inténtalo de nuevo."
            )
            with st.expander("Detalle técnico"):
                st.exception(e)
            st.stop()

    # La compra va aparte: si falla, la distribución igual queda disponible.
    compra = {"error": None}
    with st.spinner("Calculando compra..."):
        try:
            overrides = None
            if usar_pantalla:
                overrides = {
                    "crecimiento_venta": crecimiento / 100, "min_exhibicion_default": min_default,
                    "umbral_redondeo": umbral, "alfa_subida": alfa_sube, "alfa_bajada": alfa_baja,
                    "capacidad_festivo": capacidad / 100, "lt_cedi_tienda": int(lt_tienda),
                    "exhibir_sin_venta": exhibir_sin_venta, "descontar_inventario_vencido": descontar_vencido,
                    "inventario_segun_vida_util": segun_vida_util,
                    "incluir_tiendas_sin_pedido": incluir_sin_pedido,
                }
            res = ejecutar_compra(dfs, df_output, hojas_parametros=extra.get("parametros_compra"),
                                  compras_transito=extra.get("compras_transito"), overrides=overrides,
                                  alertas_maestros=alertas_maestros)
            compra.update(bytes=generate_purchase_excel(res), total=res.total_cajas, valor=res.valor_total,
                          plan=resumen_plan(res.plan), alertas=res.alertas,
                          venta=float(res.df_item["venta_esperada"].sum()) if len(res.df_item) else 0.0,
                          cobertura=res.plan.n_dias_cobertura, llega=res.plan.llegada_cedi)
        except ValueError as e:
            compra["error"] = str(e)
        except Exception as e:  # noqa: BLE001 — se muestra en pantalla, no se pierde
            compra["error"] = f"Error inesperado calculando la compra: {e}"

    st.session_state["resultado"] = {
        "excel": excel_bytes,
        "total_cajas": int(df_output["cajas_asignadas"].sum()) if len(df_output) else 0,
        "items_dist": df_output["item_code"].nunique() if len(df_output) else 0,
        "total_items": df_merged["item_code"].nunique(),
        "alertas": alertas,
        "renov": resumen_renovacion_texto(df_merged, df_output),
        "n_excluidas": len(dfs['excluidas']) if 'excluidas' in dfs else 0,
        "compra": compra,
    }

r = st.session_state.get("resultado")
if r:
    st.subheader("Distribución")
    criticos     = [a for a in r["alertas"] if a["tipo"] in ("CRÍTICO", "ERROR")]
    cajas_no_dist = sum(a.get("cajas_sin_distribuir", 0) for a in criticos)

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Ítems distribuidos", f"{r['items_dist']} / {r['total_items']}")
    col_b.metric("Total cajas asignadas", f"{r['total_cajas']:,}")
    if r["renov"]:
        st.caption(f"Renovación por vida útil (Fase 3) — {r['renov']}")
    col_c.metric("Cajas sin distribuir", cajas_no_dist, delta_color="inverse")
    col_d.metric("Tiendas excluidas", r["n_excluidas"])

    if criticos:
        for a in criticos:
            st.error(f"[{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    advertencias = [a for a in r["alertas"] if a["tipo"] not in ("CRÍTICO", "ERROR")]
    for a in advertencias:
        st.warning(f"[{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    if cajas_no_dist == 0 and not criticos:
        st.success("Distribución completada. Existencia CEDI en 0.")

    hoy = date.today().strftime('%Y%m%d')
    st.download_button(
        label="Descargar distribución",
        data=r["excel"],
        file_name=f"distribucion_fruver_{hoy}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        type="primary",
    )

    st.subheader("Compra al proveedor")
    c = r["compra"]
    if c["error"]:
        st.error(f"La compra no se pudo calcular (la distribución sí): {c['error']}")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Compra sugerida (cajas)", f"{c['total']:,}")
        m2.metric("Valor (COP)", f"${c['valor']:,.0f}")
        m3.metric("Venta esperada en la cobertura", f"{c['venta']:,.0f}")
        m4.metric("Días que cubre", c["cobertura"])
        with st.expander("Plan de la compra (fechas)"):
            for etiqueta, valor in c["plan"]:
                st.write(f"**{etiqueta}:** {valor}")
        if c["alertas"]:
            with st.expander(f"Alertas de compra ({len(c['alertas'])})", expanded=True):
                for a in c["alertas"]:
                    (st.error if a["tipo"] in ("CRÍTICO", "ERROR") else st.warning)(a["mensaje"])
        st.download_button(
            label="Descargar compra",
            data=c["bytes"],
            file_name=f"compra_fruver_{hoy}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )
