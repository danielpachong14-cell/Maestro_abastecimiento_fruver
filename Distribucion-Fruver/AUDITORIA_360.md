# Auditoría 360 — Distribuidor Fruver

> **Nota de numeración (2026-11-12):** este informe es anterior a la Fase 3 (renovación por vida útil). Aquí «Fase 3» = excedente voluntario y «Fase 4» = cero residuo; hoy son **Fase 4** y **Fase 5**.

**Fecha:** 2026-11-12 · **Alcance:** `Distribucion-Fruver/` (a fondo) + revisión ligera de `Actualizacion-Portafolio/` y del empaquetado `.exe` · **Modo:** solo lectura (no se modificó `core/`, `tests/` ni `Input/`).
**Parámetros auditados (código actual):** `TARGET_DAYS=3` · `TOPE_EXCEDENTE=5` · `MAX_CAJAS_POR_ITEM=3` · `MIN_STOCK_AGOTADO=0.4` · `MIN_STOCK_SAFETY=0.9`.
**Evidencia:** `Output/AUDITORIA_360_20261112.xlsx` (generado por `auditoria_360.py`; hojas citadas como *[Hoja]*). Datos: `Input/` del día (47 ítems, 92 tiendas, 3.358 combinaciones).

---

## 1. Resumen ejecutivo

**Veredicto:** el motor **funciona correctamente como mecánica** — todas las reglas no negociables se cumplen y el cálculo se reproduce de forma independiente al 100 %. El riesgo no está en la aritmética sino en (a) **cómo están diseñadas las reglas frente a la sobre-compra del CEDI** y (b) **robustez ante datos sucios**, que hoy están limpios pero no se protegen.

| Área | Estado | Nota |
|---|---|---|
| Reglas no negociables (cero residuo, cajas enteras, portafolio) | 🟢 | 659 de 659 cajas repartidas, 0 ítems con residuo |
| Exactitud del cálculo | 🟢 | Reconstrucción independiente desde Excel crudos: 100 % de coincidencia en 3.331 filas |
| Determinismo | 🟢 | Dos corridas idénticas |
| Diseño de reglas / resultado de negocio | 🟠 | 47 % de las cajas salen por Fase 4 (sobre-stock forzado) |
| Robustez ante datos | 🟠 | Duplicados y fraccionarios se pierden sin alerta (latente) |
| Casos borde del algoritmo | 🟡 | 2 comportamientos que contradicen la documentación |
| Documentación | 🟡 | Umbrales, nº de tests y hojas desactualizados |
| Tests | 🟢 | 112 pasan; huecos en datos sucios y casos borde |

**Top 5 riesgos**
1. **El CEDI tiene ~2× lo que las tiendas necesitan** (659 cajas vs. 336 de necesidad a 3 días) y la Fase 4 absorbe el exceso: ítems 1210 (70 cajas CEDI, necesidad 5) y 1561 (56 vs. 1). Es decisión de compra, no del algoritmo, pero deja 258 de 610 filas a >10 días de inventario.
2. **Umbrales de prioridad en cajas absolutas, no en días** → 126 filas reciben en Fase 1 teniendo ya ≥3 días de inventario.
3. **Ítems duplicados en Stock se pierden en silencio** (reproducido: 5 cajas desaparecen y el "cero residuo" sigue marcando OK).
4. **Tienda duplicada en DB_Tiendas duplica pedidos** (reproducido: 21 pares tienda×ítem repetidos).
5. **Tiendas×Ítem trae 24 pares duplicados reales**; se conserva el primero sin avisar (si difieren en `MAX`, el resultado depende del orden del archivo).

---

## 1.1 Estado de correcciones (aplicadas el 2026-11-12, tras la auditoría)

| Hallazgo | Resolución |
|---|---|
| H8a Stock repetido | **Corregido:** se suman las cajas y se alerta (decisión del negocio). |
| H8b Stock fraccionario | **Corregido:** alerta con las cajas decimales descartadas. |
| H8c Tienda repetida en DB_Tiendas | **Corregido:** se conserva la primera y se alerta. |
| H8d Pares repetidos en Tiendas×Ítem | **Corregido:** prevalece el `MAX` más restrictivo (>0) y se alerta (decisión del negocio). En los datos de hoy son 24 pares con el mismo `MAX`: sin efecto en el reparto. |
| H6 Reparto equitativo | **Corregido:** llena primero hasta 3 cajas/tienda y solo supera el máximo si es inevitable; el mensaje ahora cuenta las tiendas realmente elegibles. |
| H5 Bloqueada por MAX en nivel 2 | **Sin cambio — es el diseño vigente.** El test `test_bloqueadas_reciben_solo_despues_de_las_libres` exige que la bloqueada reciba cuando las libres ya están en 3 cajas. Se documenta tal cual. |
| H4 Hermano espejo sin stock | **Retirado como defecto:** solo ocurre en un caso sintético. En producción `df_merged` solo contiene ítems con stock (inner join), por lo que nunca existe un hermano con 0 cajas. La limitación real —el inventario en tienda de un hermano **sin stock en CEDI** no cuenta para el grupo— es una decisión de diseño; no se modificó. |
| H7 | No reproducido; descartado. |
| H1–H3 | Decisiones de negocio: se mantienen (umbrales 0,4/0,9 confirmados; cero residuo "como está"). |

Verificación: 119 tests pasan (7 nuevos) y la corrida real con el `Input/` de hoy mantiene 659 cajas, cero residuo y la misma distribución. La tabla de la sección 4.3 describe el comportamiento **previo** a las correcciones.

## 2. Cómo funciona la distribución

### 2.1 Flujo
`loader.py` valida columnas → `preprocessor.py` normaliza claves y arma `df_merged` (una fila por tienda×ítem elegible) → `algorithm.py` reparte por ítem → `exporter.py` escribe 7 hojas. `run.py`/`app.py` solo orquestan.

**Universo de filas (`preprocessor.py`):** `Tiendas×Ítem (ESTADO=ACTIVO)` ⨝ `Stock (>0)` ⨝ Celes (left) ⨝ `DB_Tiendas` (inner) − tiendas excluidas. Claves: tienda sin prefijo `BO` (`:95`), ítem a entero (`:68`), estado exacto `ACTIVO` (`:104`).

### 2.2 Fórmulas
| Concepto | Fórmula | Dónde |
|---|---|---|
| Inventario efectivo | `existencias + tránsito` (clip ≥ 0) | `preprocessor.py:339` |
| Días proyectados | `inv_efectivo / consumo` (∞ si consumo 0) | `:340` |
| Bloqueo MAX | `MAX > 0` y `inv_efectivo >= MAX` | `:365` |
| Excluida | `bloqueado_por_max OR sin_match_celes` | `:387` |
| Métricas de grupo | suma por (tienda, grupo espejo); `share = consumo/consumo_grupo` | `:445-458` |
| Prioridad | grupo: `inv<0.4`→3, `<0.9`→2, `días<3`→1, si no 0 | `algorithm.py:196-233` |
| Necesidad de grupo | `ceil(max(0, 3·consumo_grupo − inv_grupo))`, repartida entre SKUs (piso + Hamilton) | `algorithm.py:236-268` |
| Peso Fase 3 | `consumo / días_actuales` (= consumo²/stock; 0 días → epsilon) | `algorithm.py:510-511` |

### 2.3 Fases (por ítem, hasta agotar `cajas_disponibles_cedi`)
1. **Fase 1 (`:388`)** — rondas con cap 1 y 2. Tiendas ordenadas por prioridad ↓, consumo ↓, código. Cada una recibe `min(cajas_target_grupo, cap)`, mínimo 1 si prioridad ≥ 2.
2. **Fase 2 (`:408`)** — completa hasta 3 días, proporcional a la necesidad restante si el stock no alcanza; acotada a 3 cajas/ítem.
3. **Fase 3 (`:441`, grupal `:679`)** — excedente voluntario por rondas de **1 caja por tienda**, entre tiendas con consumo >0, que tras la caja sigan ≤5 días y <3 cajas, ponderado por `consumo/días`. Nunca supera el tope.
4. **Fase 4 (`:530`)** — red de cero residuo, única que puede superar el tope. Nivel 1: round-robin por menor días respetando 3 cajas. Nivel 2: ignora las 3 cajas y reincorpora excluidas. Si una sola tienda concentraría ≥5 cajas y hay tiendas sin consumo → reparto equitativo (`:573`).

### 2.4 Ejemplo real — ítem 1210 *[Traza_1210]*
CEDI: 70 cajas; necesidad real a 3 días en toda la red: 5. Solo 3 tiendas son prioridad 1 (reciben 5 cajas en Fase 1); Fase 3 coloca 7 y **la Fase 4 coloca 58** repartiendo 1–2 cajas por tienda. Ej.: S06 (consumo 2.23, inv 5.27 = 2.4 días) recibe 3 (2 en F1 + 1 en F3); SB2 (0.6 consumo, 1.97 inv) recibe 2 y queda a 6.6 días.

---

## 3. Reglas de negocio vs. implementación

| Regla | Estado | Evidencia |
|---|---|---|
| Cero residuo por ítem | ✅ Cumple | *[Invariantes]* 0 ítems con diferencia |
| Solo cajas enteras, > 0 | ✅ | min = 1 |
| Elegibilidad solo Tiendas×Ítem ACTIVO ∩ DB_Tiendas − excluidas | ✅ | 0 pares fuera |
| `INACTIVO` no cuenta como activo | ✅ | comparación exacta (`:104-117`) |
| Tope MAX (inv efectivo ≥ MAX bloquea) | ✅ | reconstrucción 100 %; 91 filas bloqueadas |
| Sin datos Celes → no se envía | ✅ | 27 filas; ninguna recibió |
| Excluidas reciben solo forzadas con alerta | ✅ | 0 excluidas con cajas |
| ≤3 cajas por tienda×ítem (salvo último recurso) | ✅ (ver H6) | 0 filas > 3 en datos reales |
| Fase 3 respeta el tope de 5 días | ✅ con matiz | 1 fila SKU sobre 5 d (S96/157): el grupo espejo está bajo el tope — diseño |
| Prioridad vectorizada = fila a fila | ✅ | 0 diferencias |
| Target de grupo espejo sin doble `ceil` | ✅ | 3.175 grupos exactos |
| Sin escritura a disco en `core/` | ✅ | solo `run.py`/botón escriben |

---

## 4. Hallazgos

### 4.1 Decisiones de negocio a revisar

**H1 — Fase 4 distribuye casi la mitad del CEDI · Alto · negocio/compra**
659 cajas vs. necesidad 336 (cobertura 1,96×). Hoy F1=329, F2=0, F3=18, **F4=312 (47 %)**. En 56 corridas históricas con columna Fase 4, la media es **53 %** (rango 15–80 %) *[Historico]*. Fase 2 queda en 0: Fase 1 ya cubre todo lo que ella cubriría. Resultado: 546/610 filas terminan >5 días y 258 >10 días (mediana 9,2 d, p90 20,7 d). Los ítems con mayor F4: 1210 (83 %), 1561 (95 %), 157, 1800, 1797.
*Recomendación:* tratar la columna "Cajas Fase 4" como KPI de compra (la hoja Análisis Comprador ya lo insinúa); evaluar topes de compra por ítem y/o enviar el exceso a tiendas de mayor consumo o a otro canal, en vez de repartir 1–2 cajas a ~90 tiendas.

**H2 — Umbrales AGOTADA/SAFETY en cajas, no en días · Medio · diseño**
Con consumo mediano de 0,12 cj/día, 0,9 cajas ≈ 7 días. 126 filas reciben en Fase 1 con ≥3 días *[KPIs]*. *[Sensibilidad]*: SAFETY 0,9→0,7 baja esas filas de 126 a 65 y la Fase 1 de 329 a 264 cajas (esas cajas pasan a F4; total igual). Un umbral mínimo físico (presentación de la góndola) es razonable, pero conviene confirmarlo con negocio — Celes trae `Capacidad máxima de la tienda` y no se usa.
*Recomendación:* decidir si la regla debe ser "mínimo de exhibición" (cajas) o "cobertura" (días), o combinar ambas con `max(…)`.

**H3 — Datos de entrada que condicionan la regla · Medio**
- Tránsito = 0 en 3.655 de 4.783 filas de Celes (76 %): la fórmula `existencias + tránsito` puede subestimar inventario (riesgo documentado).
- 475 filas con `(=) Inventario Total` mayor que existencias + tránsito: confirma la decisión de no usarlo.

### 4.2 Comportamientos del algoritmo que contradicen su documentación (reproducidos con casos sintéticos *[Casos_Borde]*)

**H5 — Nivel 2 de Fase 4 no deja a las bloqueadas "al final" · Bajo-Medio** (`algorithm.py:642-652`)
Caso: S1 y S2 pueden seguir absorbiendo (nivel 2 ignora el máximo de 3), S3 está bloqueada por MAX. Resultado: `S1=5, S2=4, S3=1` + alerta forzada. La documentación dice que la bloqueada recibe "solo cuando ya no queda ningún otro destino"; el round-robin la mete en la rotación (`order_n2 = order + bloqueadas`, avanzando `i` de a uno). El test existente (`test_bloqueadas_reciben_solo_despues_de_las_libres`) pasa porque cubre otro escenario.
*Recomendación:* primero rotar solo `order`; usar bloqueadas únicamente si `order` está vacío — o actualizar la doc si el comportamiento actual es el deseado.

**H6 — Reparto equitativo ignora el máximo de 3 cajas · Bajo-Medio** (`:573-611`)
Caso: 1 tienda con consumo + 3 sin consumo, CEDI 20 → cada tienda termina con **5 cajas** (tope 3). El nivel 1 de Fase 4 sí lo respeta; esta rama no intenta respetarlo antes de ceder. Además el mensaje dice "entre las N tiendas del portafolio" aunque excluye las bloqueadas.
*Recomendación:* aplicar el cap de 3 en el reparto equitativo y solo superarlo si no hay alternativa (misma filosofía de niveles).

**H4 — Fase 3 individual ignora al hermano espejo sin stock CEDI · Bajo** (`:948-959`)
Si un ítem espejo no tiene stock hoy, el grupo queda de tamaño 1 en la pasada 2 y Fase 3 mide el tope solo con el inventario del SKU. Caso: grupo en 5,5 días (> tope) igual recibe 6 cajas en **Fase 3** y termina a 7,5 días; sin el efecto, habrían salido por Fase 4 (mismo total). Impacto actual: bajo (2 grupos espejo, 4 ítems), pero el etiquetado F3/F4 y el reparto pueden distorsionarse.

**H7 — `_fase3_grupal` corta si una ronda no asigna nada · No reproducido**
Lo sospeché por lectura (`:720`); no logré construir un caso que lo dispare. Se descarta salvo nueva evidencia.

### 4.3 Robustez ante datos (latentes: los datos de hoy están limpios)

| ID | Caso reproducido | Resultado | Sev. |
|---|---|---|---|
| H8a | Ítem repetido en Stock (22 cajas esperadas) | **17 cajas**: `drop_duplicates(keep='first')` (`preprocessor.py:166`) descarta la 2ª fila sin alerta | Alto |
| H8b | Stock 4,9 cajas | 4 cajas; el 0,9 desaparece sin alerta (`:159`) | Bajo |
| H8c | Tienda repetida en DB_Tiendas | **21 pares tienda×ítem duplicados** → pedidos repetidos (`:408`, merge 1:N) | Alto |
| H8d | **Dato real:** Tiendas×Ítem con 24 pares duplicados | se conserva el primero (`:236`) sin alerta; no verifiqué si difieren en `MAX` | Medio |
| H8e | Espejo: ítem en 2 grupos | gana el último (`:434`); hoy 0 casos | Bajo |

*Recomendación:* validar unicidad de clave en `loader`/`preprocessor` y emitir alerta (o error en Stock/DB_Tiendas) en vez de descartar en silencio; sumar cajas de duplicados de Stock, o alertar.

### 4.4 Reporte y operación (por lectura de código, sin reproducir)

- **H9 — Análisis Comprador:** `# Tiendas Activas` y la necesidad cuentan también las filas excluidas (MAX / sin Celes) y calculan por SKU, no por grupo → porcentajes de riesgo diluidos (`exporter.py:360-376`).
- **H10 — `run.py:95-98`:** escribe fuera del `try`; si el Excel del día está abierto en Excel → traceback. Dos corridas el mismo día se sobreescriben sin aviso (el histórico muestra días con una sola corrida).
- **H11 — `MAX` depende del valor cacheado del `XLOOKUP`** (plan B no implementado) y `normalize_store` quita `BO` a cualquier código que empiece así (`:101`). Hoy sin incidencias (MAX vacío: 0).
- **H12 — Instalador (`Exe-Accesos-Directos/instalador.py`)** empaqueta `Input/` real (stock, consumos) dentro del `.exe` y lo copia a la otra PC: revisar confidencialidad y que no quede desactualizado. No ejecuté `test_instalador.py`.
- **H13 — `Actualizacion-Portafolio/`:** genera una tabla larga con `ESTADO` **por ítem**; no produce la columna `MAX` ni el archivo `DB_Tiendas Por itmes y portafolio.xlsx` — ese cruce es manual/externo y no está auditado por código. Sin relación directa con el motor.

### 4.5 Documentación desactualizada
- Umbrales: docs 0,4/0,7 · código 0,4/**0,9** (cambio sin commitear) · commit `06baf75` decía 0,2/0,5. Comentarios del código dicen "unidades" (son cajas).
- `INDICE.md`: "67 tests" y "5 hojas" → hay **112 tests** y 7 hojas. Docstring de `loader.py` dice "5 archivos" (son 6+1).
- `Input/` y `Output/` aparecen en `git status` como modificados: la nota de "no versionados" no coincide con lo rastreado.

---

## 5. Calidad de datos *[Calidad_Datos]*
Limpios: sin ítems duplicados ni negativos/fraccionarios en Stock, 0 duplicados en Celes y DB_Tiendas, UM consistente Celes↔Stock, espejo sin conflictos. Observaciones: 5 ítems con stock y `ESTADO DEL PRODUCTO` no ACTIVO (se distribuyen con alerta); 27 de 3.358 combinaciones sin Celes (0,8 %); 1 tienda de Celes fuera de DB_Tiendas (se descarta correctamente); 68 filas con consumo 0; Tiendas×Ítem trae las 5.231 filas en ACTIVO (el filtro de `INACTIVO` hoy no actúa sobre este archivo).

## 6. Tests
112 pasan (`pytest`, 8,6 s). Cubren bien MAX, sin-Celes, espejo, fases y alertas. **Huecos:** duplicados de claves, stock fraccionario, tienda duplicada, nivel 2 con bloqueadas + libres con espacio (H5), reparto equitativo vs. máximo de 3 (H6), hermano espejo sin stock (H4), y una prueba de regresión end-to-end con datos reales.

## 7. Sensibilidad *[Sensibilidad]* (mismo Input, total siempre 659)
| Escenario | F1 | F3 | F4 | % F4 | Filas >10 d | F1 con ≥3 d |
|---|---|---|---|---|---|---|
| Base (SAFETY 0,9) | 329 | 18 | 312 | 47 % | 258 | 126 |
| SAFETY 0,7 | 264 | 18 | 377 | 57 % | 247 | 65 |
| SAFETY 0,5 | 252 | 18 | 389 | 59 % | 231 | 42 |
| TARGET 4 d | 359 | 4 | 294 | 45 % | 254 | 112 |
| TOPE 7 d | 329 | 123 | 207 | 31 % | 239 | 126 |
| MAX 4 cajas | 329 | 21 | 309 | 47 % | 256 | 126 |

Lectura: ningún parámetro reduce de forma material el sobre-stock — el límite es el volumen del CEDI. Subir `TOPE_EXCEDENTE` mueve cajas de F4 a F3 (reparto "voluntario" más parejo) pero deja más tiendas sobre 5 días.

## 8. Preguntas abiertas para negocio
1. ¿Los umbrales 0,4/0,9 son "mínimo de exhibición" (cajas) o deberían ser cobertura en días? ¿Se debe usar la `Capacidad máxima` de Celes?
2. ¿Qué hacer con el exceso que hoy absorbe la Fase 4: seguir repartiendo a todas las tiendas, limitar por capacidad, o devolverlo a compras?
3. ¿Una tienda bloqueada por MAX debe recibir antes de que las libres superen las 3 cajas (H5)? ¿El reparto equitativo debe respetar las 3 cajas (H6)?
4. ¿Los duplicados de Stock deben sumarse o rechazarse? ¿Qué fila de Tiendas×Ítem debe prevalecer ante pares duplicados con `MAX` distinto?
5. ¿Se confirma 0,9 como valor definitivo de SAFETY para actualizar la documentación?

## 9. Reproducir
```
cd Distribucion-Fruver
distribuidor-fruver/venv/Scripts/python.exe auditoria_360.py     # regenera el Excel de evidencia
cd distribuidor-fruver && PYTHONPATH=. venv/Scripts/python.exe -m pytest tests/ -q
```
**Limitaciones:** no ejecuté el `.exe`/instalador ni la app Streamlit; H9–H13 son por lectura de código; H8d no verifica si los duplicados difieren en `MAX`; la sensibilidad usa un solo día de datos.
