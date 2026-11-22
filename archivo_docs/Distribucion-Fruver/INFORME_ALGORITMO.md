# Informe del algoritmo — cómo funciona hoy

*Escrito para: Daniel y el equipo de abastecimiento. Datos de la corrida del 13-nov-2026. Para el paso a paso de uso, ver `GUIA_USUARIO.md`.*

## 1. En una frase

Toma lo que hay en el CEDI y lo reparte, **en cajas completas y sin dejar sobrante**, entre las tiendas Fruver que lo necesitan, según su consumo, su inventario y su portafolio.

Hoy: **45 ítems · 65 tiendas · 705 cajas · 668 líneas de pedido.** Todo el CEDI queda repartido.

## 2. Mapa del flujo

```
 8 ARCHIVOS (Input/)                 PREPARACIÓN                    REPARTO                  RESULTADO
┌────────────────────┐      ┌─────────────────────────┐     ┌──────────────────────┐   ┌──────────────────┐
│ Stock (CEDI)       │      │ • Revisa columnas       │     │ F1 Urgentes          │   │ Excel de 5 hojas │
│ Celes (consumo)    │      │ • Cruza tienda × ítem   │     │ F2 Cobertura 3 días  │   │  1 Distribución  │
│ Portafolio (vida   │ ───► │ • Quita duplicados      │ ──► │ F3 Renovación        │──►│  2 Resumen       │
│   útil, estado)    │      │ • Calcula días desde el │     │ F4 Excedente         │   │  3 Resumen Ítems │
│ Tiendas            │      │   último ingreso        │     │ F5 Cero residuo      │   │  4 Novedades     │
│ Tiendas × Ítem     │      │ • Marca bloqueadas por  │     │                      │   │  5 Alertas       │
│ Productos espejo   │      │   MAX o sin datos Celes │     │ Tope: 3 cajas por    │   └──────────────────┘
│ Inventario Tiendas │      │ • Detecta tránsito      │     │ tienda × ítem        │
│ Tiendas excluidas* │      └─────────────────────────┘     └──────────────────────┘
└────────────────────┘                  * opcional
```

## 3. Las 5 fases, explicadas simple

El sistema reparte **ítem por ítem** y siempre en este orden. Cada fase usa lo que sobró de la anterior.

| Fase | Qué busca | A quién | Hoy |
|---|---|---|---|
| **1 · Urgentes** | Que no se agote | Tiendas con menos de 0,4 caja (agotadas) o menos de 0,9 (stock de seguridad) | 315 cajas |
| **2 · Cobertura 3 días** | Que cada tienda llegue a 3 días de inventario | Tiendas que aún no llegan, de la más urgente a la menos | 0 cajas (la Fase 1 ya lo cubrió) |
| **3 · Renovación por vida útil** | Reemplazar producto que ya superó su vida útil en la tienda | Tienda × ítem cuyo último ingreso fue hace **más** días que la vida útil y aún tiene inventario → **1 caja** | 107 cajas |
| **4 · Excedente** | Repartir lo que sobra sin pasarse | Tiendas que están por debajo de 5 días de inventario | 11 cajas |
| **5 · Cero residuo** | Que no quede nada en el CEDI | Donde mejor quepa; primero hasta 3 cajas por tienda, repartido en partes iguales | 272 cajas |

**Regla de oro:** ninguna tienda recibe más de **3 cajas por ítem**, todas las cajas son enteras y al final el CEDI queda en cero.

### La Fase 3 en detalle (la nueva)

Ejemplo con vida útil de **5 días**:

| Días desde el último ingreso | ¿Se envía? |
|---|---|
| 4 | No |
| 5 | **No** — todavía está dentro de su vida útil, se espera |
| 6 | **Sí**, 1 caja |

Y **no se renueva** cuando:
- ya hay mercancía **en tránsito** hacia esa tienda (evita enviar dos veces al día siguiente);
- la tienda ya recibió cajas en Fase 1 o 2 ese día;
- un producto espejo del mismo grupo tuvo un ingreso reciente;
- la tienda está bloqueada por MAX o sin datos en Celes;
- el ítem no tiene vida útil en el Portafolio (vacía o 0), o no queda stock en el CEDI.

Los días se cuentan contra la **fecha del archivo `Inventario Tiendas`** (hoy 13-nov). Si ese archivo tiene más de un día, aparece una alerta.

## 4. Cómo se protege el sistema

| Riesgo | Protección |
|---|---|
| Enviar dos veces el mismo producto | La Fase 3 omite lo que ya figura en tránsito. Probado con los pedidos reales de los últimos días: el **97 %** de lo pedido ayer aparece hoy en tránsito. |
| Tienda que ya tiene todo su MAX | Queda bloqueada (hoy 73 combinaciones); solo recibe si no hay otro destino para las cajas. |
| Tienda sin datos en Celes | No se sabe si necesita; no se le envía salvo el último recurso (hoy 24 combinaciones). |
| Datos duplicados o raros | Se suman, se corrigen o se avisan en la hoja Alertas (hoy 14 advertencias, ninguna crítica). |
| Resultados distintos cada vez | Mismos archivos → mismo resultado exacto. |

## 5. Resultado de la verificación

| Verificación | Resultado |
|---|---|
| 169 pruebas automáticas | ✅ todas pasan |
| Todo el CEDI repartido (cero residuo) | ✅ 0 cajas sin repartir |
| Ninguna tienda recibe más de 3 cajas por ítem | ✅ máximo observado: 3 |
| Todas las cajas son enteras y las 5 fases suman el total | ✅ |
| Solo reciben tiendas elegibles del portafolio | ✅ 0 fuera de regla |
| Mismo resultado en dos corridas seguidas | ✅ 668 líneas idénticas |
| Límite de la vida útil (5 días no, 6 sí) | ✅ con datos reales: 130 casos justo en el límite → ninguno recibió caja |
| Excluidas solo reciben como último recurso y con alerta | ✅ |

**Detalle del día de hoy en vida útil:** 550 combinaciones tienen el ingreso más viejo que su vida útil. De ellas: **107 se renovaron**; 234 no por falta de stock en el CEDI; 106 porque ya recibieron en Fase 1/2; 82 por tránsito; 21 por estar excluidas.

## 6. Puntos a tener en cuenta (honestos)

1. **El 39 % de las cajas de hoy (272 de 705) salen en la Fase 5.** Significa que el CEDI tenía más producto del que las tiendas necesitan y el sistema lo reparte igual para no dejar sobrante. Ya no se muestra en el Excel (decisión de nov-2026), pero sigue ocurriendo.
2. **Sin stock en el CEDI no se renueva:** 234 de las 550 candidatas hoy. Siguen visibles en la hoja Novedades como «Sin stock en CEDI».
3. **El tránsito depende de Celes:** cerca del 3 % de lo pedido ayer no apareció en tránsito hoy. En ese margen podría repetirse un envío.
4. **Dependemos de que `Inventario Tiendas` sea de hoy.** Si está desactualizado, los días de ingreso salen mal (el sistema avisa).
5. **El `.exe` corre en consola** (sin la pantalla web de Streamlit).

## 7. Compra al proveedor (nueva, 19-nov-2026)

En la misma corrida, después de repartir el CEDI, el sistema calcula **cuánto comprar**: una
compra por tienda y la compra general por ítem (solo ítems **activos** del Portafolio).

```
 HOY (jueves)              +2 días con recepción          +1 día
 ┌─────────────┐           ┌──────────────────┐          ┌──────────────────────────────┐
 │ Se compra   │ ────────► │ Llega al CEDI    │ ───────► │ Llega a la tienda (domingo)  │
 └─────────────┘           │ (sábado)         │          │ y debe durar hasta que llegue│
                           └──────────────────┘          │ la siguiente compra (martes) │
                                                          └──────────────────────────────┘
 Mientras tanto la tienda vende 3 días (jue, vie, sáb) con lo que tiene + tránsito + el pedido de hoy.
```

**Cómo decide, por tienda × ítem:**

1. **Cuánto tendrá el día que llega la compra** = existencias + tránsito + pedido de hoy + compras ya
   pedidas al proveedor − lo que vende mientras tanto (nunca menos de 0). **Solo cuenta lo que llega
   vendible**: lo que pasa su vida útil antes de ese día no cuenta, aunque Celes lo siga mostrando.
2. **Cuánto debe tener** = lo mayor entre la **exhibición** (`MIN`, 1 canastilla si está vacío) y la
   venta de los días que cubre la compra + un stock de seguridad. La parte de venta nunca pasa de
   lo que se alcanza a vender dentro de la vida útil.
3. **Compra** = la diferencia, en cajas enteras (redondea hacia arriba desde media caja).
4. **Compra general** = la suma de las tiendas, suavizada: si las tiendas piden mucho más que la
   venta (huecos de exhibición), se cierra la mitad del hueco hoy y el resto mañana, para que el
   flujo de compra sea estable; nunca se le quita a una tienda que quedaría agotada. Si piden menos
   (hay sobre-stock), se compra solo lo que piden.

**Calendario:** se compra de lunes a sábado. La del viernes llega el lunes y la del sábado el
martes (el domingo no se recibe). La del jueves cubre domingo y lunes. En puentes festivos el CEDI
recibe solo ~40 % el domingo y el lunes: lo que falta se compra antes (en la del jueves).

**Día de compra de cada ítem** (hoja `STATUS` de los maestros): «Todos los dias» sigue el calendario;
con días fijos (fresa: lunes, miércoles y viernes) solo se compra esos días y cada compra dura hasta
la siguiente; «CADA 15 DIAS» (flor de Jamaica) se revisa a diario y compra 15 días de una vez solo
cuando la red ya no alcanza.

**Órdenes de compra en camino:** se leen del export de Siesa. Como Siesa no marca la recepción
(«Cant. entrada» siempre en 0), una OC con fecha pasada se da por recibida si el Stock muestra un
ingreso del ítem desde esa fecha; si no, cuenta como atrasada hasta 3 días y después como vencida
(se pide cerrarla en Siesa).

**Corrida del 19-nov (con las OC reales):** 654 cajas (38 ítems, $26,3 M) para cubrir domingo y
lunes, contra 1.356 cajas de venta esperada (con +18 % de crecimiento). Con el inventario de Celes tal
cual salían 242: el **62 % del inventario de las tiendas** (2.373 de 3.842 cajas) pasa su vida útil
antes de que llegue la compra y Celes lo sigue contando. Hoy es un día bajo porque lo pedido ayer
(1.301 cajas) y la distribución de hoy (1.318) llegan frescos el viernes y el sábado: ayer + hoy ≈
980 cajas por día. El flujo estable que pide la política (venta + exhibición fresca) es ~1.230 por día
de compra.

**Dos alertas para el negocio:**

| Hallazgo | Cifra | Qué decidir |
|---|---|---|
| El MIN de exhibición no se alcanza a vender antes de vencer | 2.572 tienda×ítem · ~2.700 cajas/semana · ~$100 M/semana de merma | Ajustar el MIN por tienda con la base de exhibición (hoja «Costo Exhibición») |
| Inventario con último ingreso más viejo que la vida útil | 1.063 tienda×ítem · 879 cajas · ~$32,6 M | Dar de baja en tienda (hoja «Bajas Pendientes»); mientras figure en Celes, la compra no lo repone |

**Pendientes:** la base de exhibición por tienda y, si existe, el histórico de ventas diarias (para
la diferencia entre días de la semana). Se recomienda correrla 2 semanas en paralelo con la compra
actual antes de usarla.
