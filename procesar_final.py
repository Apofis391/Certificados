#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script optimizado para procesar PDFs de certificados
Procesa página por página: detecta tipo, extrae nombre y guarda
"""

import re
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
import fitz
import easyocr
import cv2
import numpy as np
import unicodedata

# Configuración
CARPETA_PDF = Path("pdf")
CARPETA_PROCESADA = Path("pdf_procesados")
CARPETA_PROCESADA.mkdir(exist_ok=True)

# Inicializar OCR una sola vez
print("🔄 Inicializando OCR...")
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)
print("✅ OCR listo\n")

def normalizar_texto(texto):
    """Normaliza texto removiendo tildes y caracteres especiales"""
    if not texto:
        return ""
    texto = texto.lower()
    texto = unicodedata.normalize('NFKD', texto)
    texto = texto.encode('ASCII', 'ignore').decode('ASCII')
    return texto

def detectar_tipo_certificado(texto):
    """Retorna 'P' (promoción), 'M' (matrícula) o None"""
    texto_norm = normalizar_texto(texto)
    
    # Contar palabras clave
    count_p = sum(texto_norm.count(p) for p in ['promocion', 'promovido', 'promovida', 'aprobado'])
    count_m = sum(texto_norm.count(p) for p in ['matricula', 'matriculado', 'matriculada', 'inscripcion'])
    
    if count_p > count_m and count_p > 0:
        return 'P'
    elif count_m > count_p and count_m > 0:
        return 'M'
    elif count_p > 0:
        return 'P'
    elif count_m > 0:
        return 'M'
    return None

def extraer_nombre_apellido(texto):
    """Extrae nombre y apellido de la línea 'Nombre: ...'"""
    for linea in texto.split('\n'):
        if 'nombre' in linea.lower() and ':' in linea:
            # Extraer después del ":"
            nombre_completo = linea.split(':', 1)[1].strip()
            nombre_completo = re.sub(r'[^\w\s\-áéíóúñÁÉÍÓÚÑ]', '', nombre_completo)
            
            palabras = nombre_completo.split()
            if len(palabras) < 2:
                continue
            
            # Buscar "DE" en el nombre
            for i, p in enumerate(palabras):
                if p.upper() == 'DE' and i > 0:
                    apellido = ' '.join(palabras[:i]).strip()
                    nombre = ' '.join(palabras[i+1:]).strip()
                    if apellido and nombre:
                        return nombre, apellido
            
            # Si hay 2 palabras: apellido nombre
            if len(palabras) == 2:
                return palabras[1], palabras[0]
            
            # Si hay más: primeras 2 son apellidos, resto es nombre
            if len(palabras) > 2:
                return ' '.join(palabras[2:]), ' '.join(palabras[:2])
    
    return None, None

def limpiar_nombre_archivo(texto):
    """Convierte nombre a formato válido para archivo"""
    if not texto:
        return ""
    texto = texto.upper()
    texto = re.sub(r'\s+', '_', texto)
    texto = re.sub(r'[^A-Z0-9_]', '', texto)
    texto = unicodedata.normalize('NFKD', texto)
    texto = texto.encode('ASCII', 'ignore').decode('ASCII')
    return texto

def extraer_texto_pagina_ocr(pdf_document, numero_pagina):
    """Extrae texto de UNA página usando OCR"""
    try:
        pagina = pdf_document[numero_pagina]
        pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        
        if pix.n == 4:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
        
        resultados = ocr_reader.readtext(img_array, detail=0)
        return "\n".join(resultados) if resultados else ""
    except Exception as e:
        print(f"❌ Error extrayendo página {numero_pagina}: {e}")
        return ""

def procesar_pdf(ruta_pdf):
    """
    Procesa PDF página por página:
    - Detecta tipo (C_P o C_M)
    - Extrae nombre y apellido
    - Guarda archivo
    - Pasa a siguiente página
    """
    certificados_procesados = []
    
    try:
        pdf_doc = fitz.open(str(ruta_pdf))
        pdf_reader = PdfReader(str(ruta_pdf))
        total_paginas = len(pdf_doc)
        
        for num_pagina in range(total_paginas):
            # PASO 1: Extraer texto de esta página
            texto = extraer_texto_pagina_ocr(pdf_doc, num_pagina)
            if not texto:
                continue
            
            # PASO 2: Detectar tipo
            tipo = detectar_tipo_certificado(texto)
            if not tipo:
                continue
            
            # PASO 3: Extraer nombre y apellido
            nombre, apellido = extraer_nombre_apellido(texto)
            if not nombre or not apellido:
                continue
            
            # PASO 4: Crear nombre de archivo
            prefijo = f"C_{tipo}"
            nombre_limpio = limpiar_nombre_archivo(nombre)
            apellido_limpio = limpiar_nombre_archivo(apellido)
            nombre_archivo = f"{prefijo}_{apellido_limpio}_{nombre_limpio}.pdf"
            
            # Evitar duplicados
            ruta_salida = CARPETA_PROCESADA / nombre_archivo
            contador = 1
            nombre_base = nombre_archivo.rsplit('.', 1)[0]
            while ruta_salida.exists():
                nombre_archivo = f"{nombre_base}_dup{contador}.pdf"
                ruta_salida = CARPETA_PROCESADA / nombre_archivo
                contador += 1
            
            # PASO 5: Guardar página como PDF individual
            try:
                writer = PdfWriter()
                writer.add_page(pdf_reader.pages[num_pagina])
                with open(ruta_salida, "wb") as f:
                    writer.write(f)
                
                tipo_desc = "Promoción" if tipo == 'P' else "Matrícula"
                certificados_procesados.append({
                    'tipo': tipo,
                    'nombre': nombre,
                    'apellido': apellido,
                    'archivo': nombre_archivo
                })
                
                print(f"  ✅ Página {num_pagina + 1}: C_{tipo}_{apellido_limpio} {nombre_limpio}")
                
            except Exception as e:
                print(f"  ❌ Error guardando página {num_pagina + 1}: {e}")
        
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
    
    print(f"📂 Encontrados {len(archivos_pdf)} archivos PDF\n")
    
    total_p = 0
    total_m = 0
    
    for idx, ruta_pdf in enumerate(archivos_pdf, 1):
        print(f"[{idx}/{len(archivos_pdf)}] 📄 {ruta_pdf.name}")
        
        certificados = procesar_pdf(ruta_pdf)
        
        if certificados:
            count_p = sum(1 for c in certificados if c['tipo'] == 'P')
            count_m = sum(1 for c in certificados if c['tipo'] == 'M')
            total_p += count_p
            total_m += count_m
            print(f"  → Promoción: {count_p} | Matrícula: {count_m}\n")
        else:
            print(f"  ⚠️ No se procesaron certificados\n")
    
    print("\n" + "="*70)
    print("✨ PROCESAMIENTO COMPLETADO")
    print("="*70)
    print(f"📊 Certificados de PROMOCIÓN (C_P):  {total_p}")
    print(f"📊 Certificados de MATRÍCULA (C_M):  {total_m}")
    print(f"📊 TOTAL:                             {total_p + total_m}")
    print(f"📁 Ubicación: {CARPETA_PROCESADA.absolute()}")
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
