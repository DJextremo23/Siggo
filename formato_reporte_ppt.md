# FORMATO REPORTE PPT — REPORTE DE GUARDIA

> Fuente: `0. Reporte PPT prueba rv0.pptx`

## GENERAL

- Tamaño de diapositiva: **13.333 x 7.5 in** (16:9 · 12192000 x 6858000 EMU).
- Diapositivas: **4** (Portada · Contenido · Contenido · Cierre). El número de páginas de contenido puede crecer según los datos.

### Colores

| Uso | Hex |
|-----|-----|
| Fondo oscuro (portada/cierre) | `#102A43` |
| Barra superior / acentos | `#00A6A6` |
| Acento verde (etiquetas) | `#5DD39E` |
| Blanco (títulos) | `#FFFFFF` |
| Gris claro (texto secundario) | `#D8E2EA` |
| Gris apagado (pie de página) | `#64748B` |
| Separador de pie | `#CBD5E1` |
| Tarjeta portada (fondo / borde) | `#173A5E` (88% opacidad) / `#2B567B` (65%) |

### Tipografía

- Títulos: **Aptos Display**.
- Cuerpo: **Aptos**.

---

## TIPOS DE ELEMENTOS

| Tipo | Qué es | Comportamiento |
|------|--------|----------------|
| **CAPTURA** | Texto y diseño fijo (fondo, logos, barras, pie, número de página, capturas de ejemplo del reporte). | NO se mueve. |
| **FOTO** | Fotografía de evidencia (viene del Excel). | Movible y reordenable. |

> Las **capturas** son elementos de texto y diseño que permanecen fijos. Las **fotos** son las imágenes reales de evidencia y sí deben poder moverse/reordenarse.

---

## DIAPOSITIVA 1 — PORTADA

| Elemento | Tipo | Contenido / Formato | Posición (in) | Tamaño (in) |
|----------|------|---------------------|---------------|-------------|
| Fondo | CAPTURA | Imagen de fondo (full bleed) | 1.37, 0.00 | 11.96 x 7.50 |
| Logo | CAPTURA | Logo corporativo (arriba izquierda) | 0.56, 0.49 | 1.89 x 0.41 |
| Supervisor | TEXTO | `SUPERVISOR. [NOMBRE]` — negrita, 11pt, `#5DD39E` | 0.70, 2.05 | 6.90 x 0.28 |
| Título | TEXTO | `Reporte de Guardia` — negrita, 35pt, blanco | 0.65, 2.42 | 8.90 x 0.72 |
| Fecha | TEXTO | `[día] y [día] de [Mes] [AAAA]` — 17pt, `#D8E2EA` | 0.70, 3.28 | 2.99 x 0.35 |
| Tarjeta | CAPTURA | Rectángulo redondeado `#173A5E` 88% / borde `#2B567B` 65% | 0.70, 4.25 | 8.30 x 1.45 |
| Etiqueta | TEXTO | `Talleres supervisados` — negrita, 11pt, `#5DD39E` | 1.00, 4.45 | 2.50 x 0.25 |
| Talleres | TEXTO | Lista separada por ` · ` — 14pt, blanco | 1.00, 4.85 | 7.65 x 0.55 |
| Pie | TEXTO | `oigperu.com` — 10.5pt, `#D8E2EA` | 0.70, 6.86 | 1.50 x 0.23 |

Ejemplo de línea de talleres:

`Compresión · Generación · Movimiento de suelos / Remoción · Gasfitería, ductos y tanques · Energía / Montaje / Flota / Mecánica / Instrumentación`

---

## DIAPOSITIVAS 2 Y 3 — CONTENIDO (PÁGINAS DE REPORTE)

Estas páginas llevan un marco fijo (chrome) y, en el cuerpo, el contenido del reporte.

### Marco fijo (CAPTURA — no se mueve)

| Elemento | Tipo | Contenido / Formato | Posición (in) | Tamaño (in) |
|----------|------|---------------------|---------------|-------------|
| Barra superior | CAPTURA | Línea teal `#00A6A6` | 0.00, 0.00 | 13.33 x 0.04 |
| Logo | CAPTURA | Logo pequeño (arriba derecha) | 11.25, 0.22 | 1.45 x 0.35 |
| Separador pie | CAPTURA | Línea `#CBD5E1` | 0.55, 7.05 | 12.25 x 0.00 |
| Pie izquierdo | TEXTO | `Reporte de Guardia \| [Mes] [AAAA]` — 7.5pt, `#64748B` | 0.65, 7.13 | 5.50 x 0.20 |
| Número de página | TEXTO | `[XX]` — 7.5pt, `#64748B`, derecha | 12.25, 7.10 | 0.45 x 0.20 |

### Cuerpo de la página

| Elemento | Tipo | Descripción |
|----------|------|-------------|
| Captura de ejemplo | CAPTURA | Imagen a pantalla completa (13.33 x 6.53) que muestra **cómo se ve el reporte** (texto y diseño). Es solo referencia. |
| Contenido real | TEXTO / TABLAS | Los datos del Reporte de Guardia (talleres, pendientes, producción, evidencias) ordenados. |
| Fotografías | **FOTO (movible)** | Fotos de evidencia provenientes del Excel. Se insertan y pueden moverse/reordenarse. |

> En el archivo de referencia, el cuerpo es una **captura** (screenshot del reporte). En el reporte real se reemplaza por los datos extraídos y las **fotos movibles** del Excel.

---

## DIAPOSITIVA 4 — CIERRE

| Elemento | Tipo | Contenido / Formato | Posición (in) | Tamaño (in) |
|----------|------|---------------------|---------------|-------------|
| Imagen lateral | CAPTURA | Imagen decorativa (derecha, 85% opacidad) | 5.53, 0.00 | 7.80 x 5.20 |
| Título | TEXTO | `Muchas Gracias` — negrita, 30pt, blanco | 0.70, 3.42 | 5.50 x 0.50 |
| Pie | TEXTO | `oigperu.com` — 10pt, `#D8E2EA` | 0.72, 6.92 | 1.50 x 0.22 |

---

## RESUMEN DE ESTRUCTURA (ORDEN)

1. **Portada** — Supervisor, título, fecha y talleres supervisados.
2. **Contenido** — páginas con los datos del reporte (producción, talleres, pendientes, evidencias) y las **fotos** del Excel.
3. **Cierre** — "Muchas Gracias".

### Regla de oro

- **CAPTURAS (fijas):** fondo, logos, barras, pie de página, número de página y capturas de ejemplo. No se mueven.
- **FOTOS (movibles):** fotografías de evidencia del Excel. Se insertan, se mueven y se reordenan.
