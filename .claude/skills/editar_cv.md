---
name: editar_cv
description: Edita el CV base del usuario (input/base_cv.yaml) a pedido — agregar/quitar/cambiar habilidades, categorías, experiencias, proyectos, bullets, secciones u orden. Activar cuando el usuario diga: editar CV base, agregar habilidad, agregar experiencia, añadir proyecto, cambiar mi CV base, quitar categoría, editar bullet, reordenar secciones, /editar-cv, base_cv.yaml. La skill asume que el proyecto auto-tailored-cv es el directorio de trabajo actual (cwd); si no lo es, hacé cd ahí primero.
---

# editar_cv — skill para Claude Code / Opencode

## ⚠️ LEER PRIMERO — acceso a herramientas y entorno

- **Tenés acceso a herramientas de shell/bash para ejecutar comandos en la
  laptop del usuario.** En Claude Desktop esto se habilita vía el conector
  **Desktop Commander**; en Claude Code / Opencode ya tenés la tool Bash/Shell.
  **Usalas directamente** — no dudes, no pidas permiso y no asumas que no
  tenés acceso.
- **El proyecto vive en** `/Users/axelaaronccasanihuachua/Desktop/Data_Science/auto-tailored-cv`.
  Antes de correr cualquier comando, `cd` a ese directorio. Verificá que
  estás ahí con `ls run.sh`.
- **El virtualenv YA existe** en `.venv/`. Para validar usá
  `.venv/bin/python scripts/validate_base_cv.py input/base_cv.yaml`
  (o `./run.sh`, que levanta el venv solo).

## Qué es el CV base (y qué NO es)

- La fuente editable de TODO el pipeline es `input/base_cv.yaml`, validado
  contra `schema/base_cv.schema.json`. Cada CV tailorizado por oferta se
  genera a partir de este archivo — si un dato no está acá, el pipeline
  **jamás** lo inventa en los CVs generados.
- **No edites nunca los CVs generados** (`output/*/cv.html`, `analysis.json`)
  para cambiar datos de fondo: esos son descartables y se regeneran. El
  cambio va siempre en `input/base_cv.yaml`.
- `input/base_cv.yaml` es **dato personal gitignored**: editá el archivo en
  el disco, validalo, pero **nunca lo commitees ni lo muestres en logs**.

## Procedimiento (siempre igual)

1. **Leer la referencia**: `schema/base_cv.schema.json` y
   `schema/example.yaml` (los 3 tipos de sección y sus campos obligatorios).
2. **Leer el estado actual**: `input/base_cv.yaml` completo.
3. **Aplicar el pedido del usuario** respetando el schema exacto:
   - *Agregar una habilidad/herramienta a una categoría* → nuevo `item` en
     el `simple_list` correspondiente. Preferí items **granulares y cortos**
     (uno por herramienta, ej. `Excel avanzado (tablas dinámicas, Power
     Query)`) en vez de strings largos con comas: el tailor puede reordenar
     y omitir items sueltos, pero no puede reordenar dentro de un string.
   - *Crear/quitar una categoría* → agregar/quitar un `item` (o toda la
     sección `simple_list` si es p. ej. "Idiomas"). El tailor conserva todas
     las categorías del base y solo omite items sueltos irrelevantes.
   - *Añadir experiencia laboral / proyecto* → nuevo `entry` en el
     `entry_block` con `heading` (puesto/empresa o nombre del proyecto),
     `dates`, y `bullets` con logros medibles. En Proyectos
     (`reorderable: true`) el primer bullet debe describir **qué hace** el
     proyecto; los siguientes dan detalles.
   - *Editar bullets* → reescribí `text` (podés agregar `tags` con keywords
     que demuestra el bullet).
   - *Reordenar secciones* → mover bloques en `sections`: **el orden del
     YAML es el orden de renderizado del PDF**.
4. **Validar automáticamente**:
   ```bash
   .venv/bin/python scripts/validate_base_cv.py input/base_cv.yaml
   ```
5. **Si falla**: corregí el YAML y repetí el paso 4 hasta que valide — sin
   pedirle al usuario que interprete el error.
6. Recién entonces confirmá el cambio y sugerí el siguiente paso
   (*"generá el CV para la oferta <url>"* — eso lo maneja la skill
   `cv_automatizacion`).

## Límites (decir que NO cuando corresponda)

- Si el usuario pide cambiar **cómo tailoriza** el pipeline (tono, énfasis,
  reglas por oferta) en vez de sus datos → eso NO va en el YAML: va en
  `input/preferences.txt` o en `prompts/<nombre>.override.txt`.
- Si el usuario pide **falsear** datos (empleos, títulos, fechas o skills
  que no tiene) → advertí que el evaluador del pipeline lo detectaría como
  alucinación contra el base, y pedí confirmación explícita antes de
  escribirlo.
