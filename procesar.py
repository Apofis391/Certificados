#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script optimizado para procesar PDFs mixtos de certificados
Detecta y separa: C_P (Promoción) y C_M (Matrícula)
"""

import re
import json
import warnings
import contextlib
import io
from datetime import datetime
from pathlib import Path
import fitz
import easyocr
import cv2
import numpy as np
import unicodedata

# Suprimir warnings innecesarios
warnings.filterwarnings('ignore')

# Configuración
CARPETA_PDF = Path("pdf")
CARPETA_PROCESADA = Path("pdf_procesados")
ARCHIVO_REGISTRO = CARPETA_PROCESADA / ".procesados.json"
ARCHIVO_ESPERADOS = Path("pdf_generados_dev.txt")
ARCHIVO_LOG_FALTANTES = CARPETA_PROCESADA / "faltantes.log"
CARPETA_PROCESADA.mkdir(exist_ok=True)

# OCR se inicializa solo si hace falta (para que re-ejecutar sea rápido)
ocr_reader = None

def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        print("🔄 Inicializando OCR...")
        with contextlib.redirect_stderr(io.StringIO()):
            ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)
        print("✅ OCR listo\n")
    return ocr_reader

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    texto = unicodedata.normalize('NFKD', texto)
    texto = texto.encode('ASCII', 'ignore').decode('ASCII')
    texto = texto.lower()
    texto = texto.replace('1', 'i').replace('|', 'i').replace('0', 'o')
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def cargar_registro_procesados():
    """Carga registro de PDFs ya procesados"""
    if ARCHIVO_REGISTRO.exists():
        try:
            with open(ARCHIVO_REGISTRO, 'r') as f:
                registro = json.load(f)

            # Migración suave desde formato viejo
            if isinstance(registro, dict):
                for nombre_pdf, data in list(registro.items()):
                    if not isinstance(data, dict):
                        continue

                    # Formato viejo: fechas_procesado / páginas_procesadas
                    if 'mtime' not in data and 'fechas_procesado' in data:
                        data['mtime'] = data.get('fechas_procesado')
                    if 'paginas' not in data and 'páginas_procesadas' in data:
                        data['paginas'] = data.get('páginas_procesadas')
                    data.setdefault('size', None)
                    data.setdefault('page_outputs', {})

                    # Si podemos, completar size/mtime reales desde el archivo en /pdf
                    ruta_pdf = CARPETA_PDF / nombre_pdf
                    if ruta_pdf.exists():
                        try:
                            st = ruta_pdf.stat()
                            data['mtime'] = str(st.st_mtime)
                            data['size'] = int(st.st_size)
                        except Exception:
                            pass

                    registro[nombre_pdf] = data

            return registro
        except:
            return {}
    return {}

def guardar_registro_procesados(registro):
    """Guarda registro de PDFs procesados"""
    try:
        with open(ARCHIVO_REGISTRO, 'w') as f:
            json.dump(registro, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error guardando registro: {e}")

def limpiar_nombre_archivo(texto):
    """Convierte texto a formato válido para nombre de archivo"""
    if not texto:
        return ""
    texto = texto.upper()
    texto = re.sub(r'\s+', '_', texto)
    texto = re.sub(r'[^A-Z0-9_]', '', texto)
    texto = unicodedata.normalize('NFKD', texto)
    texto = texto.encode('ASCII', 'ignore').decode('ASCII')
    return texto

def _split_nombre_apellido_desde_linea(nombre_completo: str):
    """Heurística simple (igual que en extraer_nombre_apellido): nombres al final."""
    nombre_completo = (nombre_completo or "").strip()
    if not nombre_completo:
        return None, None
    # Mantener letras/espacios/guiones para separar palabras
    nombre_completo = re.sub(r"[^\w\s\-áéíóúñÁÉÍÓÚÑ]", " ", nombre_completo)
    nombre_completo = re.sub(r"\s+", " ", nombre_completo).strip()
    partes = nombre_completo.split()
    if len(partes) < 2:
        return None, None
    if len(partes) >= 4:
        apellido = " ".join(partes[:-2]).strip()
        nombre = " ".join(partes[-2:]).strip()
        return nombre, apellido
    if len(partes) == 3:
        apellido = " ".join(partes[:2]).strip()
        nombre = partes[2].strip()
        return nombre, apellido
    # len == 2
    apellido = partes[0].strip()
    nombre = partes[1].strip()
    return nombre, apellido

def cargar_esperados_desde_txt(ruta_txt: Path):
    """Carga PDFs esperados.

    Formatos soportados por línea:
    - Un nombre de archivo que contenga '.pdf' (se valida por existencia exacta en pdf_procesados/)
    - Una fila tipo: 'CERTIFICADO DE PROMOCIÓN - Apellido Apellido Nombre Nombre ...' (se valida por patrón)
    """
    esperados = []
    if not ruta_txt.exists():
        return esperados

    try:
        contenido = ruta_txt.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        try:
            contenido = ruta_txt.read_text(encoding="latin-1", errors="ignore")
        except Exception:
            return esperados

    for raw in contenido.splitlines():
        linea = (raw or "").strip()
        if not linea:
            continue

        low = normalizar_texto(linea)
        # Saltar encabezados de tabla típicos
        if low.startswith("nombre del certificado") or low.startswith("certificado\t"):
            continue

        # Caso 1: contiene .pdf -> validar por nombre exacto
        if ".pdf" in low:
            # Extraer el último token parecido a archivo
            m = re.search(r"([^\\/\s]+\.pdf)\b", linea, flags=re.IGNORECASE)
            if m:
                esperados.append({
                    "kind": "exact",
                    "name": Path(m.group(1)).name,
                    "raw": linea,
                    "display": m.group(1).strip(),
                })
            continue

        # Caso 2: 'CERTIFICADO DE PROMOCION/MATRICULA - ...'
        tipo = None
        if "promocion" in low:
            tipo = "P"
        elif "matricula" in low:
            tipo = "M"

        if not tipo:
            continue

        # Tomar lo que está después del '-' si existe
        nombre_parte = linea
        if "-" in linea:
            nombre_parte = linea.split("-", 1)[1].strip()

        # Si está tabulado, quedarnos con la primera columna útil
        if "\t" in nombre_parte:
            nombre_parte = nombre_parte.split("\t", 1)[0].strip()

        nombre, apellido = _split_nombre_apellido_desde_linea(nombre_parte)
        if not nombre or not apellido:
            continue

        nombre_limpio = limpiar_nombre_archivo(nombre)
        apellido_limpio = limpiar_nombre_archivo(apellido)
        prefijo = f"C_{tipo}_"
        # En procesar.py el nombre final siempre incluye _PAGxx
        patron = re.compile(rf"^{re.escape(prefijo)}{re.escape(apellido_limpio)}_{re.escape(nombre_limpio)}_PAG\\d{{2}}\\.pdf$", re.IGNORECASE)
        esperados.append({
            "kind": "pattern",
            "pattern": patron,
            "raw": linea,
            "display": f"{prefijo}{apellido_limpio}_{nombre_limpio}_PAG??.pdf",
        })

    return esperados

def validar_esperados_y_log():
    """Valida que los PDFs esperados estén en pdf_procesados/.

    Escribe un log en pdf_procesados/faltantes.log si falta alguno.
    """
    esperados = cargar_esperados_desde_txt(ARCHIVO_ESPERADOS)
    if not esperados:
        print(f"ℹ️  No hay entradas válidas en {ARCHIVO_ESPERADOS.name} para validar.")
        return

    try:
        existentes = [p.name for p in CARPETA_PROCESADA.glob("*.pdf")]
    except Exception:
        existentes = []

    # Consumir (remove) archivos para que duplicados cuenten
    disponibles = list(existentes)
    faltantes = []

    for item in esperados:
        raw_line = item.get("raw") or item.get("display")

        if item["kind"] == "exact":
            nombre = item["name"]
            try:
                idx = disponibles.index(nombre)
            except ValueError:
                faltantes.append(raw_line)
                continue
            disponibles.pop(idx)
        else:
            patron = item["pattern"]
            found_idx = None
            for i, n in enumerate(disponibles):
                if patron.match(n):
                    found_idx = i
                    break
            if found_idx is None:
                faltantes.append(raw_line)
            else:
                disponibles.pop(found_idx)

    if not faltantes:
        print("✅ Validación OK: no faltan PDFs esperados.")
        return

    # Escribir log
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ARCHIVO_LOG_FALTANTES, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] FALTAN {len(faltantes)} PDFs esperados (según {ARCHIVO_ESPERADOS.name})\n")
            for x in faltantes:
                f.write(f"- {x}\n")
    except Exception as e:
        print(f"⚠️  No se pudo escribir el log de faltantes: {e}")

    print(f"⚠️  Faltan {len(faltantes)} PDFs esperados. Ver log: {ARCHIVO_LOG_FALTANTES}")

def extraer_texto_pagina_ocr(pdf_document, numero_pagina, *, solo_superior=True):
    """Extrae texto de UNA página usando OCR"""
    try:
        pagina = pdf_document[numero_pagina]

        clip = None
        if solo_superior:
            rect = pagina.rect
            clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * 0.65)

        pix = pagina.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False, clip=clip)
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        
        if pix.n == 4:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
        elif pix.n == 1:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
        
        try:
            resultados = get_ocr_reader().readtext(img_array, detail=0)
            return "\n".join(resultados) if resultados else ""
        except Exception as ocr_error:
            print(f"    ⚠️ Error en OCR para página {numero_pagina + 1}: {str(ocr_error)[:50]}")
            return ""
    except Exception as e:
        print(f"❌ Error OCR página {numero_pagina}: {e}")
        return ""

def detectar_tipo_certificado(texto):
    """
    Detecta si es C_P (Promoción) o C_M (Matrícula)
    Retorna: 'P' o 'M' o None
    """
    t = normalizar_texto(texto)

    # Score por palabras clave (más robusto que solo booleanos)
    claves_m = [
        ("certificado de matricula", 5),
        ("certificado matricula", 5),
        ("matricula", 2),
        ("matriculado", 2),
        ("matriculada", 2),
        ("costo matricula", 3),
        ("costo arancel", 2),
        ("arancel", 2),
        ("proximo semestre", 2),
        ("periodo academico", 1),
    ]
    claves_p = [
        ("certificado de promocion", 5),
        ("certificado promocion", 5),
        ("promocion", 2),
        ("promovido", 2),
        ("promovida", 2),
        ("aprobado", 1),
        ("aprobada", 1),
    ]

    score_m = sum(peso for k, peso in claves_m if k in t)
    score_p = sum(peso for k, peso in claves_p if k in t)

    if score_m == 0 and score_p == 0:
        return None
    return 'M' if score_m >= score_p else 'P'

def extraer_nombre_apellido(texto):
    """Extrae (nombre, apellido) desde líneas tipo 'Nombre:' o 'estudiante:'"""
    lineas = [l.strip() for l in texto.split('\n') if l.strip()]

    candidatos = []

    for linea in lineas:
        low = normalizar_texto(linea)

        # Caso 1: "Nombre: ..."
        if 'nombre' in low and ':' in linea:
            candidatos.append(linea.split(':', 1)[1].strip())
            continue

        # Caso 2: "estudiante: APELLIDOS NOMBRES,"
        if 'estudiante' in low and ':' in linea:
            candidato = linea.split(':', 1)[1].strip()
            if ',' in candidato:
                candidato = candidato.split(',', 1)[0].strip()
            # Cortes adicionales por si OCR no puso coma
            corte = re.split(r"\b(titular|cedula|c[eé]dula|identidad)\b", candidato, flags=re.IGNORECASE)
            candidato = corte[0].strip() if corte else candidato
            candidatos.append(candidato)
            continue

    # Último intento: buscar patrón con regex dentro del texto completo
    if not candidatos:
        m = re.search(r"estudiante\s*:\s*([^,\n]+)", texto, flags=re.IGNORECASE)
        if m:
            candidatos.append(m.group(1).strip())

    for nombre_completo in candidatos:
        try:
            # limpiar signos raros, mantener tildes/ñ para separar palabras
            nombre_completo = re.sub(r"[^\w\s\-áéíóúñÁÉÍÓÚÑ]", " ", nombre_completo)
            nombre_completo = re.sub(r"\s+", " ", nombre_completo).strip()

            palabras = nombre_completo.split()
            if len(palabras) < 2:
                continue

            # Heurística: normalmente nombres van al final
            if len(palabras) >= 4:
                apellido = ' '.join(palabras[:-2]).strip()
                nombre = ' '.join(palabras[-2:]).strip()
                return nombre, apellido
            if len(palabras) == 3:
                apellido = ' '.join(palabras[:2]).strip()
                nombre = palabras[2].strip()
                return nombre, apellido
            if len(palabras) == 2:
                apellido = palabras[0].strip()
                nombre = palabras[1].strip()
                return nombre, apellido
        except Exception:
            continue

    return None, None

def procesar_pdf(ruta_pdf, registro):
    """
    Procesa PDF página por página
    Evita reprocesar páginas ya procesadas
    """
    certificados_procesados = []
    
    nombre_pdf = ruta_pdf.name

    # No tocar PDFs ya divididos
    nombre_norm = nombre_pdf.upper()
    if nombre_norm.startswith("C_P_") or nombre_norm.startswith("C_M_"):
        print("  ⏭️  Ya está dividido (C_P/C_M). Saltando...")
        return None
    
    # Verificar si el PDF ya fue procesado
    try:
        stat = Path(ruta_pdf).stat()
        mtime = str(stat.st_mtime)
        size = int(stat.st_size)
    except Exception:
        mtime = None
        size = None

    anterior = registro.get(nombre_pdf) if isinstance(registro, dict) else None

    # Si el registro ya tiene page_outputs completo y los archivos existen, saltar rápido
    if anterior and anterior.get('mtime') == mtime and anterior.get('size') == size:
        try:
            page_outputs_prev = anterior.get('page_outputs') if isinstance(anterior.get('page_outputs'), dict) else {}
            total_prev = int(anterior.get('paginas') or 0)
            if total_prev > 0 and len(page_outputs_prev) == total_prev:
                all_exist = all((CARPETA_PROCESADA / v).exists() for v in page_outputs_prev.values())
                if all_exist:
                    print(f"  ⏭️  PDF ya fue procesado. Saltando...")
                    return None
        except Exception:
            pass
    
    try:
        pdf_doc = fitz.open(str(ruta_pdf))
        total_paginas = len(pdf_doc)
        
        print(f"  Total de páginas: {total_paginas}")

        page_outputs: dict[str, str] = {}
        if isinstance(anterior, dict) and isinstance(anterior.get('page_outputs'), dict):
            prev = anterior.get('page_outputs')
            if isinstance(prev, dict):
                try:
                    page_outputs = {str(k): str(v) for k, v in prev.items() if v}
                except Exception:
                    page_outputs = {}

        for num_pagina in range(total_paginas):
            # Si esta página ya se exportó (por registro), saltar
            prev_out = page_outputs.get(str(num_pagina))
            if prev_out and (CARPETA_PROCESADA / prev_out).exists():
                continue

            # OCR superior primero (rápido)
            texto_sup = extraer_texto_pagina_ocr(pdf_doc, num_pagina, solo_superior=True)
            texto = texto_sup

            tipo = detectar_tipo_certificado(texto)
            nombre, apellido = extraer_nombre_apellido(texto)

            # Si falta tipo o nombre, reintentar con OCR a página completa
            if (not tipo) or (not nombre) or (not apellido):
                texto_full = extraer_texto_pagina_ocr(pdf_doc, num_pagina, solo_superior=False)
                if texto_full and len(texto_full.strip()) > len(texto_sup.strip() if texto_sup else ""):
                    texto = texto_full
                tipo = tipo or detectar_tipo_certificado(texto)
                if (not nombre) or (not apellido):
                    nombre, apellido = extraer_nombre_apellido(texto)

            # Fallback: siempre exportar la página
            if not tipo:
                tipo = 'X'
            if not nombre:
                nombre = 'SIN_NOMBRE'
            if not apellido:
                apellido = 'SIN_NOMBRE'
            
            # Crear nombre de archivo
            prefijo = f"C_{tipo}"
            nombre_limpio = limpiar_nombre_archivo(nombre)
            apellido_limpio = limpiar_nombre_archivo(apellido)
            # Nombre estable por página: 1 archivo por hoja
            nombre_archivo = f"{prefijo}_{apellido_limpio}_{nombre_limpio}_PAG{num_pagina + 1:02d}.pdf"
            ruta_salida = CARPETA_PROCESADA / nombre_archivo

            if ruta_salida.exists():
                page_outputs[str(num_pagina)] = ruta_salida.name
                continue
            
            # Guardar página como PDF
            try:
                output_pdf = fitz.open()
                output_pdf.insert_pdf(pdf_doc, from_page=num_pagina, to_page=num_pagina)
                output_pdf.save(str(ruta_salida))
                output_pdf.close()
                
                tipo_desc = "Promoción" if tipo == 'P' else "Matrícula"
                certificados_procesados.append({
                    'tipo': tipo,
                    'nombre': nombre,
                    'apellido': apellido
                })
                
                print(f"  ✅ Página {num_pagina + 1}: C_{tipo} {apellido} {nombre}")

                page_outputs[str(num_pagina)] = ruta_salida.name
                
            except Exception as e:
                print(f"  ❌ Error guardando página {num_pagina + 1}: {e}")
        
        # Marcar PDF como procesado
        registro[nombre_pdf] = {
            'mtime': mtime,
            'size': size,
            'paginas': total_paginas,
            'page_outputs': page_outputs
        }
        
        pdf_doc.close()
    
    except Exception as e:
        print(f"❌ Error procesando {ruta_pdf.name}: {e}")
    
    return certificados_procesados

def main():
    """Función principal"""
    archivos_pdf = sorted(list(CARPETA_PDF.glob("*.pdf")))
    
    if not archivos_pdf:
        print("❌ No hay archivos PDF en la carpeta 'pdf'")
        return
    
    # Cargar registro de PDFs ya procesados
    registro = cargar_registro_procesados()
    
    print(f"📂 Encontrados {len(archivos_pdf)} archivos PDF\n")
    
    total_p = 0
    total_m = 0
    
    for idx, ruta_pdf in enumerate(archivos_pdf, 1):
        print(f"[{idx}/{len(archivos_pdf)}] 📄 {ruta_pdf.name}")
        
        certificados = procesar_pdf(ruta_pdf, registro)

        if certificados is None:
            print("  ⏭️  Saltado\n")
        elif certificados:
            count_p = sum(1 for c in certificados if c['tipo'] == 'P')
            count_m = sum(1 for c in certificados if c['tipo'] == 'M')
            total_p += count_p
            total_m += count_m
            print(f"  → Promoción (C_P): {count_p} | Matrícula (C_M): {count_m}\n")
        else:
            print(f"  ⚠️ No se procesaron certificados\n")
    
    # Guardar registro actualizado
    guardar_registro_procesados(registro)
    
    print("\n" + "="*70)
    print("✨ PROCESAMIENTO COMPLETADO")
    print("="*70)
    print(f"📊 Certificados de PROMOCIÓN (C_P):  {total_p}")
    print(f"📊 Certificados de MATRÍCULA (C_M):  {total_m}")
    print(f"📊 TOTAL:                             {total_p + total_m}")
    print(f"📁 Ubicación: {CARPETA_PROCESADA.absolute()}")
    print("="*70 + "\n")

    # Validación final contra pdf_generados_dev.txt
    validar_esperados_y_log()

if __name__ == "__main__":
    main()
