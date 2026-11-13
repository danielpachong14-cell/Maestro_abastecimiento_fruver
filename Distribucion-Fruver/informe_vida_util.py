"""Informe «antes vs ahora» de la Fase 3 (renovación por vida útil) + revisión de funcionamiento.

Solo LEE Input/ y escribe únicamente Output/INFORME_VIDA_UTIL_YYYYMMDD.xlsx.

Escenarios (mismos datos de Input/):
  ANTES        — el sistema sin Fase 3 (la renovación se desactiva).
  AHORA        — sistema vigente (UMBRAL_VENTA_RENOVACION = 0: siempre renueva).
  AHORA_0.7    — alternativa informativa: exigir venta esperada >= 0.7 caja.

Hojas: Resumen · Cobertura_VidaUtil · Cambios · Candidatos · Ejemplos ·
       Prioridad_VidaUtil · Validaciones · Transito_Doble_Envio · Fuzz

Uso:
    cd Distribucion-Fruver
    distribuidor-fruver/venv/Scripts/python.exe informe_vida_util.py
"""

import re
import sys
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent
OUTPUT = ROOT / "Output"
sys.path.insert(0, str(ROOT / "distribuidor-fruver"))
sys.path.insert(0, str(ROOT))

import core.algorithm as alg                                     # noqa: E402
import auditoria_360 as a360                                     # noqa: E402  (cargar, pipeline, mk)
from core.exporter import _build_renovacion_sheet                # noqa: E402
from core.preprocessor import _is_active                         # noqa: E402

_ORIG_FASE3 = alg._fase3_renovacion
FASES = ["cajas_fase1", "cajas_fase2", "cajas_fase3", "cajas_fase4", "cajas_fase5"]


def correr(dm, alertas_pre, escenario):
    """Corre el motor en el escenario pedido y restaura el estado original."""
    umbral = alg.UMBRAL_VENTA_RENOVACION
    try:
        if escenario == "ANTES":
            alg._fase3_renovacion = lambda estado, item_codes: []
        elif escenario == "AHORA_0.7":
            alg.UMBRAL_VENTA_RENOVACION = 0.7
        out, al = alg.run_distribution(dm, alertas_pre)
    finally:
        alg._fase3_renovacion = _ORIG_FASE3
        alg.UMBRAL_VENTA_RENOVACION = umbral
    return out, al


def dias_post(out):
    return (out["inventario_efectivo"] + out["cajas_asignadas"]) / out["consumo_diario"].where(out["consumo_diario"] > 0)


def metricas(nombre, out, al, dm):
    d = dias_post(out)
    r = {"Escenario": nombre, "Cajas distribuidas": int(out["cajas_asignadas"].sum()),
         "Filas con pedido": len(out), "Tiendas con pedido": out["store_code"].nunique()}
    for f in FASES:
        r[f.replace("cajas_fase", "Cajas Fase ")] = int(out[f].sum()) if f in out.columns else 0
    r["% Fase 5"] = round(r["Cajas Fase 5"] / r["Cajas distribuidas"] * 100, 1)
    r["Días post p50"] = round(float(d.median()), 1)
    r["Días post p90"] = round(float(d.quantile(0.9)), 1)
    r["Filas > 10 días"] = int((d > 10).sum())
    r["Alertas"] = len(al)
    return r


# ───────────────────────── Candidatos y cobertura ─────────────────────────
def candidatos(dm, out):
    """Todas las tienda×ítem con ingreso más viejo que la vida útil y aún con inventario."""
    base = dm.assign(_tr_g=dm.groupby(["store_code", "grupo_id"])["inventario_transito"].transform("sum"))
    c = base[(base["vida_util_portafolio"] > 0) & (base["dias_desde_ingreso"] > base["vida_util_portafolio"])
             & (base["inventario_tienda"] > 0)].copy()
    cols = ["store_code", "item_code", "cajas_asignadas"] + FASES
    o = out.reindex(columns=cols).groupby(["store_code", "item_code"], as_index=False).sum()
    c = c.merge(o, on=["store_code", "item_code"], how="left")
    for f in ["cajas_asignadas"] + FASES:
        c[f] = c[f].fillna(0)
    return c


def cobertura(dm, dfs):
    p = dfs["portafolio"].copy()
    vu = pd.to_numeric(p["VIDA UTIL EXHIBICION"], errors="coerce")
    items_dm = dm.groupby("item_code").agg(cajas=("cajas_disponibles_cedi", "first"),
                                           vu=("vida_util_portafolio", "first"), tiendas=("store_code", "nunique"))
    con = items_dm["vu"].notna()
    inv = dfs["inventario_tiendas"]
    rows = [
        ("Portafolio", "Ítems en el portafolio", len(p)),
        ("Portafolio", "Ítems con VIDA UTIL EXHIBICION > 0", int((vu > 0).sum())),
        ("Portafolio", "% con vida útil", round((vu > 0).mean() * 100, 1)),
        ("Hoy en CEDI", "Ítems con stock", len(items_dm)),
        ("Hoy en CEDI", "  con vida útil (la Fase 3 puede actuar)", int(con.sum())),
        ("Hoy en CEDI", "  sin vida útil (la Fase 3 los omite)", int((~con).sum())),
        ("Hoy en CEDI", "Cajas CEDI de ítems con vida útil", int(items_dm.loc[con, "cajas"].sum())),
        ("Hoy en CEDI", "Cajas CEDI de ítems sin vida útil", int(items_dm.loc[~con, "cajas"].sum())),
        ("Hoy en CEDI", "% de cajas CEDI cubiertas por la regla", round(items_dm.loc[con, "cajas"].sum() / items_dm["cajas"].sum() * 100, 1)),
        ("Hoy en CEDI", "Combinaciones tienda×ítem totales", len(dm)),
        ("Hoy en CEDI", "  con vida útil", int(dm["vida_util_portafolio"].notna().sum())),
        ("Inventario Tiendas", "Fecha de corte (ingreso más reciente)", f"{dm['fecha_corte_inventario'].iloc[0]:%Y-%m-%d}"),
        ("Inventario Tiendas", "Combinaciones con vida útil y sin fecha de ingreso",
         int((dm["vida_util_portafolio"].notna() & dm["fecha_ultimo_ingreso"].isna()).sum())),
        ("Inventario Tiendas", "Filas del extracto", len(inv)),
    ]
    return pd.DataFrame(rows, columns=["Bloque", "Métrica", "Valor"])


def prioridad_vida_util(dm):
    """Ítems con stock hoy SIN vida útil, ordenados por cuántas tiendas tienen inventario
    viejo: sirve para decidir cuáles cargar primero. La sensibilidad cuenta tiendas con
    inventario>0 cuyo último ingreso supera X días (sin aplicar los demás filtros)."""
    s = dm[dm["vida_util_portafolio"].isna()].copy()
    s["_con_inv"] = s["inventario_tienda"] > 0
    rows = []
    for item, g in s.groupby("item_code"):
        gi = g[g["_con_inv"] & g["dias_desde_ingreso"].notna()]
        rows.append({
            "Código Ítem": item, "Nombre": g["item_desc"].iloc[0], "Cajas CEDI": int(g["cajas_disponibles_cedi"].iloc[0]),
            "Tiendas activas": len(g), "Tiendas con inventario": int(g["_con_inv"].sum()),
            "Mediana días desde último ingreso": round(float(gi["dias_desde_ingreso"].median()), 1) if len(gi) else None,
            "Tiendas con ingreso > 3 d": int((gi["dias_desde_ingreso"] > 3).sum()),
            "Tiendas con ingreso > 5 d": int((gi["dias_desde_ingreso"] > 5).sum()),
            "Tiendas con ingreso > 8 d": int((gi["dias_desde_ingreso"] > 8).sum()),
            "Tiendas con ingreso > 10 d": int((gi["dias_desde_ingreso"] > 10).sum()),
            "Tiendas con ingreso > 15 d": int((gi["dias_desde_ingreso"] > 15).sum()),
            "Inventario en tiendas (cajas)": round(float(g["inventario_tienda"].sum()), 1),
        })
    if not rows:   # todos los ítems con stock ya tienen vida útil: nada que priorizar
        return pd.DataFrame(columns=["Código Ítem", "Nombre", "Cajas CEDI", "Tiendas activas", "Tiendas con inventario"])
    return pd.DataFrame(rows).sort_values(["Cajas CEDI", "Tiendas con inventario"], ascending=False).reset_index(drop=True)


# ───────────────────────── Ejemplos antes/ahora ─────────────────────────
def ejemplos(dm, o_antes, o_ahora, alertas_ahora):
    def fila(out, s, i):
        r = out[(out["store_code"] == s) & (out["item_code"] == i)]
        if len(r) == 0:
            return {f: 0 for f in ["cajas_asignadas"] + FASES}
        return {f: int(r.iloc[0][f]) for f in ["cajas_asignadas"] + FASES}

    c = candidatos(dm, o_ahora)
    c["exceso"] = c["dias_desde_ingreso"] - c["vida_util_portafolio"]
    sin_stock_items = {i for a in alertas_ahora if a.get("combos_sin_renovar") for i in a["items_afectados"]}
    sel = []
    r1 = c[(c["cajas_fase3"] > 0)].sort_values("exceso", ascending=False)
    if len(r1): sel.append(("Renovada", r1.iloc[0]))
    if len(r1) > 1: sel.append(("Renovada", r1[r1["item_code"] != r1.iloc[0]["item_code"]].iloc[0] if (r1["item_code"] != r1.iloc[0]["item_code"]).any() else r1.iloc[1]))
    t = c[(c["inventario_transito"] > 0) & (c["cajas_fase3"] == 0)]
    if len(t): sel.append(("No renovada: tiene tránsito", t.sort_values("consumo_diario", ascending=False).iloc[0]))
    f = c[c["grupo_con_ingreso_fresco"] & (c["cajas_fase3"] == 0)]
    if len(f): sel.append(("No renovada: SKU espejo con ingreso fresco", f.iloc[0]))
    ss = c[c["item_code"].isin(sin_stock_items) & (c["cajas_fase3"] == 0) & (c["_tr_g"] == 0) & ~c["grupo_con_ingreso_fresco"]
           & ((c["cajas_fase1"] + c["cajas_fase2"]) == 0)]
    if len(ss): sel.append(("No renovada: sin stock en CEDI", ss.sort_values("consumo_diario", ascending=False).iloc[0]))
    rows = []
    for caso, r in sel:
        s, i = r["store_code"], r["item_code"]
        a, b = fila(o_antes, s, i), fila(o_ahora, s, i)
        rows.append({
            "Caso": caso, "Tienda": s, "Nombre tienda": r["store_name"], "Ítem": i, "Descripción": r["item_desc"],
            "Vida útil (d)": int(r["vida_util_portafolio"]), "Último ingreso": f"{r['fecha_ultimo_ingreso']:%Y-%m-%d}",
            "Días desde ingreso": int(r["dias_desde_ingreso"]), "Existencias (cj)": round(float(r["inventario_tienda"]), 2),
            "Tránsito (cj)": round(float(r["inventario_transito"]), 2), "Consumo diario": round(float(r["consumo_diario"]), 3),
            "Días de cobertura según sistema": round(float(r["dias_proyectados"]), 1),
            "Venta esperada en vida útil (cj)": round(float(r["venta_esperada_vida_util"]), 2),
            "ANTES: pedido": a["cajas_asignadas"], "ANTES: fase": ", ".join(f"F{k[-1]}={v}" for k, v in a.items() if k != "cajas_asignadas" and v) or "—",
            "AHORA: pedido": b["cajas_asignadas"], "AHORA: fase": ", ".join(f"F{k[-1]}={v}" for k, v in b.items() if k != "cajas_asignadas" and v) or "—",
        })
    return pd.DataFrame(rows)


# ───────────────────────── Cambios fila a fila ─────────────────────────
def cambios(o_antes, o_ahora, dm):
    k = ["store_code", "item_code"]
    a = o_antes[k + ["cajas_asignadas"] + FASES].rename(columns={f: f"A_{f}" for f in ["cajas_asignadas"] + FASES})
    b = o_ahora[k + ["cajas_asignadas"] + FASES].rename(columns={f: f"B_{f}" for f in ["cajas_asignadas"] + FASES})
    m = a.merge(b, on=k, how="outer").fillna(0)
    m["Δ pedido"] = (m["B_cajas_asignadas"] - m["A_cajas_asignadas"]).astype(int)
    m = m[(m["Δ pedido"] != 0) | (m["B_cajas_fase3"] > 0) | (m["A_cajas_fase5"] != m["B_cajas_fase5"]) | (m["A_cajas_fase4"] != m["B_cajas_fase4"])]
    info = dm[k + ["store_name", "item_desc", "vida_util_portafolio", "dias_desde_ingreso", "inventario_tienda"]]
    m = m.merge(info, on=k, how="left")
    m["Tipo de cambio"] = np.select(
        [m["B_cajas_fase3"] > 0, m["Δ pedido"] < 0, m["Δ pedido"] > 0],
        ["Recibe renovación (Fase 3)", "Pierde caja (la cedió a una renovación)", "Gana caja"], "Misma cantidad, otra fase")
    out = m[["store_code", "store_name", "item_code", "item_desc", "Tipo de cambio", "A_cajas_asignadas", "B_cajas_asignadas",
             "Δ pedido", "B_cajas_fase3", "A_cajas_fase4", "B_cajas_fase4", "A_cajas_fase5", "B_cajas_fase5",
             "vida_util_portafolio", "dias_desde_ingreso", "inventario_tienda"]]
    out.columns = ["Tienda", "Nombre tienda", "Ítem", "Descripción", "Tipo de cambio", "Pedido ANTES", "Pedido AHORA", "Δ pedido",
                   "Fase 3 AHORA", "Fase 4 ANTES", "Fase 4 AHORA", "Fase 5 ANTES", "Fase 5 AHORA", "Vida útil", "Días desde ingreso", "Existencias"]
    return out.sort_values(["Tipo de cambio", "Ítem", "Tienda"]).reset_index(drop=True)


# ───────────────────────── Validaciones ─────────────────────────
def validaciones(dfs, dm, o_antes, o_ahora, al_ahora, o_ahora2):
    res = []

    def add(n, ok, det):
        res.append({"Validación": n, "Resultado": "PASS" if ok else "FAIL", "Detalle": det})

    cedi = dm.groupby("item_code")["cajas_disponibles_cedi"].first()
    for nom, o in (("ANTES", o_antes), ("AHORA", o_ahora)):
        dif = cedi.sub(o.groupby("item_code")["cajas_asignadas"].sum(), fill_value=0)
        add(f"[{nom}] Cero residuo por ítem", (dif == 0).all(), f"ítems con diferencia: {int((dif != 0).sum())}")
        add(f"[{nom}] ≤ {alg.MAX_CAJAS_POR_ITEM} cajas por tienda×ítem", (o["cajas_asignadas"] <= alg.MAX_CAJAS_POR_ITEM).all(),
            f"máximo: {int(o['cajas_asignadas'].max())}")
        add(f"[{nom}] Suma de fases = pedido", (o[FASES].sum(axis=1) == o["cajas_asignadas"]).all(), "")
    add("El total despachado no cambia (la fase solo redistribuye)", int(o_antes["cajas_asignadas"].sum()) == int(o_ahora["cajas_asignadas"].sum()),
        f"{int(o_antes['cajas_asignadas'].sum())} vs {int(o_ahora['cajas_asignadas'].sum())}")
    add("Sin Fase 3 en el escenario ANTES", int(o_antes["cajas_fase3"].sum()) == 0, "")

    r = o_ahora[o_ahora["cajas_fase3"] > 0].copy()
    g = dm.groupby(["store_code", "grupo_id"])
    dm2 = dm.assign(_tr_g=g["inventario_transito"].transform("sum"))
    rr = r.merge(dm2[["store_code", "item_code", "_tr_g", "grupo_con_ingreso_fresco"]], on=["store_code", "item_code"], suffixes=("", "_d"))
    add("Cada renovación respeta tránsito=0 en el grupo", (rr["_tr_g"] == 0).all(), f"violaciones: {int((rr['_tr_g'] > 0).sum())}")
    add("Ninguna renovación con SKU espejo fresco", (~rr["grupo_con_ingreso_fresco_d"].astype(bool)).all(), "")
    add("Ninguna renovación a filas excluidas (MAX / sin Celes)", (~r["excluida_del_reparto"].astype(bool)).all(), "")
    f12 = r.groupby(["store_code", "grupo_id"])[["cajas_fase1", "cajas_fase2"]].sum().sum(axis=1)
    add("Ninguna renovación a una tienda que ya recibió en Fase 1/2", (f12 == 0).all(), "")
    por_grupo = r.groupby(["store_code", "grupo_id"])["cajas_fase3"].sum()
    add(f"Máximo {alg.CAJAS_RENOVACION} caja de renovación por tienda×grupo", (por_grupo <= alg.CAJAS_RENOVACION).all(), f"máx: {int(por_grupo.max())}")
    # El SKU hermano que recibe puede no ser el vencido: se valida a nivel de tienda×grupo
    c = dm[(dm["vida_util_portafolio"] > 0) & (dm["dias_desde_ingreso"] > dm["vida_util_portafolio"]) & (dm["inventario_tienda"] > 0)]
    llaves = set(zip(c["store_code"], c["grupo_id"]))
    add("Toda renovación corresponde a un ingreso vencido con inventario", all(k in llaves for k in por_grupo.index), "")
    sin_vu = dm[dm["vida_util_portafolio"].isna()][["store_code", "item_code"]]
    r_sin = r.merge(sin_vu, on=["store_code", "item_code"])
    # Un SKU sin vida útil puede recibir renovación solo si un hermano espejo la tiene; se informa.
    add("Renovaciones en SKU sin vida útil propia (solo vía hermano espejo)", True, f"{len(r_sin)} fila(s)")
    k = ["store_code", "item_code"]
    a = o_ahora.sort_values(k).reset_index(drop=True)[k + ["cajas_asignadas"]]
    b = o_ahora2.sort_values(k).reset_index(drop=True)[k + ["cajas_asignadas"]]
    add("Determinismo (2 corridas AHORA idénticas)", a.equals(b), f"{len(a)} filas")
    inv = dfs["inventario_tiendas"]
    add("Inventario Tiendas: sin fechas futuras", not (pd.to_datetime(inv["Fecha última entrada"], errors="coerce") > pd.Timestamp.today().normalize() + pd.Timedelta(days=1)).any(),
        f"corte {dm['fecha_corte_inventario'].iloc[0]:%Y-%m-%d}")
    n_sin = sum(a_["combos_sin_renovar"] for a_ in al_ahora if a_.get("combos_sin_renovar"))
    add("Candidatas sin stock quedan alertadas", True, f"{n_sin} tienda(s) en alertas")
    return pd.DataFrame(res)


# ───────────────────────── Fuzz ─────────────────────────
def fuzz(n=1000, seed=7):
    """Instancias aleatorias (grupos espejo, tránsito, MAX, fechas, vida útil, poco stock) que
    verifican cero residuo y las reglas de la Fase 3."""
    rng = np.random.default_rng(seed)
    fallos, con_f3 = [], 0
    for it in range(n):
        n_items, n_stores = int(rng.integers(1, 4)), int(rng.integers(2, 8))
        grupo = "G" if n_items > 1 and rng.random() < 0.6 else None
        rows = []
        for i in range(n_items):
            cedi = int(rng.integers(1, 15))   # df_merged solo contiene ítems con stock (inner join con Stock)
            for s in range(n_stores):
                rows.append(dict(item=10 + i, store=f"S{s}", cons=float(rng.choice([0, 0.05, 0.15, 0.4, 1.0])),
                                 inv=float(rng.choice([0, 0.3, 1.0, 1.5, 3.0])), trans=float(rng.choice([0, 0, 0, 1.0])),
                                 cedi=cedi, grupo=grupo, max=float(rng.choice([0, 0, 0, 2.0])),
                                 sin_match=bool(rng.random() < 0.05)))
        df = a360.mk(rows)
        vu_item = {10 + i: rng.choice([np.nan, 5.0, 8.0]) for i in range(n_items)}
        df["vida_util_portafolio"] = df["item_code"].map(vu_item).astype(float)
        df["dias_desde_ingreso"] = rng.integers(0, 16, len(df)).astype(float)
        df.loc[rng.random(len(df)) < 0.1, "dias_desde_ingreso"] = np.nan
        df["venta_esperada_vida_util"] = df["consumo_diario_grupo"] * df["vida_util_portafolio"]
        vug = df.groupby(["store_code", "grupo_id"])["vida_util_portafolio"].transform("max")
        fresco = df["dias_desde_ingreso"].notna() & (df["dias_desde_ingreso"] <= vug)
        df["grupo_con_ingreso_fresco"] = fresco.groupby([df["store_code"], df["grupo_id"]]).transform("any")
        try:
            out, _ = alg.run_distribution(df)
            cedi_item = df.groupby("item_code")["cajas_disponibles_cedi"].first()
            tot = out.groupby("item_code")["cajas_asignadas"].sum() if len(out) else pd.Series(dtype=float)
            if not (cedi_item.sub(tot, fill_value=0) == 0).all():
                fallos.append((it, "residuo")); continue
            if len(out) == 0:
                continue
            if not (out[FASES].sum(axis=1) == out["cajas_asignadas"]).all():
                fallos.append((it, "suma fases")); continue
            f3 = out[out["cajas_fase3"] > 0]
            con_f3 += len(f3)
            gk = f3.groupby(["store_code", "grupo_id"])["cajas_fase3"].sum()
            if (gk > alg.CAJAS_RENOVACION).any():
                fallos.append((it, "más de 1 caja por tienda×grupo")); continue
            trg = df.groupby(["store_code", "grupo_id"])["inventario_transito"].sum()
            f12 = out.groupby(["store_code", "grupo_id"])[["cajas_fase1", "cajas_fase2"]].sum().sum(axis=1)
            for (s, g), _ in gk.items():
                d = df[(df["store_code"] == s) & (df["grupo_id"] == g)]
                viejo = ((d["vida_util_portafolio"] > 0) & (d["dias_desde_ingreso"] > d["vida_util_portafolio"]) & (d["inventario_tienda"] > 0)).any()
                if not viejo or trg[(s, g)] > 0 or f12.get((s, g), 0) > 0 or bool(d["grupo_con_ingreso_fresco"].any()):
                    fallos.append((it, f"F3 indebida en {s}/{g}")); break
            if (f3["excluida_del_reparto"].astype(bool)).any():
                fallos.append((it, "F3 a fila excluida"))
        except Exception as e:  # noqa: BLE001
            fallos.append((it, f"excepción {type(e).__name__}: {e}"))
    return pd.DataFrame([{"Instancias": n, "Fallos": len(fallos), "Filas con Fase 3 generadas": con_f3,
                          "Detalle de fallos": "; ".join(f"#{i}:{m}" for i, m in fallos[:10]) or "—"}])


# ───────────────────────── Tránsito vs. doble envío ─────────────────────────
UMBRAL_TRANSITO_AYER = 80.0   # % mínimo de lo pedido ayer que debe figurar hoy como tránsito en Celes


def transito_doble_envio(dm, hoy=None, max_dias=4):
    """¿Lo que se pidió en corridas anteriores aparece hoy como tránsito en Celes?

    Es la base de la regla «no renovar si ya hay tránsito»: la renovación se repite al día
    siguiente (el ingreso a la tienda sigue siendo el viejo porque la caja aún no llegó) y solo
    el tránsito de Celes evita el doble envío. Lee los Output de los últimos `max_dias` días y
    cruza cada pedido con el tránsito de hoy; los de hace 2+ días bajan porque ya llegaron.
    """
    hoy = pd.Timestamp(hoy or date.today()).normalize()
    base = dm[["store_code", "item_code", "inventario_transito"]]
    filas = []
    for f in sorted(OUTPUT.glob("distribucion_fruver_*.xlsx")):
        m = re.search(r"(\d{8})", f.name)
        if not m:
            continue
        fecha = pd.Timestamp(m.group(1))
        dias = (hoy - fecha).days
        if not 1 <= dias <= max_dias:
            continue
        try:
            d = pd.read_excel(f, sheet_name="Distribución")
        except Exception as e:  # noqa: BLE001  (archivo abierto en Excel, formato viejo…)
            filas.append({"Output": f.name, "Días atrás": dias, "Pedidos": None, "Cruzan con hoy": None,
                          "Con tránsito hoy": None, "% con tránsito": None, "Nota": f"no se pudo leer ({type(e).__name__})"})
            continue
        d = d.rename(columns={"Centro Operacional de la Bodega": "store_code", "Código de Producto": "item_code"})
        x = d[["store_code", "item_code", "Pedido Final"]].merge(base, on=["store_code", "item_code"], how="inner")
        n = len(x)
        con = int((x["inventario_transito"] > 0).sum())
        filas.append({"Output": f.name, "Días atrás": dias, "Pedidos": len(d), "Cruzan con hoy": n,
                      "Con tránsito hoy": con, "% con tránsito": round(con / n * 100, 1) if n else None,
                      "Nota": "ayer: debe ser alto" if dias == 1 else "a más días, parte ya llegó"})
    return pd.DataFrame(filas, columns=["Output", "Días atrás", "Pedidos", "Cruzan con hoy",
                                        "Con tránsito hoy", "% con tránsito", "Nota"])


def validar_transito(tabla):
    """Fila de «Validaciones»: el % de lo pedido ayer que hoy figura en tránsito debe ser >= 80 %."""
    ayer = tabla[(tabla["Días atrás"] == 1) & tabla["% con tránsito"].notna()] if len(tabla) else tabla
    if len(ayer) == 0:
        return {"Validación": "Lo pedido ayer figura hoy en tránsito (evita el doble envío)", "Resultado": "PASS",
                "Detalle": "sin Output de ayer legible: no evaluable"}
    pct = float(ayer.iloc[-1]["% con tránsito"])
    return {"Validación": "Lo pedido ayer figura hoy en tránsito (evita el doble envío)",
            "Resultado": "PASS" if pct >= UMBRAL_TRANSITO_AYER else "FAIL",
            "Detalle": f"{pct:.1f} % de {int(ayer.iloc[-1]['Cruzan con hoy'])} pedidos (mínimo {UMBRAL_TRANSITO_AYER:g} %)"}


def main():
    print("Cargando y preprocesando…")
    dfs = a360.cargar()
    dm, alertas_pre = a360.build_distribution_df(dfs)
    print("Escenarios…")
    o_antes, al_antes = correr(dm, alertas_pre, "ANTES")
    o_ahora, al_ahora = correr(dm, alertas_pre, "AHORA")
    o_ahora2, _ = correr(dm, alertas_pre, "AHORA")
    o_07, al_07 = correr(dm, alertas_pre, "AHORA_0.7")

    resumen = pd.DataFrame([metricas("ANTES (sin Fase 3)", o_antes, al_antes, dm),
                            metricas("AHORA (umbral de venta 0)", o_ahora, al_ahora, dm),
                            metricas("Alternativa (umbral 0.7)", o_07, al_07, dm)])
    print(resumen.T.to_string())

    c_ant, c_aho = candidatos(dm, o_antes), candidatos(dm, o_ahora)
    exc = lambda c: ~c["excluida_del_reparto"].astype(bool)
    cob = pd.DataFrame([
        ("Tienda×ítem con ingreso más viejo que la vida útil y aún con inventario", len(c_aho), len(c_aho)),
        ("  con tránsito en camino (grupo)", int((c_aho["_tr_g"] > 0).sum()), int((c_aho["_tr_g"] > 0).sum())),
        ("  recibían producto en Fase 1/2/4/5 sin renovación (algún pedido)", int((c_ant["cajas_asignadas"] > 0).sum()), int((c_aho["cajas_asignadas"] > 0).sum())),
        ("  recibe renovación en Fase 3", 0, int((c_aho["cajas_fase3"] > 0).sum())),
        ("  quedan SIN producto fresco (sin pedido, sin tránsito, sin SKU espejo fresco)",
         int(((c_ant["cajas_asignadas"] == 0) & (c_ant["_tr_g"] == 0) & ~c_ant["grupo_con_ingreso_fresco"]).sum()),
         int(((c_aho["cajas_asignadas"] == 0) & (c_aho["_tr_g"] == 0) & ~c_aho["grupo_con_ingreso_fresco"]).sum())),
        ("Cajas físicas con ingreso vencido en esas tiendas (existencias)", round(float(c_aho["inventario_tienda"].sum()), 1), round(float(c_aho["inventario_tienda"].sum()), 1)),
    ], columns=["Indicador", "ANTES", "AHORA"])
    print(cob.to_string())

    cob_vu = cobertura(dm, dfs)
    camb = cambios(o_antes, o_ahora, dm)
    ej = ejemplos(dm, o_antes, o_ahora, al_ahora)
    print(ej.T.to_string())
    prio = prioridad_vida_util(dm)
    cand_sheet = _build_renovacion_sheet(dm, o_ahora)
    val = validaciones(dfs, dm, o_antes, o_ahora, al_ahora, o_ahora2)
    tdv = transito_doble_envio(dm)
    print(tdv.to_string())
    val = pd.concat([val, pd.DataFrame([validar_transito(tdv)])], ignore_index=True)
    print(val.to_string())
    fz = fuzz()
    print(fz.to_string())
    print(camb["Tipo de cambio"].value_counts().to_string())

    dest = OUTPUT / f"INFORME_VIDA_UTIL_{date.today():%Y%m%d}.xlsx"
    with pd.ExcelWriter(dest, engine="openpyxl") as w:
        resumen.to_excel(w, sheet_name="Resumen", index=False)
        cob.to_excel(w, sheet_name="Resumen", index=False, startrow=len(resumen) + 3)
        cob_vu.to_excel(w, sheet_name="Cobertura_VidaUtil", index=False)
        camb.to_excel(w, sheet_name="Cambios", index=False)
        cand_sheet.to_excel(w, sheet_name="Candidatos", index=False)
        ej.to_excel(w, sheet_name="Ejemplos", index=False)
        prio.to_excel(w, sheet_name="Prioridad_VidaUtil", index=False)
        val.to_excel(w, sheet_name="Validaciones", index=False)
        tdv.to_excel(w, sheet_name="Transito_Doble_Envio", index=False)
        fz.to_excel(w, sheet_name="Fuzz", index=False)
        for ws in w.book.worksheets:
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 60)
    print("Generado:", dest)


if __name__ == "__main__":
    main()
