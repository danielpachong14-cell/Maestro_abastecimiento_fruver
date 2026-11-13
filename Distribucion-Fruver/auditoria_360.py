"""Auditoría 360 del Distribuidor Fruver — genera el Excel de evidencia.

Solo LEE Input/ y Output/ y escribe únicamente Output/AUDITORIA_360_YYYYMMDD.xlsx.
No modifica core/, tests/ ni Input/.

Hojas: KPIs · Invariantes · Reconstruccion · Traza_1210 · Traza_1561 ·
Calidad_Datos · Casos_Borde · Sensibilidad · Historico

Uso:
    cd Distribucion-Fruver
    distribuidor-fruver/venv/Scripts/python.exe auditoria_360.py
"""

import glob
import math
import re
import sys
import warnings
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
INPUT = ROOT / "Input"
OUTPUT = ROOT / "Output"
sys.path.insert(0, str(ROOT / "distribuidor-fruver"))

import core.algorithm as alg                                   # noqa: E402
from core.loader import load_files                             # noqa: E402
from core.preprocessor import (                                # noqa: E402
    _is_active, _normalize_item_series, build_distribution_df, normalize_store,
)

FILES = {
    "stock": INPUT / "Stock.xlsx",
    "celes": INPUT / "Celes.xlsx",
    "portafolio": INPUT / "DB_Portafolio Fruver.xlsx",
    "tiendas": INPUT / "DB_Tiendas.xlsx",
    "tiendas_item": INPUT / "DB_Tiendas Por itmes y portafolio.xlsx",
    "espejo": INPUT / "DB_ProductosEspejo.xlsx",
}
EXCL = INPUT / "Tiendas_No generar pedido.xlsx"


def cargar():
    with ExitStack() as st:
        fs = [st.enter_context(open(p, "rb")) for p in FILES.values()]
        ex = st.enter_context(open(EXCL, "rb")) if EXCL.exists() else None
        inv = st.enter_context(open(INPUT / "Inventario Tiendas.xlsx", "rb"))
        return load_files(*fs, tiendas_excluidas=ex, inventario_tiendas=inv)


def pipeline(dfs):
    dm, al = build_distribution_df(dfs)
    out, al2 = alg.run_distribution(dm, al)
    return dm, out, al2


def dias_post(out):
    return (out["inventario_efectivo"] + out["cajas_asignadas"]) / out["consumo_diario"].where(out["consumo_diario"] > 0)


# ───────────────────────────── 1. KPIs ─────────────────────────────
def hoja_kpis(dm, out, alertas):
    d = dias_post(out)
    cedi = dm.groupby("item_code")["cajas_disponibles_cedi"].first()
    need = np.where(dm["consumo_diario"] > 0,
                    np.ceil((alg.TARGET_DAYS * dm["consumo_diario"] - dm["inventario_efectivo"]).clip(lower=0)), 0)
    need_item = pd.Series(need, index=dm.index).groupby(dm["item_code"]).sum()
    tot = int(out["cajas_asignadas"].sum())
    f = {k: int(out[k].sum()) for k in ["cajas_fase1", "cajas_fase2", "cajas_fase3", "cajas_fase4", "cajas_fase5"]}
    rows = [
        ("Parámetros", "TARGET_DAYS / TOPE_EXCEDENTE / MAX_CAJAS / AGOTADO / SAFETY",
         f"{alg.TARGET_DAYS} / {alg.TOPE_EXCEDENTE} / {alg.MAX_CAJAS_POR_ITEM} / {alg.MIN_STOCK_AGOTADO} / {alg.MIN_STOCK_SAFETY}"),
        ("Universo", "Ítems con stock", dm["item_code"].nunique()),
        ("Universo", "Tiendas", dm["store_code"].nunique()),
        ("Universo", "Combinaciones tienda×ítem", len(dm)),
        ("Universo", "Excluidas del reparto (total)", int(dm["excluida_del_reparto"].sum())),
        ("Universo", "  por MAX", int(dm["bloqueado_por_max"].sum())),
        ("Universo", "  sin datos Celes", int(dm["sin_match_celes"].sum())),
        ("Cajas", "Cajas CEDI (suma por ítem)", int(cedi.sum())),
        ("Cajas", "Cajas distribuidas", tot),
        ("Cajas", "Necesidad a TARGET_DAYS (suma por SKU, ceil)", int(need_item.sum())),
        ("Cajas", "Cobertura CEDI / necesidad", round(cedi.sum() / max(need_item.sum(), 1), 2)),
        *[("Fases", k, v) for k, v in f.items()],
        ("Fases", "% Fase 5 sobre total", round(f["cajas_fase5"] / tot * 100, 1)),
        ("Filas", "Filas con pedido", len(out)),
        ("Días post-pedido", "p50", round(float(d.median()), 1)),
        ("Días post-pedido", "p90", round(float(d.quantile(.9)), 1)),
        ("Días post-pedido", "Filas > TOPE_EXCEDENTE", int((d > alg.TOPE_EXCEDENTE).sum())),
        ("Días post-pedido", "Filas > 10 días", int((d > 10).sum())),
        ("Filas", "Reciben con consumo = 0", int((out["consumo_diario"] == 0).sum())),
        ("Filas", "Reciben Fase 1 con días_proy >= TARGET_DAYS",
         int(((out["cajas_fase1"] > 0) & (out["dias_proyectados"] >= alg.TARGET_DAYS)).sum())),
        ("Alertas", "Total", len(alertas)),
        *[("Alertas", t, n) for t, n in pd.Series([a["tipo"] for a in alertas]).value_counts().items()],
    ]
    top4 = out.groupby("item_code")[["cajas_asignadas", "cajas_fase5"]].sum()
    top4["% Fase 5"] = (top4["cajas_fase5"] / top4["cajas_asignadas"] * 100).round(0)
    top4["Cajas CEDI"] = cedi
    top4["Necesidad 3d"] = need_item
    top4 = top4.sort_values("cajas_fase5", ascending=False).reset_index()
    return pd.DataFrame(rows, columns=["Bloque", "Métrica", "Valor"]), top4


# ───────────────────────────── 2. Invariantes ─────────────────────────────
def hoja_invariantes(dfs, dm, out, alertas, out2):
    res = []

    def add(nombre, ok, detalle):
        res.append({"Invariante": nombre, "Resultado": "PASS" if ok else "FAIL", "Detalle": detalle})

    cedi = dm.groupby("item_code")["cajas_disponibles_cedi"].first()
    tot = out.groupby("item_code")["cajas_asignadas"].sum()
    dif = cedi.sub(tot, fill_value=0)
    add("Cero residuo por ítem", (dif == 0).all(), f"ítems con diferencia: {int((dif != 0).sum())}")
    add("Pedido Final entero y > 0", (out["cajas_asignadas"] > 0).all() and
        (out["cajas_asignadas"] == out["cajas_asignadas"].astype(int)).all(), f"min={out['cajas_asignadas'].min()}")
    suma = out[["cajas_fase1", "cajas_fase2", "cajas_fase3", "cajas_fase4", "cajas_fase5"]].sum(axis=1)
    add("F1+F2+F3+F4+F5 = total por fila", (suma == out["cajas_asignadas"]).all(), f"filas distintas: {int((suma != out['cajas_asignadas']).sum())}")

    forz = {(str(a["store_code"]), a["item"]) for a in alertas if "causa_forzado" in a}
    sobre = out[out["cajas_asignadas"] > alg.MAX_CAJAS_POR_ITEM]
    sobre_sin_alerta = [(r.store_code, r.item_code) for r in sobre.itertuples()
                        if (str(r.store_code), r.item_code) not in forz]
    add("≤ MAX_CAJAS_POR_ITEM (salvo nivel 2 F5)", len(sobre) == 0, f"filas > {alg.MAX_CAJAS_POR_ITEM}: {len(sobre)}")

    ex = out[out["excluida_del_reparto"]]
    ex_sin = [(r.store_code, r.item_code) for r in ex.itertuples() if (str(r.store_code), r.item_code) not in forz]
    add("Excluidas reciben solo con alerta de forzado", len(ex_sin) == 0,
        f"excluidas con cajas: {len(ex)}; sin alerta: {len(ex_sin)}")

    f3 = out[out["cajas_fase4"] > 0]
    d3 = (f3["inventario_efectivo"] + f3["cajas_fase1"] + f3["cajas_fase2"] + f3["cajas_fase4"]) / f3["consumo_diario"].where(f3["consumo_diario"] > 0)
    viol = f3[d3 > alg.TOPE_EXCEDENTE + 1e-9]
    add("Fase 4 nunca deja SKU > TOPE_EXCEDENTE (vista por SKU)", len(viol) == 0,
        f"filas F4 por encima del tope: {len(viol)} (en grupos espejo el tope se mide sobre el grupo)")

    ti = dfs["tiendas_item"].copy()
    ti = ti[_is_active(ti["ESTADO"])]
    ti["k"] = _normalize_item_series(ti["ITEM"])
    elig = set(zip(ti["COD SIESA"].astype(str).str.strip(), ti["k"]))
    tiendas_ok = set(dfs["tiendas"]["COD SIESA"].astype(str).str.strip())
    excl = set(dfs["excluidas"]["Centro Operacional de la Bodega"].astype(str).str.strip()) if "excluidas" in dfs else set()
    fuera = [(r.store_code, r.item_code) for r in out.itertuples()
             if (r.store_code, r.item_code) not in elig or r.store_code not in tiendas_ok or r.store_code in excl]
    add("Elegibilidad ⊆ Tiendas×Ítem ACTIVO ∩ DB_Tiendas − excluidas", len(fuera) == 0, f"pares fuera: {len(fuera)}")

    pr_v = alg._compute_priority(dm)
    pr_r = dm.apply(alg.get_priority, axis=1)
    add("_compute_priority == get_priority", (pr_v == pr_r).all(), f"filas distintas: {int((pr_v != pr_r).sum())}")

    k = ["store_code", "item_code"]
    a = out.sort_values(k).reset_index(drop=True)[k + ["cajas_asignadas"]]
    b = out2.sort_values(k).reset_index(drop=True)[k + ["cajas_asignadas"]]
    add("Determinismo (2 corridas idénticas)", a.equals(b), f"filas {len(a)} vs {len(b)}")

    tg = alg._apportion_grupo_targets(dm)
    ng = (alg.TARGET_DAYS * dm["consumo_diario_grupo"] - dm["inventario_efectivo_grupo"]).clip(lower=0)
    g = pd.DataFrame({"s": dm["store_code"], "g": dm["grupo_id"], "t": tg, "n": ng.apply(math.ceil)})
    gg = g.groupby(["s", "g"]).agg(t=("t", "sum"), n=("n", "first"))
    # El reparto Hamilton cubre `total`, salvo que `room` (=total por ítem) lo limite
    add("Target de grupo espejo == ceil(necesidad_grupo)", (gg["t"] == gg["n"]).all(),
        f"grupos distintos: {int((gg['t'] != gg['n']).sum())} de {len(gg)}")

    dup = dm.duplicated(["store_code", "item_code"]).sum()
    add("Sin pares tienda×ítem duplicados en df_merged", dup == 0, f"duplicados: {int(dup)}")
    return pd.DataFrame(res)


# ───────────────────────────── 3. Reconstrucción independiente ─────────────────────────────
def hoja_reconstruccion(dfs, dm):
    c = dfs["celes"].copy()
    c["store_code"] = c["Código de Bodega"].astype(str).str.strip().str.replace(r"^BO", "", regex=True, case=False)
    c["item_code"] = pd.to_numeric(c["Código de Producto"], errors="coerce")
    ex = pd.to_numeric(c["Inventario Disponible en esta Tienda (Unidades de Distibución)"], errors="coerce").fillna(0).clip(lower=0)
    tr = pd.to_numeric(c["(-) inventario de traslado en proceso"], errors="coerce").fillna(0).clip(lower=0)
    cons = pd.to_numeric(c["Consumo Diario (Unidades de Distribución)"], errors="coerce").fillna(0).clip(lower=0)
    c = pd.DataFrame({"store_code": c["store_code"], "item_code": c["item_code"], "ef_r": ex + tr, "cons_r": cons})
    ti = dfs["tiendas_item"].copy()
    ti["item_code"] = pd.to_numeric(ti["ITEM"], errors="coerce")
    ti["store_code"] = ti["COD SIESA"].astype(str).str.strip()
    ti["max_r"] = pd.to_numeric(ti["MAX"], errors="coerce").fillna(0)
    ti = ti[["store_code", "item_code", "max_r"]].drop_duplicates(["store_code", "item_code"])
    m = dm.merge(c, on=["store_code", "item_code"], how="left").merge(ti, on=["store_code", "item_code"], how="left")
    m = m[~m["sin_match_celes"]]
    m["dias_r"] = np.where(m["cons_r"] > 0, m["ef_r"] / m["cons_r"].replace(0, np.nan), np.inf)
    m["bloq_r"] = (m["max_r"] > 0) & (m["ef_r"] >= m["max_r"])
    m["prio_r"] = np.select([m["ef_r"] < alg.MIN_STOCK_AGOTADO, m["ef_r"] < alg.MIN_STOCK_SAFETY, m["dias_r"] < alg.TARGET_DAYS], [3, 2, 1], 0)
    m["prio_a"] = alg._compute_priority(m.assign(inventario_efectivo_grupo=m["inventario_efectivo_grupo"]))
    esp = m.groupby("grupo_id")["item_code"].nunique()
    solo = m["grupo_id"].map(esp) == 1  # prioridad individual == de grupo solo en ítems sin espejo
    chk = {
        "inventario_efectivo": (np.isclose(m["ef_r"], m["inventario_efectivo"])).mean(),
        "consumo_diario": (np.isclose(m["cons_r"], m["consumo_diario"])).mean(),
        "bloqueado_por_max": (m["bloq_r"] == m["bloqueado_por_max"]).mean(),
        "prioridad (ítems sin espejo)": (m.loc[solo, "prio_r"] == m.loc[solo, "prio_a"]).mean(),
    }
    return pd.DataFrame([{"Campo": k, "% coincidencia": round(v * 100, 3), "Filas comparadas": len(m)} for k, v in chk.items()])


# ───────────────────────────── 4. Traza por ítem ─────────────────────────────
def traza(dm, out, item):
    t = dm[dm["item_code"] == item].copy()
    t["prioridad"] = alg._compute_priority(dm).loc[t.index]
    t["target_grupo"] = alg._apportion_grupo_targets(dm).loc[t.index]
    cols = ["store_code", "cajas_fase1", "cajas_fase2", "cajas_fase3", "cajas_fase4", "cajas_fase5", "cajas_asignadas"]
    t = t.merge(out[["store_code", "item_code"] + cols[1:]], on=["store_code", "item_code"], how="left")
    t[cols[1:]] = t[cols[1:]].fillna(0).astype(int)
    t["dias_antes"] = t["dias_proyectados"].replace(np.inf, np.nan).round(2)
    t["dias_despues"] = ((t["inventario_efectivo"] + t["cajas_asignadas"]) / t["consumo_diario"].where(t["consumo_diario"] > 0)).round(2)
    keep = ["store_code", "zona", "consumo_diario", "inventario_efectivo", "dias_antes", "max_existencias", "excluida_del_reparto",
            "causa_sin_match", "prioridad", "target_grupo"] + cols[1:] + ["dias_despues", "cajas_disponibles_cedi"]
    return t[keep].sort_values("cajas_asignadas", ascending=False).round(3)


# ───────────────────────────── 5. Calidad de datos ─────────────────────────────
def hoja_calidad(dfs, dm):
    s = dfs["stock"]
    s = s[~s["Item"].astype(str).str.lower().str.contains("total", na=False)]
    q = pd.to_numeric(s["Cant. disponible"], errors="coerce")
    sk = _normalize_item_series(s["Item"])
    c = dfs["celes"]
    ti = dfs["tiendas_item"]
    e = dfs["espejo"]
    tiendas = set(dfs["tiendas"]["COD SIESA"].astype(str).str.strip())
    celes_t = set(c["Código de Bodega"].apply(normalize_store))
    um = c.assign(k=pd.to_numeric(c["Código de Producto"], errors="coerce")).groupby("k")["UM"].nunique()
    um_stock = s.assign(k=sk).set_index("k")["U.M."].astype(str)
    um_celes = c.assign(k=pd.to_numeric(c["Código de Producto"], errors="coerce")).groupby("k")["UM"].first().astype(str)
    um_dif = int((um_stock.reindex(um_celes.index.intersection(um_stock.index)) != um_celes.reindex(um_celes.index.intersection(um_stock.index))).sum())
    rows = [
        ("Stock", "Filas (sin totalizador)", len(s)),
        ("Stock", "Ítems duplicados (se pierde la 2ª fila)", int(sk.duplicated().sum())),
        ("Stock", "Cantidades fraccionarias (floor silencioso)", int(((q % 1) > 0).sum())),
        ("Stock", "Cantidades negativas", int((q < 0).sum())),
        ("Stock", "ESTADO DEL PRODUCTO no ACTIVO", int((~_is_active(s["ESTADO DEL PRODUCTO"])).sum())),
        ("Celes", "Filas", len(c)),
        ("Celes", "Pares tienda×ítem duplicados", int(c.duplicated(["Código de Bodega", "Código de Producto"]).sum())),
        ("Celes", "Tiendas en Celes fuera de DB_Tiendas", len(celes_t - tiendas)),
        ("Celes", "Tiendas de DB_Tiendas ausentes en Celes", len(tiendas - celes_t)),
        ("Celes", "Ítems con >1 UM distinta", int((um > 1).sum())),
        ("Celes", "Ítems con UM Celes ≠ U.M. Stock", um_dif),
        ("Celes", "Filas con inventario total > existencias+tránsito (inflado)",
         int((pd.to_numeric(c["(=) Inventario Total"], errors="coerce") >
              pd.to_numeric(c["Inventario Disponible en esta Tienda (Unidades de Distibución)"], errors="coerce").fillna(0)
              + pd.to_numeric(c["(-) inventario de traslado en proceso"], errors="coerce").fillna(0) + 1e-9).sum())),
        ("Celes", "Filas con tránsito = 0", int((pd.to_numeric(c["(-) inventario de traslado en proceso"], errors="coerce").fillna(0) == 0).sum())),
        ("DB_Tiendas", "COD SIESA duplicados (duplicarían filas)", int(dfs["tiendas"]["COD SIESA"].astype(str).str.strip().duplicated().sum())),
        ("Tiendas×Ítem", "Filas", len(ti)),
        ("Tiendas×Ítem", "Filas ACTIVO", int(_is_active(ti["ESTADO"]).sum())),
        ("Tiendas×Ítem", "Pares duplicados", int(ti.duplicated(["COD SIESA", "ITEM"]).sum())),
        ("Tiendas×Ítem", "MAX > 0", int((pd.to_numeric(ti["MAX"], errors="coerce") > 0).sum())),
        ("Tiendas×Ítem", "MAX vacío/no numérico", int(pd.to_numeric(ti["MAX"], errors="coerce").isna().sum())),
        ("Espejo", "Ítems en más de un grupo", int(e.groupby("item_id")["grupo_id"].nunique().gt(1).sum())),
        ("Espejo", "Grupos / filas", f"{e['grupo_id'].nunique()} / {len(e)}"),
        ("Cobertura Celes", "Combinaciones sin match (de elegibles)", f"{int(dm['sin_match_celes'].sum())} de {len(dm)}"),
        ("Consumo", "Filas con consumo = 0 (con match)", int(((dm["consumo_diario"] == 0) & ~dm["sin_match_celes"]).sum())),
        ("Capacidad", "Celes trae 'Capacidad máxima de la tienda' (no usada)",
         "sí" if any("apacidad" in str(x) for x in c.columns) else "no"),
    ]
    return pd.DataFrame(rows, columns=["Fuente", "Chequeo", "Valor"])


# ───────────────────────────── 6. Casos borde sintéticos ─────────────────────────────
def mk(rows):
    df = pd.DataFrame(rows)
    df = df.assign(
        item_desc=df["item"].astype(str), store_name=df["store"], zona="Z", dias_inventario_actuales=0.0,
        inventario_tienda=df["inv"], inventario_transito=df.get("trans", 0), inventario_total=0.0,
        um="CN", vida_util=0.0, sin_match_celes=df.get("sin_match", False), causa_sin_match="",
        max_existencias=df.get("max", 0.0),
    )
    df = df.rename(columns={"item": "item_code", "store": "store_code", "cons": "consumo_diario", "cedi": "cajas_disponibles_cedi"})
    df["inventario_efectivo"] = df["inventario_tienda"] + df["inventario_transito"]
    df["dias_proyectados"] = np.where(df["consumo_diario"] > 0, df["inventario_efectivo"] / df["consumo_diario"].replace(0, np.nan), np.inf)
    df["bloqueado_por_max"] = (df["max_existencias"] > 0) & (df["inventario_efectivo"] >= df["max_existencias"])
    df["excluida_del_reparto"] = df["bloqueado_por_max"] | df["sin_match_celes"]
    if "grupo" not in df.columns:
        df["grupo"] = None
    df["grupo_id"] = df["grupo"].where(df["grupo"].notna(), "ITEM_" + df["item_code"].astype(str))
    g = df.groupby(["store_code", "grupo_id"])
    df["consumo_diario_grupo"] = g["consumo_diario"].transform("sum")
    df["inventario_efectivo_grupo"] = g["inventario_efectivo"].transform("sum")
    df["dias_proyectados_grupo"] = np.where(df["consumo_diario_grupo"] > 0, df["inventario_efectivo_grupo"] / df["consumo_diario_grupo"].replace(0, np.nan), np.inf)
    n = g["item_code"].transform("size")
    df["item_share_consumo"] = np.where(df["consumo_diario_grupo"] > 0, df["consumo_diario"] / df["consumo_diario_grupo"].replace(0, np.nan), 1.0 / n)
    return df


def casos_borde(dfs):
    res = []

    # H4: espejo cuyo hermano (B) no tiene stock CEDI: la Fase 4 de A ve solo su propio inventario.
    rows = []
    for s in ["S1", "S2", "S3"]:
        rows.append(dict(item=10, store=s, cons=0.5, inv=0.0, cedi=6, grupo="G"))
        rows.append(dict(item=11, store=s, cons=0.5, inv=5.5, cedi=0, grupo="G"))
    o, al = alg.run_distribution(mk(rows))
    o = o.assign(dias_grupo=(o["inventario_efectivo_grupo"] + o["cajas_asignadas"]) / o["consumo_diario_grupo"])
    res.append({"Caso": "H4 espejo con hermano sin stock CEDI", "Esperado": "Tiendas con grupo en 5.5 días (> tope 5) no reciben excedente voluntario (F4); solo F5",
                "Obtenido": f"F4={int(o['cajas_fase4'].sum())}, F5={int(o['cajas_fase5'].sum())}, días_grupo post={o['dias_grupo'].round(1).tolist()}"})

    # H5: nivel 2 de Fase 5 — ¿la excluida recibe mientras otras aún pueden absorber?
    rows = [dict(item=20, store="S1", cons=0.1, inv=0.9, cedi=10), dict(item=20, store="S2", cons=0.1, inv=0.9, cedi=10),
            dict(item=20, store="S3", cons=0.1, inv=1.0, cedi=10, max=1.0)]
    o, al = alg.run_distribution(mk(rows))
    r = o.set_index("store_code")["cajas_asignadas"].to_dict()
    res.append({"Caso": "H5 excluida por MAX vs tiendas que aún pueden recibir (nivel 2 F5)",
                "Esperado": "S3 (bloqueada) solo recibe cuando S1/S2 ya no puedan absorber nada",
                "Obtenido": f"asignación={r}; alertas forzadas={[a['store_code'] for a in al if 'causa_forzado' in a]}"})

    # H6: reparto equitativo ignora MAX_CAJAS_POR_ITEM
    rows = [dict(item=30, store="S1", cons=0.5, inv=0.0, cedi=20)] + \
           [dict(item=30, store=f"S{i}", cons=0.0, inv=5.0, cedi=20) for i in (2, 3, 4)]
    o, al = alg.run_distribution(mk(rows))
    res.append({"Caso": "H6 reparto equitativo (portafolio sin consumo)", "Esperado": f"≤ {alg.MAX_CAJAS_POR_ITEM} cajas por tienda (como el nivel 1 de F5)",
                "Obtenido": f"asignación={o.set_index('store_code')['cajas_asignadas'].to_dict()}; alerta equitativo={any('equitativo' in a['mensaje'] for a in al)}"})

    # H8a: ítem duplicado en Stock
    d = {k: v.copy() for k, v in dfs.items()}
    s = d["stock"]
    primera = s.iloc[[0]].copy()
    item0 = primera.iloc[0]["Item"]
    cant0 = float(primera.iloc[0]["Cant. disponible"])
    dup = primera.copy()
    dup["Cant. disponible"] = 5
    d["stock"] = pd.concat([s, dup], ignore_index=True)
    dm2, al2 = build_distribution_df(d)
    got = int(dm2.loc[dm2["item_code"] == int(float(str(item0))), "cajas_disponibles_cedi"].iloc[0])
    res.append({"Caso": "H8a ítem duplicado en Stock (2ª fila de 5 cajas)", "Esperado": f"{int(cant0) + 5} cajas (o alerta)",
                "Obtenido": f"{got} cajas; alertas de duplicado={any('duplic' in a['mensaje'].lower() for a in al2)}"})

    # H8b: cantidad fraccionaria
    d = {k: v.copy() for k, v in dfs.items()}
    d["stock"] = dfs["stock"].copy()
    d["stock"]["Cant. disponible"] = d["stock"]["Cant. disponible"].astype(float)
    d["stock"].loc[d["stock"].index[0], "Cant. disponible"] = 4.9
    dm3, al3 = build_distribution_df(d)
    got = int(dm3.loc[dm3["item_code"] == int(float(str(d["stock"].iloc[0]["Item"]))), "cajas_disponibles_cedi"].iloc[0])
    res.append({"Caso": "H8b stock fraccionario 4.9", "Esperado": "alerta por 0.9 caja descartada", "Obtenido": f"{got} cajas; alerta={any('fraccion' in a['mensaje'].lower() for a in al3)}"})

    # H8c: tienda duplicada en DB_Tiendas
    d = {k: v.copy() for k, v in dfs.items()}
    d["tiendas"] = pd.concat([d["tiendas"], d["tiendas"].iloc[[0]]], ignore_index=True)
    dm4, _ = build_distribution_df(d)
    res.append({"Caso": "H8c tienda duplicada en DB_Tiendas", "Esperado": "sin pares tienda×ítem duplicados",
                "Obtenido": f"pares duplicados en df_merged = {int(dm4.duplicated(['store_code', 'item_code']).sum())}"})
    return pd.DataFrame(res)


# ───────────────────────────── 7. Sensibilidad ─────────────────────────────
def sensibilidad(dm):
    base = {k: getattr(alg, k) for k in ["MIN_STOCK_SAFETY", "MIN_STOCK_AGOTADO", "TARGET_DAYS", "TOPE_EXCEDENTE", "MAX_CAJAS_POR_ITEM"]}
    esc = [("Base (vigente)", {}), ("SAFETY 0.7", {"MIN_STOCK_SAFETY": 0.7}), ("SAFETY 0.5", {"MIN_STOCK_SAFETY": 0.5}),
           ("TARGET 4 días", {"TARGET_DAYS": 4.0}), ("TOPE 7 días", {"TOPE_EXCEDENTE": 7}), ("MAX cajas 4", {"MAX_CAJAS_POR_ITEM": 4})]
    rows = []
    try:
        for nom, ch in esc:
            for k, v in {**base, **ch}.items():
                setattr(alg, k, v)
            o, _ = alg.run_distribution(dm)
            d = dias_post(o)
            rows.append({"Escenario": nom, "Cajas": int(o["cajas_asignadas"].sum()), "F1": int(o["cajas_fase1"].sum()),
                         "F2": int(o["cajas_fase2"].sum()), "F3": int(o["cajas_fase3"].sum()), "F4": int(o["cajas_fase4"].sum()), "F5": int(o["cajas_fase5"].sum()),
                         "% F5": round(o["cajas_fase5"].sum() / o["cajas_asignadas"].sum() * 100, 1),
                         "Filas con pedido": len(o), "Días post p50": round(float(d.median()), 1), "Días post p90": round(float(d.quantile(.9)), 1),
                         "Filas > 10d": int((d > 10).sum()),
                         "F1 con días_proy>=3": int(((o["cajas_fase1"] > 0) & (o["dias_proyectados"] >= alg.TARGET_DAYS)).sum())})
    finally:
        for k, v in base.items():
            setattr(alg, k, v)
    return pd.DataFrame(rows)


# ───────────────────────────── 8. Histórico ─────────────────────────────
def historico():
    rows = []
    for f in sorted(glob.glob(str(OUTPUT / "distribucion_fruver_*.xlsx"))):
        m = re.search(r"(\d{8})", f)
        try:
            dist = pd.read_excel(f, sheet_name="Distribución", usecols=["Código de Producto", "Pedido Final", "Días Inventario Proyectado"])
            r = {"Fecha": m.group(1), "Cajas": int(dist["Pedido Final"].sum()), "Ítems": dist["Código de Producto"].nunique(),
                 "Filas": len(dist), "Días post p50": round(float(dist["Días Inventario Proyectado"].median()), 1),
                 "Filas > 10d": int((dist["Días Inventario Proyectado"] > 10).sum())}
            try:
                ri = pd.read_excel(f, sheet_name="Resumen Ítems", header=5)
                # Los Excel anteriores al 2026-11-12 usan la numeración vieja de 4 fases
                # (Fase 3 = excedente, Fase 4 = cero residuo); los nuevos, la de 5.
                if "Cajas Fase 5" in ri.columns:
                    r["F3"] = int(ri["Cajas Fase 3"].sum())
                    r["F4"] = int(ri["Cajas Fase 4"].sum())
                    r["F5"] = int(ri["Cajas Fase 5"].sum())
                elif "Cajas Fase 4" in ri.columns:
                    r["F4"] = int(ri["Cajas Fase 3"].sum())
                    r["F5"] = int(ri["Cajas Fase 4"].sum())
                if "F5" in r:
                    r["% F5"] = round(r["F5"] / r["Cajas"] * 100, 1) if r["Cajas"] else None
            except Exception:
                pass
            rows.append(r)
        except Exception as e:
            rows.append({"Fecha": m.group(1) if m else f, "Cajas": f"error: {type(e).__name__}"})
    return pd.DataFrame(rows)


def main():
    print("Cargando y corriendo pipeline (2 veces, determinismo)...")
    dfs = cargar()
    dm, out, alertas = pipeline(dfs)
    _, out2, _ = pipeline(cargar())
    kp, top4 = hoja_kpis(dm, out, alertas)
    inv = hoja_invariantes(dfs, dm, out, alertas, out2)
    print(inv.to_string())
    rec = hoja_reconstruccion(dfs, dm)
    print(rec.to_string())
    cal = hoja_calidad(dfs, dm)
    print("Casos borde...")
    cb = casos_borde(dfs)
    print(cb.to_string())
    print("Sensibilidad...")
    sens = sensibilidad(dm)
    print(sens.to_string())
    print("Histórico...")
    hist = historico()

    alert_df = pd.DataFrame([{k: v for k, v in a.items() if k in ("tipo", "item", "mensaje")} for a in alertas])
    dest = OUTPUT / f"AUDITORIA_360_{date.today():%Y%m%d}.xlsx"
    with pd.ExcelWriter(dest, engine="openpyxl") as w:
        kp.to_excel(w, sheet_name="KPIs", index=False)
        top4.to_excel(w, sheet_name="KPIs", index=False, startrow=len(kp) + 3)
        inv.to_excel(w, sheet_name="Invariantes", index=False)
        rec.to_excel(w, sheet_name="Reconstruccion", index=False)
        traza(dm, out, 1210).to_excel(w, sheet_name="Traza_1210", index=False)
        traza(dm, out, 1561).to_excel(w, sheet_name="Traza_1561", index=False)
        cal.to_excel(w, sheet_name="Calidad_Datos", index=False)
        cb.to_excel(w, sheet_name="Casos_Borde", index=False)
        sens.to_excel(w, sheet_name="Sensibilidad", index=False)
        hist.to_excel(w, sheet_name="Historico", index=False)
        alert_df.to_excel(w, sheet_name="Alertas_Corrida", index=False)
        for ws in w.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 70)
    print("Generado:", dest)


if __name__ == "__main__":
    main()
