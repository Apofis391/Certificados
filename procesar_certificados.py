#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script para procesar PDFs de certificados y separarlos por página
Detecta automáticamente: C_P (Promoción) y C_M (Matrícula)
"""

import os
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
print("🔄 Inicializando OCR (primera vez toma tiempo)...")
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)
print("✅ OCR listo\n")

def normalizar_texto(texto):
    """Normaliza texto para búsqueda de palabras clave"""
    if not texto:
        return ""
    # Convertir a minúsculas y remover caracteres especiales/acentos
    texto = texto.lower()
    # Remover caracteres no ASCII pero mantener letras
    texto = unicodedata.normalize('NFKD', texto)
    texto = texto.encode('ASCII', 'ignore').decode('ASCII')
    return texto

def detectar_tipo_certificado(texto):
    """
    Detecta si es C_P (Promoción) o C_M (Matrícula)
    Retorna: 'P' para promoción, 'M' para matrícula, None si no detecta
    """
    texto_normalizado = normalizar_texto(texto)
    
    # Palabras clave para promoción
    palabras_promocion = ['promocion', 'promovido', 'promovida', 'ascenso', 'grado']
    # Palabras clave para matrícula
    palabras_matricula = ['matricula', 'matriculado', 'matriculada', 'enrollment', 'inscripcion', 'inscr']
    
    # Contar coincidencias
    count_p = sum(1 for palabra in palabras_promocion if palabra in texto_normalizado)
    count_m = sum(1 for palabra in palabras_matricula if palabra in texto_normalizado)
    
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
    lineas = texto.split('\n')
    
    for linea in lineas:
        if 'nombre' in linea.lower() and ':' in linea.lower():
            # Extraer todo después del primer ":"
            partes_linea = linea.split(':')
            if len(partes_linea) > 1:
                nombre_completo = partes_linea[1].strip()
                # Limpiar caracteres especiales no deseados
                nombre_completo = re.sub(r'[^\w\s\-áéíóúñÁÉÍÓÚÑ]', '', nombre_completo)
                
                # Dividir por espacios
                palabras = nombre_completo.split()
                
                if len(palabras) >= 2:
                    # Buscar si hay "DE" (para casos como "YUSETH DE LOS ANGELES")
                    indice_de = -1
                    for i, palabra in enumerate(palabras):
                        if palabra.upper() == 'DE':
                            indice_de = i
                            break
                    
                    if indice_de > 0:
                        # Formato: Apellido(s) DE Nombre(s)
                        apellido = ' '.join(palabras[:indice_de])
                        nombre = ' '.join(palabras[indice_de + 1:])
                    elif len(palabras) == 2:
                        # Simple: Apellido Nombre
                        apellido = palabras[0]
                        nombre = palabras[1]
                    else:
                        # Más de 2 palabras sin DE: primeras 2 son apellidos, resto nombre
                        apellido = ' '.join(palabras[:2])
                        nombre = ' '.join(palabras[2:])
                    
                    # Validar que ambos tengan contenido
                    if apellido.strip() and nombre.strip():
                        return nombre.strip(), apellido.strip()
    
    return None, None

def limpiar_nombre_archivo(nombre):
    """Convierte nombre a formato válido para nombre de archivo"""
    if not nombre:
        return ""
    # Convertir a mayúsculas
    nombre = nombre.upper()
    # Reemplazar espacios con guion bajo
    nombre = re.sub(r'\s+', '_', nombre)
    # Remover caracteres especiales
    nombre = re.sub(r'[^A-Z0-9_Á-Ú]', '', nombre)
    # Normalizar tildes (para compatibilidad)
    nombre = unicodedata.normalize('NFKD', nombre)
    nombre = nombre.encode('ASCII', 'ignore').decode('ASCII')
    return nombre

def extraer_texto_pdf(ruta_pdf):
    """Extrae texto de todas las páginas usando OCR"""
    textos = []
    try:
        pdf_document = fitz.open(str(ruta_pdf))
        
        for pagina_num in range(len(pdf_document)):
            pagina = pdf_document[pagina_num]
            # Renderizar página a imagen con buena resolución
            pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            
            if pix.n == 4:  # RGBA
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
            
            # Extraer texto con OCR
            resultados = ocr_reader.readtext(img_array, detail=0)
            texto = "\n".join(resultados) if resultados else ""
            textos.append(texto)
        
        pdf_document.close()
    except Exception as e:
        print(f"❌ Error leyendo {ruta_pdf.name}: {e}")
        return []
    
    return textos

def procesar_pdf(ruta_pdf):
    """Procesa un PDF, separando páginas y renombrando según tipo"""
    certificados_creados = []
    
    # Extraer texto de todas las páginas
    textos = extraer_texto_pdf(ruta_pdf)
    if not textos:
        return certificados_creados
    
    # Procesar cada página
    try:
        reader = PdfReader(str(ruta_pdf))
        
        for pagina_num, texto_pagina in enumerate(textos):
            # Detectar tipo de certificado
            tipo = detectar_tipo_certificado(texto_pagina)
            if not tipo:
                continue
            
            # Extraer nombre y apellido
            nombre, apellido = extraer_nombre_apellido(texto_pagina)
            if not nombre or not apellido:
                continue
            
            # Crear nombre de archivo
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
            
            # Guardar página como PDF individual
            writer = PdfWriter()
            writer.add_page(reader.pages[pagina_num])
            with open(ruta_salida, "wb") as f:
                writer.write(f)
            
            tipo_desc = "Promoción" if tipo == 'P' else "Matrícula"
            certificados_creados.append({
                'tipo': tipo,
                'nombre': nombre,
                'apellido': apellido,
                'archivo': nombre_archivo,
                'tipo_desc': tipo_desc
            })
    
    except Exception as e:
        print(f"❌ Error procesando {ruta_pdf.name}: {e}")
    
    return certificados_creados

def main():
    """Función principal"""
    archivos_pdf = sorted(list(CARPETA_PDF.glob("*.pdf")))
    
    if not archivos_pdf:
        print("❌ No hay archivos PDF en la carpeta 'pdf'")
        return
    
    print(f"📂 Se encontraron {len(archivos_pdf)} archivos PDF\n")
    
    total_p = 0
    total_m = 0
    
    for idx, ruta_pdf in enumerate(archivos_pdf, 1):
        print(f"[{idx}/{len(archivos_pdf)}] 📄 {ruta_pdf.name}")
        
        certificados = procesar_pdf(ruta_pdf)
        
        if not certificados:
            print(f"  ⚠️  No se procesaron certificados\n")
            continue
        
        count_p = sum(1 for c in certificados if c['tipo'] == 'P')
        count_m = sum(1 for c in certificados if c['tipo'] == 'M')
        
        total_p += count_p
        total_m += count_m
        
        if count_p > 0:
            print(f"  ✅ Promoción: {count_p}")
        if count_m > 0:
            print(f"  ✅ Matrícula: {count_m}")
        
        for cert in certificados:
            print(f"    → {cert['tipo']}_{cert['apellido']} {cert['nombre']}")
        
        print()
    
    print("\n" + "="*60)
    print("✨ PROCESAMIENTO COMPLETADO")
    print("="*60)
    print(f"📊 Certificados de PROMOCIÓN (C_P):  {total_p}")
    print(f"📊 Certificados de MATRÍCULA (C_M):  {total_m}")
    print(f"📊 TOTAL:                             {total_p + total_m}")
    print(f"📁 Ubicación: {CARPETA_PROCESADA.absolute()}")
    print("="*60)

if __name__ == "__main__":
    main()
