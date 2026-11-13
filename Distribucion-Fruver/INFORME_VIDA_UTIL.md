# Informe — Renovación por vida útil: cómo estaba, cómo queda y qué impacto tiene

**Fecha:** 2026-11-13 · **Datos:** `Input/` del 2026-11-12 (47 ítems con stock, 92 tiendas, 659 cajas en CEDI) · **Evidencia:** `Output/INFORME_VIDA_UTIL_20261113.xlsx` (generado por `informe_vida_util.py`).
Todas las cifras comparan **el mismo `Input/`** corrido con y sin la Fase 3, por lo que la diferencia se debe solo a la regla nueva.

---

## 1. Resumen

- **El sistema funciona correctamente.** Se revisó de punta a punta (sección 5): 149 tests, 18 validaciones sobre datos reales, 1.000 pruebas aleatorias, el CLI real, la app web y el instalador. No se encontró ningún error en el motor.
- **El cambio:** si el último ingreso de un ítem a una tienda es más viejo que su vida útil y la tienda aún figura con inventario, ahora recibe **1 caja de renovación**. Antes no recibía nada, porque el sistema contaba ese inventario como si fuera vendible.
- **Impacto hoy** (con los pocos ítems que tienen vida útil): **20 tiendas×ítem reciben su caja de renovación** (18 tiendas distintas, 4 ítems). El total despachado **no cambia** (659 cajas, cero residuo): la regla solo mueve cajas, y las 18 que se mueven salen casi todas de la Fase 5, es decir, del sobre-stock forzado.
- **El alcance está limitado por los datos, no por la regla:** solo 13 de 79 ítems del portafolio tienen vida útil, y apenas **16 % de las cajas del CEDI** de hoy (108 de 659) pertenecen a ítems cubiertos. Al cargar más ítems el efecto crece (sección 7 indica cuáles cargar primero).

---

## 2. Cómo estaba antes

El motor decide cuánto necesita cada tienda con el inventario que reporta Celes:

```
días de cobertura = inventario (existencias + tránsito) / consumo diario
```

El sistema **no sabe cuándo entró ese inventario**. Una tienda con 1 caja que entró hace 33 días se ve igual que una con 1 caja que entró ayer: ambas "tienen cobertura". Si el producto ya venció y no se ha dado de baja, la tienda queda **"cubierta" en el sistema pero sin producto vendible**, y nunca se repone mientras el registro siga ahí.

### Ejemplo: Papa 2000 g en USME PUEBLO (S79)

| Dato | Valor |
|---|---|
| Vida útil del ítem | 10 días |
| Último ingreso | 2026-10-10 → **33 días** atrás |
| Existencias en el sistema | 1,0 caja |
| Consumo diario | 0,096 cajas/día |
| **Cobertura que ve el sistema** | **10,4 días** (se considera cubierta) |
| **Pedido ANTES** | **0 cajas** |

El producto lleva más de tres veces su vida útil en tienda; muy probablemente ya no es vendible. El sistema no la repone porque cree que tiene 10 días de inventario.

---

## 3. Cómo queda ahora

Se agregó la **Fase 3 — Renovación por vida útil**, entre la cobertura a 3 días (Fase 2) y el reparto del excedente (que pasa a ser Fase 4; el cero residuo pasa a Fase 5).

**Regla.** Se envía 1 caja a una tienda×ítem si:

1. El ítem tiene `VIDA UTIL` en el Portafolio (vacía o 0 = se omite).
2. Los días desde el último ingreso **superan** la vida útil, y la tienda tiene inventario.
3. **No** tiene mercancía en tránsito (el producto en camino ya renueva la góndola).
4. **No** recibió ya cajas en Fase 1 o 2.
5. Ningún SKU espejo del mismo producto tiene ingreso fresco (p. ej. arándanos 157 y 2602).
6. No está excluida (tope `MAX` o sin datos en Celes) y el CEDI tiene stock.

Por decisión del negocio **no se exige venta mínima**: si ya superó la vida útil, se renueva. Solo usa el sobrante que dejan las fases urgentes, así que **no quita cajas a tiendas agotadas ni a la cobertura de 3 días**.

Los días se cuentan contra la **fecha de corte del archivo** `Inventario Tiendas.xlsx` (su ingreso más reciente, hoy 2026-11-12), no contra el reloj del computador: el resultado no cambia por correr otro día con el mismo archivo.

### El mismo ejemplo, ahora (S79, papa 2000 g)

| | ANTES | AHORA |
|---|---|---|
| Pedido | 0 cajas | **1 caja (Fase 3)** |
| Qué pasa | sigue con inventario vencido y sin reposición | recibe producto fresco para exhibir |

### Segundo ejemplo: Cebolla roja 500 g en MONTE BLANCO (S83)

| Dato | Valor |
|---|---|
| Vida útil / último ingreso | 8 días / 2026-10-29 → 14 días |
| Existencias / consumo | 1,42 cajas / 0,058 al día |
| Cobertura que ve el sistema | **24,6 días** |
| Pedido ANTES → AHORA | **0 → 1 caja (Fase 3)** |

Este caso ilustra el costo de no tener umbral de venta: la tienda vende 0,46 caja en toda la vida útil, así que esa caja nueva tiene riesgo de vencer sin venderse (ver sección 6).

### Casos donde NO se envía (y por qué)

| Caso | Tienda / ítem | Motivo |
|---|---|---|
| Tránsito | ISIMO CALATRAVA (SB6) / papa 2000 g — 1 caja en tránsito | La mercancía que viene ya renueva la góndola |
| SKU espejo fresco | SUBA VILLA GLORIA (S03) / arándanos Pure Valley — el 157 entró hace menos de 5 días | Ya hay arándano fresco del mismo producto |
| Sin stock en CEDI | SAN CRISTOBAL SUR (S29) / papa pareja 1000 g — vida útil 10, ingreso hace 17 días | El CEDI solo tiene 8 cajas y la Fase 1 ya las usó con tiendas agotadas |

---

## 4. Impacto medido (antes vs. ahora)

### 4.1 Distribución

| Indicador | ANTES | AHORA |
|---|---|---|
| Cajas distribuidas | 659 | **659** |
| Cero residuo (ítems con diferencia) | 0 | **0** |
| Fase 1 (urgentes) | 329 | 329 |
| Fase 2 (cobertura 3 días) | 0 | 0 |
| **Fase 3 (renovación por vida útil)** | 0 | **20** |
| Fase 4 (excedente voluntario) | 18 | 17 |
| Fase 5 (cero residuo forzado) | 312 | **293** |
| Filas tienda×ítem con pedido | 610 | 612 |
| Tiendas con pedido | 89 | 90 |
| Máximo de cajas por tienda×ítem | 3 | 3 |

La regla **no aumenta lo que sale del CEDI**: 20 cajas llegan a renovaciones y 18 de ellas dejan de ir a la Fase 4/5 (17 filas pierden una caja: 17 de Fase 5 y 1 de Fase 4; las otras 2 renovaciones ya tenían pedido). Es decir, cajas que antes se repartían como sobre-stock forzado ahora van a tiendas con producto vencido.

### 4.2 Las tiendas con producto vencido (95 candidatas)

| Indicador (de 95 tienda×ítem con ingreso vencido y con inventario) | ANTES | AHORA |
|---|---|---|
| Reciben alguna caja en el pedido | 9 | **27** |
| Reciben renovación (Fase 3) | 0 | **20** |
| Quedan sin producto fresco (sin pedido, sin tránsito y sin SKU espejo fresco) | 44 | **26** |

Las 95 candidatas terminan así: **23 renovadas** (20 directas + 3 vía el SKU hermano), 30 con SKU espejo fresco, 13 con tránsito (propio o de un SKU espejo), 6 que ya recibían en Fase 1/2, y **23 sin stock en el CEDI**.
Las 26 que siguen sin producto fresco son, en su mayoría, esas 23 sin stock.

### 4.3 Por ítem

| Ítem | Vida útil | Candidatas | Renovadas | Observación |
|---|---|---|---|---|
| 754 Cebolla roja 500 g | 8 d | 13 | **10** | |
| 231 Papa 2000 g | 10 d | 15 | **6** | 3 sin stock en CEDI |
| 157 Arándanos 125 g | 5 d | 8 | **3** | 4 con SKU espejo fresco |
| 175 Cebolla blanca 500 g | 8 d | 4 | **1** | |
| 2602 Arándanos Pure Valley 125 g | 5 d | 29 | 0 (3 vía el 157) | 26 con el SKU 157 fresco |
| 1868 Papa pareja 1000 g | 10 d | 17 | 0 | **14 sin stock en CEDI** (8 cajas, usadas en Fase 1) |
| 3351 Tomate uvalina | 10 d | 7 | 0 | 4 sin stock en CEDI |
| 3103 Arándanos 500 g | 20 d | 2 | 0 | 2 sin stock en CEDI |

### 4.4 Alertas nuevas

Cada ítem con candidatas sin stock genera una alerta que nombra las tiendas sin cubrir (231, 1868, 3351 y 3103). Hoy suman 23 tiendas. El detalle completo, con el motivo de cada decisión, está en la hoja **«Renovación Vida Útil»** del Excel de salida.

---

## 5. Revisión completa: ¿funciona correctamente?

| Verificación | Resultado |
|---|---|
| Tests automáticos | **149 pasan** (eran 112 al iniciar la auditoría; 37 nuevos cubren datos sucios, la Fase 3 y la hoja nueva) |
| Validaciones sobre datos reales (18) | **18 PASS**: cero residuo, ≤ 3 cajas por tienda×ítem, suma de fases = pedido, total sin cambios, ninguna renovación con tránsito / SKU espejo fresco / fila excluida / tienda que ya recibió en Fase 1-2, máximo 1 caja de renovación por tienda×grupo, determinismo |
| Pruebas aleatorias | **1.000 casos, 0 fallos** (453 renovaciones generadas; grupos espejo, tránsito, `MAX`, ítems sin vida útil, fechas nulas, poco stock) |
| CLI real (`run.py`, en copia aislada) | OK: 47/47 ítems, 20 cajas de Fase 3, 659 cajas, existencia CEDI en 0 |
| App web (Streamlit) | Carga sin errores; incluye el cargador nuevo «Inventario Tiendas (Siesa)» |
| Instalador | Su test pasa |

**Dos falsos positivos durante la revisión, ninguno en el motor:** una de mis pruebas aleatorias generó un grupo espejo con un SKU de 0 cajas en CEDI, algo imposible en producción (el cruce con Stock solo deja ítems con existencia); y un error de índices en mi propio script de informe mostraba "tiene tránsito" en una fila sin tránsito. Ambos se corrigieron en las herramientas de prueba.

**Limitaciones que conviene conocer**
- **Supuesto central:** se asume que inventario con ingreso más viejo que la vida útil "probablemente ya venció". Si la tienda rota el producto por otra vía (por ejemplo, ingresos parciales no registrados) la renovación sería innecesaria. No hay datos de merma real para medirlo.
- **Dependencia de la fecha de corte:** si `Inventario Tiendas.xlsx` es un extracto viejo, los días salen subestimados; el sistema alerta si el archivo tiene más de un día de atraso.
- **Prioridad de stock:** la renovación solo usa el sobrante. Con pocas cajas en el CEDI, las tiendas agotadas pasan primero.
- **Pendiente operativo:** hay que regenerar el `.exe` y el instalador (`Generar_Exe.bat`); el actual no conoce la fase ni pide el archivo nuevo.

---

## 6. Qué costó la regla (riesgo de merma)

Como se decidió **no exigir venta mínima**, 7 de las 20 cajas renovadas van a tiendas que esperan vender **menos de 0,7 caja** dentro de la vida útil, y 4 menos de 0,5: son las de mayor riesgo de vencer sin venderse. Con el umbral anterior (0,7) la regla habría renovado 13 en vez de 20 (Fase 5: 299 en vez de 293).

Además, los días de inventario después del pedido de las tiendas de menor consumo suben ligeramente (p90 de 20,7 a 23,0 días; filas por encima de 10 días: 258 → 262). Es el costo de reponer tiendas que venden poco.

La hoja «Renovación Vida Útil» muestra la venta esperada de cada tienda: las que la repiten sin vender son candidatas a **baja de inventario** o a salir del portafolio de ese ítem.

**Valor que no se elimina:** hay unas **79,6 cajas físicas** en esas 95 tiendas con ingreso vencido. La regla repone producto fresco, pero esas cajas siguen en el sistema hasta que la tienda las dé de baja.

---

## 7. Cobertura de vida útil y qué cargar primero

| | |
|---|---|
| Ítems del portafolio con `VIDA UTIL` | 13 de 79 (16,5 %) |
| Ítems con stock en CEDI hoy con vida útil | 9 de 47 |
| Cajas CEDI cubiertas por la regla | 108 de 659 (**16,4 %**) |
| Tienda×ítem con vida útil | 722 de 3.358 (21,5 %) |

Los ítems **sin** vida útil se omiten: no se les aplica la regla (como se pidió). Para elegir cuáles cargar primero, la hoja **«Prioridad_VidaUtil»** del Excel ordena los 38 ítems sin vida útil por volumen y por cuántas tiendas tienen inventario con ingreso antiguo. Los de mayor oportunidad hoy:

| Ítem | Cajas CEDI | Tiendas con ingreso > 5 d | > 10 d |
|---|---|---|---|
| 2052 Cebolla larga 500 g | 14 | 20 | **13** |
| 2247 Limón 1000 g | 14 | 24 | 8 |
| 1601 Plátano unidad | 30 | 23 | 5 |
| 1074 Aguacate variedad unidad | 14 | 9 | 1 |
| 1797 Ciruela nacional 400 g | 19 | 7 | 0 |

(Conteo de tiendas con inventario cuyo último ingreso supera X días, sin aplicar los demás filtros.)

Los ítems de mayor volumen, como banano (70 cajas), aguacate Hass (56) o tomate chonto (28), tienen una mediana de **0–1 día** desde el último ingreso: llegan casi a diario, así que la regla casi no actuaría aunque se les cargue vida útil.

---

## 8. Reproducir

```
cd Distribucion-Fruver
distribuidor-fruver/venv/Scripts/python.exe informe_vida_util.py      # regenera el Excel de evidencia
cd distribuidor-fruver && PYTHONPATH=. venv/Scripts/python.exe -m pytest tests -q
```

El Excel incluye: *Resumen* (antes/ahora), *Cobertura_VidaUtil*, *Cambios* (fila por fila), *Candidatos*, *Ejemplos*, *Prioridad_VidaUtil*, *Validaciones* y *Fuzz*.


---

## Anexo (2026-11-13) — Tránsito y doble envío

**Pregunta:** si hoy se renueva una caja y mañana el ingreso a la tienda sigue siendo el viejo, ¿se vuelve a enviar?
**Respuesta: no.** La Fase 3 omite toda tienda×grupo con tránsito (> 0), y Celes sí refleja los envíos del día anterior:

| Output | Días atrás | Pedidos que cruzan con hoy | Con tránsito hoy |
|---|---|---|---|
| 2026-11-12 | 1 | 353 | **343 (97,2 %)** |
| 2026-11-11 | 2 | 346 | 140 (40,5 %) |
| 2026-11-10 | 3 | 730 | 235 (32,2 %) |
| 2026-11-09 | 4 | 405 | 121 (29,9 %) |

A más días baja porque la mercancía ya llegó (y entonces el ingreso sí se actualiza). Hoy, 82 candidatas se omiten por tránsito.
El límite es ~3 % de lo pedido ayer que no figuraba en tránsito; el informe lo vigila (hoja `Transito_Doble_Envio`, FAIL si baja de 80 %).
Se descartaron como señales las columnas de Celes `Fecha Esperada de entrada a Tienda`, `Pedido realizado en`, `Cantidad Solicitada con Celes`
y `Última Fecha de Ingreso` (esta última difiere de la de Siesa en todas las candidatas; por decisión del usuario se ignora).
