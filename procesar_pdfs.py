import os
import re
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
import easyocr
from PIL import Image
import io
import fitz  # PyMuPDF
import cv2
import numpy as np

# Configuración
CARPETA_PDF = Path("pdf")
CARPETA_PROCESADA = Path("pdf_procesados")

# Crear carpeta de salida si no existe
CARPETA_PROCESADA.mkdir(exist_ok=True)

# Inicializar OCR (español + inglés)
print("Inicializando OCR (primera vez puede tomar un poco)...")
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)

def extraer_texto_pdf(ruta_pdf):
    """Extrae texto de todas las páginas de un PDF usando OCR con PyMuPDF"""
    textos = []
    try:
        # Abrir PDF con PyMuPDF
        pdf_document = fitz.open(str(ruta_pdf))
        
        for pagina_num in range(len(pdf_document)):
            pagina = pdf_document[pagina_num]
            
            # Renderizar página a imagen (pixmap)
            pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)  # Aumentar resolución
            
            # Convertir pixmap a numpy array
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            
            # Convertir BGR a RGB si es necesario
            if pix.n == 4:  # RGBA
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
            
            # Extraer texto con OCR
            resultados = ocr_reader.readtext(img_array, detail=0)
            texto = "\n".join(resultados)
            textos.append(texto)
        
        pdf_document.close()
            
    except Exception as e:
        print(f"Error al leer {ruta_pdf}: {e}")
        import traceback
        traceback.print_exc()
    
    return textos

def detectar_tipo_certificado(texto):
    """Detecta si es promoción o matrícula"""
    texto_lower = texto.lower()
    
    # Palabras clave para promoción
    palabras_promocion = ["promoción", "promotion", "promovido", "promovida", "ascenso", "grado"]
    # Palabras clave para matrícula
    palabras_matricula = ["matrícula", "matricula", "enrollment", "inscripción", "inscr", "matricul"]
    
    # Contar coincidencias
    count_promocion = sum(1 for palabra in palabras_promocion if palabra in texto_lower)
    count_matricula = sum(1 for palabra in palabras_matricula if palabra in texto_lower)
    
    if count_promocion > count_matricula and count_promocion > 0:
        return "promoción"
    elif count_matricula > count_promocion and count_matricula > 0:
        return "matrícula"
    
    # Si encuentra algo similar aunque sea parcial
    if any(palabra in texto_lower for palabra in palabras_promocion):
        return "promoción"
    if any(palabra in texto_lower for palabra in palabras_matricula):
        return "matrícula"
    
    return None

def extraer_nombre_apellido(texto):
    """Extrae nombre y apellido del PDF"""
    lineas = texto.split('\n')
    
    # Estrategia 1: Buscar patrones específicos comunes en certificados
    patrones = [
        r"(?:Que|que)\s+(.+?)\s+(?:con|ha sido|está)",  # "Que Juan Pérez ha sido"
        r"(?:Alumno|ALUMNO|Student)\s*:?\s*(.+?)(?:\n|$)",
        r"(?:Nombre|NAME)\s*:?\s*(.+?)(?:Apellido|LASTNAME)",
        r"^\s*([A-ZÁÉÍÓÚ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚ][a-záéíóúñ]+)?)\s+([A-ZÁÉÍÓÚ][a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?)",
    ]
    
    for patron in patrones:
        for linea in lineas:
            match = re.search(patron, linea.strip(), re.IGNORECASE | re.MULTILINE)
            if match:
                texto_extraido = match.group(1).strip()
                # Intentar dividir nombre y apellido
                palabras = texto_extraido.split()
                if len(palabras) >= 2:
                    # Tomar las últimas dos palabras (apellido y penúltim palabra)
                    # O probar si hay una separación clara
                    nombre = " ".join(palabras[:-1])
                    apellido = palabras[-1]
                    
                    if len(nombre) > 2 and len(apellido) > 2:
                        if all(c.isalpha() or c.isspace() or c in "áéíóúñÁÉÍÓÚÑ" for c in nombre + apellido):
                            return nombre, apellido
    
    # Estrategia 2: Buscar palabras capitalizadas que parezcan nombres
    palabras_capitalizadas = [p.strip() for p in re.findall(r'\b[A-ZÁÉÍÓÚ][a-záéíóúñ]+\b', texto) if len(p) > 2]
    # Filtrar palabras comunes
    palabras_comunes = {'certificado', 'diploma', 'grado', 'alumno', 'course', 'student', 'promoción', 'matrícula', 'nivel', 'escuela', 'colegio', 'educación', 'que', 'del', 'por', 'for', 'ha', 'sido', 'the', 'academic', 'year', 'registrado', 'constancia', 'presente', 'certifica', 'certify', 'hereby'}
    
    palabras_capitalizadas = [p for p in palabras_capitalizadas if p.lower() not in palabras_comunes]
    
    if len(palabras_capitalizadas) >= 2:
        # Usar las primeras dos palabras no comunes que parecen nombres
        return palabras_capitalizadas[0], palabras_capitalizadas[1]
    
    return None, None

def separar_pdf_por_paginas(ruta_entrada, tipo_cert, nombre, apellido):
    """Separa un PDF en archivos individuales por página"""
    try:
        reader = PdfReader(ruta_entrada)
        total_paginas = len(reader.pages)
        
        # Si es una sola página, solo renombrar
        if total_paginas == 1:
            prefijo = "C_P" if tipo_cert == "promoción" else "C_M"
            nombre_completo = f"{nombre}_{apellido}".replace(" ", "_").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
            nombre_archivo = f"{prefijo}_{nombre_completo}.pdf"
            ruta_salida = CARPETA_PROCESADA / nombre_archivo
            
            writer = PdfWriter()
            writer.add_page(reader.pages[0])
            with open(ruta_salida, "wb") as f:
                writer.write(f)
            return [(ruta_salida, nombre_archivo)]
        
        # Si hay múltiples páginas, separar cada una
        archivos_creados = []
        for idx, pagina in enumerate(reader.pages, 1):
            prefijo = "C_P" if tipo_cert == "promoción" else "C_M"
            nombre_completo = f"{nombre}_{apellido}".replace(" ", "_").replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
            nombre_archivo = f"{prefijo}_{nombre_completo}_{idx}.pdf"
            ruta_salida = CARPETA_PROCESADA / nombre_archivo
            
            writer = PdfWriter()
            writer.add_page(pagina)
            with open(ruta_salida, "wb") as f:
                writer.write(f)
            archivos_creados.append((ruta_salida, nombre_archivo))
        
        return archivos_creados
    
    except Exception as e:
        print(f"Error al separar {ruta_entrada}: {e}")
        return []

def procesar_pdfs():
    """Función principal para procesar todos los PDFs"""
    archivos_pdf = list(CARPETA_PDF.glob("*.pdf"))
    
    print(f"Se encontraron {len(archivos_pdf)} archivos PDF\n")
    
    for idx, ruta_pdf in enumerate(archivos_pdf, 1):
        print(f"[{idx}/{len(archivos_pdf)}] Procesando: {ruta_pdf.name}")
        
        # Extraer texto
        textos = extraer_texto_pdf(ruta_pdf)
        if not textos:
            print("  ❌ No se pudo extraer texto del PDF\n")
            continue
        
        # Procesar la primera página para detectar tipo y nombre
        texto_primera_pagina = textos[0]
        tipo_cert = detectar_tipo_certificado(texto_primera_pagina)
        nombre, apellido = extraer_nombre_apellido(texto_primera_pagina)
        
        if not tipo_cert:
            print("  ⚠️ No se pudo detectar el tipo de certificado\n")
            continue
        
        if not nombre or not apellido:
            print("  ⚠️ No se pudo extraer nombre y apellido\n")
            continue
        
        print(f"  ✓ Tipo: {tipo_cert}")
        print(f"  ✓ Nombre: {nombre} {apellido}")
        print(f"  ✓ Total de páginas: {len(textos)}")
        
        # Separar y renombrar
        archivos_creados = separar_pdf_por_paginas(ruta_pdf, tipo_cert, nombre, apellido)
        print(f"  ✓ Archivos creados: {len(archivos_creados)}")
        
        for _, nombre_archivo in archivos_creados:
            print(f"    → {nombre_archivo}")
        
        print()

if __name__ == "__main__":
    procesar_pdfs()
    print("✅ Procesamiento completado!")
    print(f"Los archivos se encuentran en: {CARPETA_PROCESADA.absolute()}")
