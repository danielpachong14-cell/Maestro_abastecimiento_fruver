# Guía del Distribuidor Fruver — de principio a fin

*Escrita para quien usa el programa día a día (no necesita saber de código).*

## ¿Qué hace?
Reparte las cajas que hay en el CEDI entre las tiendas Fruver, en cajas enteras y sin que sobre ninguna, y **calcula la compra al proveedor** para que llegue a tiempo a las tiendas. Usted deja los Excel del día en una carpeta, abre el programa y recibe dos Excel: el pedido de distribución y la compra.

## Paso 1 · Actualizar los archivos (carpeta `Input`)
La carpeta tiene **5 archivos**. Reemplace los del día **con el mismo nombre exacto**; no cambie nombres ni el orden de las columnas.

| Archivo | Qué contiene | ¿Cambia cada día? |
|---|---|---|
| `Stock.xlsx` | Cajas disponibles en el CEDI (Siesa) | **Sí** |
| `Celes.xlsx` | Consumo e inventario por tienda × ítem (incluye el tránsito) | **Sí** |
| `Inventario Tiendas.xlsx` | Fecha del último ingreso de cada ítem a cada tienda (Siesa) | **Sí** |
| `Compras_Transito.xlsx` | Export de Siesa de las **órdenes de compra abiertas** (hoja «OC en tránsito») | **Sí** (sin él la compra puede salir doble) |
| `Maestros Fruver.xlsx` | **Todo lo que se mantiene a mano**, una hoja por tema (abajo) | Cuando cambie |

**Hojas de `Maestros Fruver.xlsx`** (cada una es una tabla de Excel: agregue filas al final de la tabla):

| Hoja | Para qué |
|---|---|
| `Portafolio` | Ítems, costo y **vida útil**. `ESTADO` y `DIA DE COMPRA` se llenan solos desde `STATUS` (no escriba encima). |
| `STATUS` | **Qué ítems se compran y cuándo.** `STATUS` = ACTIVO; `DIA DE COMPRA` = «Todos los dias», días con guion («LUNES-MIERCOLES-VIERNES») o «CADA 15 DIAS». Opcional por ítem: crecimiento de venta (%), múltiplo y compra mínima del proveedor. Un ítem que no esté aquí queda DESCONTINUADO. |
| `Tiendas` | Tiendas activas. |
| `Tiendas x Item` | Qué ítem maneja cada tienda (`ESTADO`), **`MIN` = exhibición en canastillas** (vacío = 1) y `MAX` (se calcula solo desde `BD_MAX`). |
| `BD_MAX` | Máximo por tienda × ítem. |
| `Espejo` | Productos que se reemplazan entre sí. |
| `No generar pedido` | Tiendas que hoy no reciben distribución (la compra sí las cuenta, porque siguen vendiendo). |
| `Parametros`, `Dias de compra`, `Calendario` | Parámetros de la compra: lead time, crecimiento de venta (%), exhibición por defecto, días de compra, festivos. |

> **Importante:** cierre los Excel antes de ejecutar. Stock, Celes e Inventario Tiendas deben ser **del mismo día**. Los archivos sueltos de antes (`DB_Portafolio…`, `DB_Tiendas…`, etc.) quedaron en la carpeta `Respaldo_Input_20261119`: ya no se usan.

## Paso 2 · Ejecutar
- **Programa instalado:** doble clic en el acceso directo **«Distribuidor Fruver»** (la manzana del escritorio). Se abre una ventana negra con el avance. Al terminar, pulse Enter para cerrarla.
- **Primera vez en otra PC:** abra `Instalador_Distribuidor_Fruver.exe`, elija una carpeta y se crea todo (programa, `Input`, `Output` y el acceso directo).

Al final la ventana debe decir `Existencia CEDI : 0 OK`, `Archivo generado: …distribucion…` y, después de «Calculando compra…», `Compra: N cajas …` y `Archivo generado: …compra…`. Si la compra falla, la distribución igual queda guardada.

## Paso 3 · Leer el resultado (carpeta `Output`)
Se crea `distribucion_fruver_AAAAMMDD.xlsx` con 5 hojas:

| Hoja | Para qué sirve |
|---|---|
| **Distribución** | **El pedido.** Una fila por tienda × ítem; la columna `Pedido Final` son las cajas a enviar. |
| **Resumen** | Total de cajas por tienda. |
| **Resumen Ítems** | Cajas por ítem y por fase (F1 urgentes, F2 cobertura, F3 renovación, F4 excedente). |
| **Novedades** | Lo que merece revisión, en una sola lista. Filtre por la columna `Situación` (abajo). |
| **Alertas** | Avisos sobre los datos, con semáforo. |

### Hoja Novedades — las 3 situaciones
| Situación (color) | Qué significa | Qué hacer |
|---|---|---|
| **Sin datos Celes** (gris) | La tienda o el ítem no aparece en Celes: no se sabe si necesita. No se envía (salvo último recurso). | Revisar por qué falta en Celes. |
| **Bloqueada por MAX** (amarillo) | Existencias + tránsito ya igualan o superan su MAX. | Normal; revisar el MAX si parece bajo. |
| **Renovación vida útil** (azul) | El último ingreso es más viejo que la vida útil. El `Detalle` dice si se envió 1 caja o por qué no (tránsito, sin stock en CEDI, ya recibió, …). | Las que no se renovaron por *sin stock* son candidatas a revisar baja de inventario. |

`Cajas Enviadas` muestra lo que sí recibió esa tienda.

### Hoja Alertas — el semáforo
🔴 **Crítico:** hay que resolverlo antes de usar el pedido · 🟠 **Advertencia:** revisar cuando pueda · 🟢 **OK:** «Todo en orden».

## Paso 3b · La compra (`compra_fruver_AAAAMMDD.xlsx`)

La compra de hoy llega al CEDI en 2 días (el domingo no se recibe: la del viernes llega el lunes y la del sábado el martes) y a la tienda al día siguiente. El sistema proyecta cuánto tendrá cada tienda ese día (inventario + tránsito + el pedido de hoy + compras en camino − lo que se vende mientras tanto) y compra lo que falta para cubrir hasta la siguiente compra, sin bajar de la exhibición (`MIN`).

**Solo cuenta lo que todavía se puede vender:** si un producto ya pasó su vida útil (según la fecha de su último ingreso a la tienda) o la pasará antes de que llegue la compra, no cuenta como inventario aunque Celes lo siga mostrando; la compra lo repone. Se puede cambiar con `INVENTARIO_SEGUN_VIDA_UTIL` en la hoja `Parametros`.

| Hoja | Para qué sirve |
|---|---|
| **Compra General** | **La orden al proveedor**, una fila por ítem. `Compra Sugerida` es lo que calcula el sistema; `Compra Final` (amarilla) es la que usted puede ajustar antes de pedir. También muestra el día de compra del ítem, si hoy se compra y el proveedor de su última OC. |
| **Compra por Tienda** | De dónde sale la compra: cuánto llega a tener cada tienda, su MIN y cuánto le toca. La columna `Motivo` dice por qué (Agotada proyectada, Exhibición, Reposición, Cubierta…). |
| Costo Exhibición | Tiendas donde el MIN no se alcanza a vender antes de que venza: la merma que cuesta sostener esa exhibición (cajas y pesos por semana). Sirve para decidir el MIN. |
| Bajas Pendientes | Tiendas que siguen con inventario de un ingreso más viejo que la vida útil: probablemente producto vencido sin dar de baja. Darlo de baja en tienda hace que la compra lo reponga. |
| Sin Pronóstico | Ítems activos que Celes no trae (sin consumo ni inventario): se compran a mano. |
| OC en Tránsito | Cada orden de compra del archivo de Siesa y si cuenta como «en camino»: En tránsito, Atrasada, Recibida según Stock, Vencida sin recibir… Sirve para limpiar OC viejas en Siesa. |
| Parámetros | Fechas de la compra (cuándo llega, qué días cubre) y los valores usados. |
| Alertas | Avisos de la compra (por ejemplo, si falta el archivo de compras en tránsito). |

## Paso 4 · Problemas frecuentes

| Qué ve | Qué significa | Solución |
|---|---|---|
| «No se encontró …» | Falta un archivo o tiene otro nombre | Revise el nombre exacto en `Input` |
| «Error de datos de entrada: falta columna …» | Cambió el nombre de una columna | Devuelva el nombre original (p. ej. `VIDA UTIL EXHIBICION`) |
| Alerta: *Inventario Tiendas parece desactualizado* | Archivo desactualizado: los días de ingreso salen mal | Descargue el archivo de hoy |
| Un ítem nunca aparece en «Renovación vida útil» | Su vida útil está vacía o en 0 en el Portafolio (no genera alerta; simplemente se omite) | Complete `VIDA UTIL EXHIBICION` |
| Alerta: *repetido(s)* o *aparece N veces* | Una tienda o ítem está duplicado | El sistema ya lo corrigió; limpie el archivo de origen |
| `ADVERTENCIA: N cajas sin distribuir` | Hubo un caso que el sistema no pudo ubicar | Mire las alertas críticas de la hoja Alertas |
| Alerta de compra: *Compras_Transito… vacío / no se cargó* | No se sabe qué ya está pedido al proveedor | Descargue de Siesa las OC abiertas en `Compras_Transito.xlsx`; si no, revise la compra antes de pedir (puede salir doble) |
| Alerta de compra: *OC vencidas … cerrarlas en Siesa* | OC con fecha de entrega vieja que nunca entró al Stock | Ciérrela o anúlela en Siesa; el sistema ya no la cuenta |
| Alerta de compra: *OC atrasadas* | La OC debía llegar y el Stock aún no muestra el ingreso | Confirme con el proveedor; el sistema la cuenta como si llegara hoy |
| Un ítem aparece «No se compra hoy» | Su `DIA DE COMPRA` (hoja `STATUS`) no incluye hoy | Normal; la columna `Observación` dice cuándo es la próxima compra |
| «… le faltan hojas» / «hoja … faltan columnas» | Se borró o renombró una hoja o columna de `Maestros Fruver.xlsx` | Devuelva el nombre original de la hoja o columna |
| «Compra NO generada …» | Error en los datos de compra | La distribución sí quedó lista; revise el mensaje (p. ej. una columna de `Compras_Transito.xlsx`) |

## Reglas clave (para tener en la cabeza)
1. **Cajas enteras, nada sobra:** al final el CEDI queda en 0.
2. **Máximo 3 cajas por tienda y por ítem.**
3. **Orden de prioridad:** 1) urgentes → 2) cobertura a 3 días → 3) renovación por vida útil → 4) excedente → 5) cero residuo.
4. **Renovación:** se envía 1 caja cuando los días desde el último ingreso son **mayores** que la vida útil (vida útil 5: con 5 días no, con 6 sí) y no hay mercancía en tránsito.
5. **Mismos archivos, mismo resultado.**

## Glosario
- **CEDI:** centro de distribución (bodega central).
- **Tránsito:** mercancía ya enviada que aún no llega a la tienda.
- **MAX:** inventario máximo permitido a una tienda para un ítem.
- **Vida útil:** días que el producto dura en exhibición.
- **Producto espejo:** ítems equivalentes que se tratan como un solo grupo.
- **Cero residuo:** regla de que no sobren cajas en el CEDI.
- **Celes / Siesa:** sistemas de origen de los datos de consumo e inventario.
- **Fase:** paso del reparto (urgentes, cobertura, renovación, excedente, cero residuo).
- **MIN / exhibición:** canastillas que una tienda debe tener a la vista de un ítem.
- **OC en tránsito:** orden de compra ya pedida al proveedor que aún no llega al CEDI.
- **Cobertura:** días que debe durar la compra de hoy en la tienda, hasta que llegue la siguiente.

*Más detalle: `INFORME_ALGORITMO.md` (cómo decide el sistema).*
