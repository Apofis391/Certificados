# ✅ Separación de certificados (PDF) + validación de faltantes

Convierte PDFs ubicados en `pdf/` en PDFs individuales (1 página = 1 archivo) dentro de `pdf_procesados/`.

La versión actual guarda **todo en la raíz** de `pdf_procesados/` (no crea subcarpetas nuevas):

- `pdf_procesados/C_<TIPO>_<APELLIDO>_<NOMBRE>_PAG##.pdf`

> Nota: si ya tienes subcarpetas de ejecuciones anteriores (por ejemplo `pdf_procesados/20260407_142728/`), el script las **reconoce** al validar/deduplicar, pero **ya no crea** carpetas nuevas.

Además, compara lo generado contra una **lista de control** en `pdf_generados_dev.txt` (la lista de lo que debes imprimir/firmar). Si algo falta, lo reporta en un log.

---

## ⚡ Quick start (copy/paste)

```powershell
cd c:\xampp\htdocs\certificados
pip install pymupdf easyocr opencv-python numpy
python procesar.py
```

---

## ✅ Checklist (para imprimir y firmar sin errores)

1. `pdf/` contiene los PDFs originales.
2. `pdf_generados_dev.txt` contiene la lista completa esperada.
3. Ejecutas `python procesar.py`.
4. Verificas:
  - `pdf_procesados/` contiene los PDFs separados
  - si hubo faltantes, revisas `pdf_procesados/faltantes.log`

Opcional (recomendado si los nombres en los PDFs salen con alguna letra cambiada por OCR):

```powershell
python verificar_faltantes_fuzzy.py
```

## Índice

- [Qué hace](#qué-hace)
- [Comando exacto (Windows)](#comando-exacto-windows)
- [Instalación](#instalación)
- [Cómo preparar las carpetas](#cómo-preparar-las-carpetas)
- [Qué archivos genera (nombres y prefijos)](#qué-archivos-genera-nombres-y-prefijos)
- [Validación con pdf_generados_dev.txt (lista de control)](#validación-con-pdf_generados_devtxt-lista-de-control)
- [Dónde ver faltantes](#dónde-ver-faltantes)
- [Re-ejecución / no reprocesar](#re-ejecución--no-reprocesar)
- [Comandos (todas las funciones)](#comandos-todas-las-funciones)
- [Solución de problemas](#solución-de-problemas)

---

## Qué hace

1. Lee todos los `*.pdf` dentro de `pdf/`.
  - Si dentro de `pdf/` hay PDFs que ya empiezan con `C_P_` o `C_M_`, se consideran ya divididos y se saltan.
2. Por cada PDF, procesa página por página:
   - Hace OCR (texto) para detectar el tipo de certificado.
   - Intenta extraer `Nombre` y `Apellido`.
3. Exporta cada página como un nuevo PDF dentro de `pdf_procesados/`.
4. Al finalizar, valida contra `pdf_generados_dev.txt` y registra faltantes en `pdf_procesados/faltantes.log`.

---

## Comandos (todas las funciones)

> Todos los comandos siguientes NO crean subcarpetas nuevas. Para evitar OCR/reescrituras usa siempre `--solo-validar`.

### 1) Separar PDFs (modo normal)

Procesa todo lo que haya en `pdf/` y separa páginas (si ya existe, no vuelve a exportar):

```powershell
python procesar.py
```

### 2) Validar vs lista (sin OCR)

```powershell
python procesar.py --solo-validar
```

### 3) Limpieza recomendada (sin OCR) — “limpiar duplicados”

1) Duplicados por lista (incluye duplicados que solo cambian `PAG##`):

```powershell
python procesar.py --solo-validar --dedupe-por-lista --dedupe-delete --yes
```

2) Duplicados idénticos por contenido (hash SHA-256):

```powershell
python procesar.py --solo-validar --dedupe-hash --dedupe-delete --yes
```

Logs:

- `pdf_procesados/duplicados_eliminados_por_lista.txt`
- `pdf_procesados/duplicados_eliminados_por_hash.txt`

### 4) Duplicados por persona ignorando `PAG##` (modo seguro)

Detecta duplicados por persona/tipo aunque el `PAG##` sea distinto. En modo seguro solo elimina si el contenido es equivalente.

Reporte:

```powershell
python procesar.py --solo-validar --dedupe-persona
```

Eliminar (modo seguro):

```powershell
python procesar.py --solo-validar --dedupe-persona --dedupe-delete --yes
```

Forzar (agresivo, deja solo 1 por persona aunque el contenido difiera):

```powershell
python procesar.py --solo-validar --dedupe-persona --dedupe-persona-force --dedupe-delete --yes
```

Log:

- `pdf_procesados/duplicados_eliminados_por_persona.txt`

### 5) Reparar nombres incompletos (por OCR)

Preview:

```powershell
python procesar.py --solo-validar --reparar-nombres
```

Aplicar:

```powershell
python procesar.py --solo-validar --reparar-nombres --yes
```

Log:

- `pdf_procesados/reparaciones_nombres.txt`

### 6) Generar SOLO los faltantes desde `pdf/` (sin crear carpetas)

Si el validador dice “faltan X”, genera únicamente esos faltantes, exportando a la raíz de `pdf_procesados/`:

```powershell
python procesar.py --solo-validar --generar-faltantes
```

Log:

- `pdf_procesados/generados_faltantes.txt`

### 7) Limpieza automática de nombres `SRC...`

Si quedaron archivos legacy tipo `C_M_SRC<id>_..._PAG##.pdf`, el script los renombra automáticamente a su versión sin `SRC` al iniciar.

---

---

## Comando exacto (Windows)

### Opción A: usando `python`

```powershell
cd c:\xampp\htdocs\certificados
python procesar.py
```

### Opción B: usando el launcher de Python (`py`)

Si Windows no reconoce `python`:

```powershell
cd c:\xampp\htdocs\certificados
py -3 procesar.py
```

---

## Instalación

### Requisitos

- Python 3.10+ (recomendado)
- Paquetes (pip):
  - `pymupdf` (se importa como `fitz`)
  - `easyocr`
  - `opencv-python` (se importa como `cv2`)
  - `numpy`

### Instalar dependencias

```powershell
pip install pymupdf easyocr opencv-python numpy
```

Notas importantes (para evitar confusiones):

- `easyocr` trae dependencias pesadas (incluye PyTorch). La **primera ejecución** suele tardar más.
- Si tienes varios Python instalados, asegúrate de instalar los paquetes en el mismo Python que ejecuta `procesar.py`.

---

## Cómo preparar las carpetas

- Entrada: coloca tus PDFs originales aquí:
  - `pdf/`
- Salida: aquí se guardan los PDFs separados:
  - `pdf_procesados/`

Estructura esperada:

```text
certificados/
  procesar.py
  pdf_generados_dev.txt
  pdf/
    archivo1.pdf
    archivo2.pdf
  pdf_procesados/
```

---

## Qué archivos genera (nombres y prefijos)

Cada página se exporta como un PDF con este formato:

```text
C_<TIPO>_<APELLIDO>_<NOMBRE>_PAG##.pdf
```

Donde:

- `C_P_...` = Promoción
- `C_M_...` = Matrícula
- `C_X_...` = no se detectó el tipo (fallback)
- `PAG##` = número de página en el PDF original (siempre 2 dígitos, por ejemplo `PAG01`)

### Normalización (para que el nombre de archivo sea válido)

Para evitar problemas de Windows con caracteres, los nombres de archivo se normalizan:

- Se convierten a MAYÚSCULAS.
- Se reemplazan espacios por `_`.
- Se eliminan caracteres inválidos para Windows (por ejemplo `<>:"/\\|?*`).

Ejemplo real:

```text
C_P_ABAD_MAZA_NATHALY_NICOLE_PAG01.pdf
```

### Nota sobre `SRC...`

Si ves archivos con `SRC...` en el nombre, son de ejecuciones anteriores (un esquema viejo).
La versión actual ya no crea `SRC...` y además los **renombra automáticamente** (sin crear carpetas) para evitar duplicados.
```

---

## Validación con pdf_generados_dev.txt (lista de control)

### Para qué sirve

`pdf_generados_dev.txt` es tu **lista de certificados que deberían existir** (para saber si te faltó imprimir/firmar alguno).

Al final de la ejecución, el script compara lo que existe en `pdf_procesados/` (incluyendo subcarpetas) contra esa lista.

### Opcional: quitar duplicados antes de validar

Si tienes copias exactas del mismo PDF (mismo nombre de archivo) en más de una ruta, puedes limpiar antes de validar:

```powershell
python procesar.py --solo-validar --dedupe-por-lista --dedupe-delete --yes
```

Esto elimina duplicados **solo si están en** `pdf_generados_dev.txt` y deja un log en:

- `pdf_procesados/duplicados_eliminados_por_lista.txt`

Si solo quieres que avise (sin borrar):

```powershell
python procesar.py --solo-validar --dedupe-por-lista --dedupe
```

### Limpieza extra (copias idénticas aunque tengan distinto nombre)

Si sigues encontrando “duplicados” pero con nombres diferentes (misma página exportada varias veces), usa dedupe por contenido (hash):

```powershell
python procesar.py --solo-validar --dedupe-hash --dedupe-delete --yes
```

### Reparar nombres incompletos (por OCR)

Si existe un PDF con nombre incompleto (ej. `C_M_ARMIJOS_POVEDA_PAG07.pdf`), puedes repararlo con OCR:

Preview (no aplica cambios):

```powershell
python procesar.py --solo-validar --reparar-nombres
```

Aplicar renombres:

```powershell
python procesar.py --solo-validar --reparar-nombres --yes
```

Log:

- `pdf_procesados/reparaciones_nombres.txt`

### (Opcional) Mover a la raíz de pdf_procesados/

Si quedaron PDFs dentro de subcarpetas y quieres todo plano en `pdf_procesados/`:

```powershell
python procesar.py --solo-validar --mover-a-raiz --yes
```

Log:

- `pdf_procesados/mover_a_raiz.log`

### Generar SOLO los faltantes desde pdf/

Si el validador dice “faltan X”, puedes generar únicamente esos faltantes (escanea PDFs en `pdf/` y exporta a la raíz de `pdf_procesados/`):

```powershell
python procesar.py --solo-validar --generar-faltantes
```

Log:

- `pdf_procesados/generados_faltantes.txt`

Si quieres procesar los PDFs y luego deduplicar/validar al final de la corrida (más lento):

```powershell
python procesar.py --dedupe-delete --yes
```

### Formato recomendado (tu formato actual)

Tu archivo puede ser una tabla separada por TABs, por ejemplo:

```text
Nombre del certificado	Fecha	Acción
CERTIFICADO DE PROMOCIÓN - Abad Maza Nathaly Nicole	01/04/2026
CERTIFICADO DE MATRÍCULA - Abad Maza Nathaly Nicole	01/04/2026
```

Reglas (sin ambigüedad):

- Se ignora el encabezado `Nombre del certificado	Fecha	Acción`.
- Se usa principalmente el texto de la primera columna (antes del primer TAB).
- Debe contener `PROMOCIÓN` o `MATRÍCULA` y un `-` con el nombre: `CERTIFICADO DE ... - <Nombre y Apellidos>`.
- No necesitas escribir `_PAG##`: el validador acepta cualquier página que coincida con el patrón.

> Nota sobre nombres: el validador usa una heurística simple para separar apellido/nombre (asume que los **últimos 1–2 términos** suelen ser el/los nombres y lo anterior apellidos). Si algún caso raro no coincide, usa el modo de **archivo exacto** (`.pdf`) para eliminar cualquier duda.

### Alternativa: validar por nombre exacto de archivo

Si en lugar del texto quieres validar por archivo exacto, puedes poner líneas con `.pdf`:

```text
C_P_ABAD_MAZA_NATHALY_NICOLE_PAG01.pdf
```

Esto es lo más estricto y elimina ambigüedades (si falta ese archivo exacto, se reporta como faltante).

### Duplicados (importante)

- La validación es **por línea**.
- Por defecto, si una misma persona/tipo aparece repetida en `pdf_generados_dev.txt`, se considera **duplicado de la lista** y se ignora.
  - Se registra en `pdf_procesados/esperados_duplicados.txt`.
- Si necesitas el comportamiento antiguo (contar repetidos como requerimientos adicionales), usa:
  - `python procesar.py --solo-validar --contar-repetidos`

---

## Dónde ver faltantes

Si falta algo:

- Consola: muestra un resumen (cantidad de faltantes)
- Log: se escribe en
  - `pdf_procesados/faltantes.log`

El log guarda la **línea original** del `.txt` que no pudo ser satisfecha.

---

## Re-ejecución / no reprocesar

El script mantiene un registro incremental:

- `pdf_procesados/.procesados.json`

Eso permite:

- Re-ejecutar sin volver a exportar páginas ya generadas.
- Saltar PDFs que ya fueron procesados (si no cambiaron en tamaño/fecha).

Importante:

- Si cancelas con `Ctrl+C`, el script guarda progreso parcial en el registro para que al re-ejecutar no regenere lo ya exportado.

### Si quieres limpiar nombres viejos (por ejemplo `SRC...`)

Opción rápida (reinicio total):

1. Borra PDFs en `pdf_procesados/` (opcional)
2. Borra `pdf_procesados/.procesados.json`
3. Ejecuta `python procesar.py`

Si necesitas “empezar desde cero”:

1. Borra PDFs en `pdf_procesados/` (opcional)
2. Borra `pdf_procesados/.procesados.json`
3. Ejecuta `python procesar.py`

---

## Detectar/eliminar duplicados exactos

Si por algún motivo te quedan archivos repetidos con el mismo nombre base (por ejemplo:
`C_P_VEGA_OROZCO_ROSARIO_DEL_CARMEN_PAG01.pdf` y `..._PAG02.pdf`), puedes detectarlos y eliminarlos.

### Solo reporte (no borra nada)

```powershell
python limpiar_duplicados.py
```

Por defecto usa un modo seguro (`--mode base+pag`) que solo marca duplicado cuando coincide el nombre base + el `PAG##`.

### Modo agresivo (por nombre base sin importar PAG)

Úsalo solo si quieres quedarte con 1 archivo por nombre base (por ejemplo, porque el mismo certificado se generó varias veces):

```powershell
python limpiar_duplicados.py --mode base
```

### Eliminar duplicados (deja 1 por clave)

Por seguridad requiere confirmación con `--yes`.

```powershell
python limpiar_duplicados.py --mode base+pag --keep prefer-subdir --delete --yes
```

Opcional: elegir cuál conservar

```powershell
python limpiar_duplicados.py --delete --yes --keep lowest-pag
```

---

## Solución de problemas

### “Inicializando OCR…” tarda mucho

- Normal en la primera ejecución (modelos/pesos).
- En máquinas lentas puede tardar varios minutos.

### No detecta tipo o nombre

- Igual exporta la página (fallback):
  - `C_X_SIN_NOMBRE_SIN_NOMBRE_PAG##.pdf`

### `fitz` / `easyocr` / `cv2` no se encuentran

- Reinstala dependencias en el mismo Python con el que ejecutas:

```powershell
pip install pymupdf easyocr opencv-python numpy
```

<details>
<summary><strong>¿Cómo saber qué Python está ejecutando el script?</strong></summary>

En PowerShell:

```powershell
python -c "import sys; print(sys.executable)"
python -m pip --version
```

La ruta debe corresponder al Python donde instalaste los paquetes.

</details>

---
