"""Distribuidor Fruver — UI Streamlit."""

import streamlit as st
from datetime import date

from core.algorithm import run_distribution
from core.exporter import generate_excel
from core.loader import load_files
from core.preprocessor import build_distribution_df

st.set_page_config(page_title="Distribuidor Fruver", page_icon="🥬", layout="wide")
st.title("Distribuidor Fruver — Isimo")
st.caption("Distribuye el inventario CEDI a las tiendas activas. Sube los 6 archivos y descarga el resultado.")

with st.form("upload_form"):
    col1, col2 = st.columns(2)
    with col1:
        stock_file       = st.file_uploader("Stock CEDI (Siesa)", type=["xlsx", "xls"])
        celes_file       = st.file_uploader("Celes (consumos)", type=["xlsx", "xls"])
        portafolio_file  = st.file_uploader("Portafolio Fruver", type=["xlsx", "xls"])
        espejo_file       = st.file_uploader("Productos Espejo", type=["xlsx", "xls"])
    with col2:
        tiendas_file      = st.file_uploader("Base de Tiendas", type=["xlsx", "xls"])
        tiendas_item_file = st.file_uploader("Tiendas × Ítem", type=["xlsx", "xls"])
        excluidas_file    = st.file_uploader("Tiendas sin pedido (opcional)", type=["xlsx", "xls"])

    submitted = st.form_submit_button("Ejecutar distribución", type="primary", use_container_width=True)

if submitted:
    archivos = {
        "Stock": stock_file,
        "Celes": celes_file,
        "Portafolio": portafolio_file,
        "Tiendas": tiendas_file,
        "Tiendas × Ítem": tiendas_item_file,
        "Productos Espejo": espejo_file,
    }
    faltantes = [nombre for nombre, f in archivos.items() if f is None]
    if faltantes:
        st.error(f"Faltan archivos: {', '.join(faltantes)}")
        st.stop()

    with st.spinner("Procesando..."):
        try:
            dfs = load_files(stock_file, celes_file, portafolio_file, tiendas_file, tiendas_item_file, espejo_file, excluidas_file)
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

    total_cajas  = int(df_output["cajas_asignadas"].sum()) if len(df_output) else 0
    items_dist   = df_output["item_code"].nunique() if len(df_output) else 0
    total_items  = df_merged["item_code"].nunique()
    criticos     = [a for a in alertas if a["tipo"] in ("CRÍTICO", "ERROR")]
    cajas_no_dist = sum(a.get("cajas_sin_distribuir", 0) for a in criticos)

    n_excluidas = len(dfs['excluidas']) if 'excluidas' in dfs else 0
    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Ítems distribuidos", f"{items_dist} / {total_items}")
    col_b.metric("Total cajas asignadas", f"{total_cajas:,}")
    col_c.metric("Cajas sin distribuir", cajas_no_dist, delta_color="inverse")
    col_d.metric("Tiendas excluidas", n_excluidas)

    if criticos:
        for a in criticos:
            st.error(f"[{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    advertencias = [a for a in alertas if a["tipo"] not in ("CRÍTICO", "ERROR")]
    for a in advertencias:
        st.warning(f"[{a['tipo']}] Ítem {a['item']}: {a['mensaje']}")

    if cajas_no_dist == 0 and not criticos:
        st.success("Distribución completada. Existencia CEDI en 0.")

    filename = f"distribucion_fruver_{date.today().strftime('%Y%m%d')}.xlsx"
    st.download_button(
        label="Descargar resultado",
        data=excel_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        type="primary",
    )
