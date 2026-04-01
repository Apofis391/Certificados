# ✅ Separación de certificados (PDF) + validación de faltantes

Convierte PDFs ubicados en `pdf/` en PDFs individuales (1 página = 1 archivo) dentro de `pdf_procesados/`.

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

## Índice

- [Qué hace](#qué-hace)
- [Comando exacto (Windows)](#comando-exacto-windows)
- [Instalación](#instalación)
- [Cómo preparar las carpetas](#cómo-preparar-las-carpetas)
- [Qué archivos genera (nombres y prefijos)](#qué-archivos-genera-nombres-y-prefijos)
- [Validación con pdf_generados_dev.txt (lista de control)](#validación-con-pdf_generados_devtxt-lista-de-control)
- [Dónde ver faltantes](#dónde-ver-faltantes)
- [Re-ejecución / no reprocesar](#re-ejecución--no-reprocesar)
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
- Se eliminan tildes y caracteres especiales (por ejemplo `León` → `LEON`, `Añapa` → `ANAPA`).

Ejemplo real:

```text
C_P_ABAD_MAZA_NATHALY_NICOLE_PAG01.pdf
```

---

## Validación con pdf_generados_dev.txt (lista de control)

### Para qué sirve

`pdf_generados_dev.txt` es tu **lista de certificados que deberían existir** (para saber si te faltó imprimir/firmar alguno).

Al final de la ejecución, el script compara lo que existe en `pdf_procesados/` contra esa lista.

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
- Si una misma línea aparece 2 veces en el `.txt`, el validador esperará **2 PDFs distintos** que puedan satisfacerla.
  - Esto ayuda a detectar faltantes reales cuando tu lista tiene repetidos.

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

Si necesitas “empezar desde cero”:

1. Borra PDFs en `pdf_procesados/` (opcional)
2. Borra `pdf_procesados/.procesados.json`
3. Ejecuta `python procesar.py`

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
