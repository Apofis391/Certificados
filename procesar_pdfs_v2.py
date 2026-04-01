import os
import re
from pathlib import Path
from PyPDF2 import PdfReader, PdfWriter
import fitz
import easyocr
import cv2
import numpy as np

# Configuración
CARPETA_PDF = Path("pdf")
CARPETA_PROCESADA = Path("pdf_procesados")

# Crear carpeta de salida si no existe
CARPETA_PROCESADA.mkdir(exist_ok=True)

# Inicializar OCR
print("Inicializando OCR...")
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)

def extraer_texto_pdf(ruta_pdf):
    """Extrae texto de todas las páginas de un PDF usando OCR con PyMuPDF"""
    textos = []
    try:
        pdf_document = fitz.open(str(ruta_pdf))
        
        for pagina_num in range(len(pdf_document)):
            pagina = pdf_document[pagina_num]
            
            # Renderizar página a imagen
            pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            
            if pix.n == 4:
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
            
            # Extraer texto con OCR
            resultados = ocr_reader.readtext(img_array, detail=0)
            texto = "\n".join(resultados)
            textos.append(texto)
        
        pdf_document.close()
            
    except Exception as e:
        print(f"Error al leer {ruta_pdf}: {e}")
    
    return textos

def detectar_tipo_certificado(texto):
    """Detecta si es promoción o matrícula"""
    import re
    
    texto_clean = texto.lower()
    # Remover caracteres especiales para mejor detección
    texto_clean = re.sub(r'[^\w\s]', '', texto_clean)
    
    # Contar ocurrencias de ambas palabras
    count_matricula = 0
    count_promocion = 0
    
    # Palabras clave para matrícula
    if re.search(r'(matricula|matriculado|matriculada)', texto_clean):
        count_matricula += 15
    if re.search(r'(inscripcion|inscrito)', texto_clean):
        count_matricula += 8
    if re.search(r'(costo.*matricula|arancel)', texto_clean):
        count_matricula += 5
    if re.search(r'(proximo.*semestre|próximo.*semestre)', texto_clean):
        count_matricula += 3
    
    # Palabras clave para promoción
    if re.search(r'(promocion|promovido|promovida)', texto_clean):
        count_promocion += 15
    if re.search(r'(aprobado|aprobada)', texto_clean):
        count_promocion += 5
    if re.search(r'(calificacion|nota)', texto_clean):
        count_promocion += 3
    
    # Retornar el que tenga más puntos
    if count_matricula > count_promocion and count_matricula > 0:
        return "matrícula"
    elif count_promocion > count_matricula and count_promocion > 0:
        return "promoción"
    elif count_matricula > 0:
        return "matrícula"
    elif count_promocion > 0:
        return "promoción"
    
    return None

def extraer_nombre_apellido(texto):
    """Extrae nombre y apellido buscando la línea 'Nombre: ' """
    lineas = texto.split('\n')
    
    # Buscar la línea que comienza con "Nombre:"
    for linea in lineas:
        if 'nombre:' in linea.lower():
            # Extraer todo después de "Nombre:"
            patron = r"nombre\s*:\s*(.+?)(?:\n|$|Cédula|cedula|carne|carné)"
            match = re.search(patron, linea, re.IGNORECASE)
            if match:
                nombre_completo = match.group(1).strip()
                # Dividir en partes
                partes = nombre_completo.split()
                
                if len(partes) >= 2:
                    # Buscar dónde termina el apellido y comienza el nombre
                    # Típicamente: APELLIDO1 APELLIDO2 NOMBRE1 NOMBRE2
                    # O: APELLIDO NOMBRE
                    # Vamos a tomar las últimas 2 palabras como nombre
                    # O si no hay como nombre/apellido, usar las dos primeras
                    
                    # Estrategia: si hay 2 palabras, son apellido y nombre
                    if len(partes) == 2:
                        apellido = partes[1]
                        nombre = partes[0]
                    # Si hay más de 2, típicamente últimas palabras son el nombre
                    elif len(partes) >= 3:
                        # Tomar las dos últimas que parecen ser el nombre del alumno
                        # O en este caso, parece ser que el formato es:
                        # APELLIDO1 APELLIDO2 NOMBRE1 NOMBRE2 (o similar)
                        # Pero mirando "BASTIDAS MORALES YUSETH DE LOS ANGELES"
                        # Es: Apellido Apellido Nombre Preposición Nombre
                        
                        # Nueva estrategia: buscar la palabra "DE" y considerar que todo después es nombre
                        tiene_de = False
                        for i, parte in enumerate(partes):
                            if parte.upper() == "DE":
                                tiene_de = True
                                # Todo lo anterior es apellido, todo después es nombre
                                apellido = " ".join(partes[:i])
                                nombre = " ".join(partes[i+1:])
                                break
                        
                        if not tiene_de:
                            # Si no tiene "DE", tomar primeras 2 como apellido, resto como nombre
                            apellido = " ".join(partes[:2])
                            nombre = " ".join(partes[2:])
                    
                    return nombre, apellido
    
    return None, None

def limpiar_nombre(nombre):
    """Limpia el nombre para usarlo en el archivo"""
    # Replicar letras acentuadas, espacios, etc.
    nombre = nombre.strip()
    # Reemplazar espacios múltiples con underscore
    nombre = re.sub(r'\s+', '_', nombre)
    # Remover caracteres especiales excepto guion bajo
    nombre = re.sub(r'[^a-zA-Z0-9áéíóúñÁÉÍÓÚÑ_]', '', nombre)
    return nombre

def obtener_numero_pagina_pdf(archivo_pdf_path, pagina_num):
    """Obtiene el número de página del PDF original"""
    try:
        reader = PdfReader(archivo_pdf_path)
        return pagina_num + 1
    except:
        return pagina_num + 1

def separar_pdf_por_paginas(ruta_entrada, tipo_cert, nombre, apellido):
    """Separa un PDF en archivos individuales por página"""
    try:
        reader = PdfReader(str(ruta_entrada))
        total_paginas = len(reader.pages)
        archivos_creados = []
        
        for idx, pagina in enumerate(reader.pages):
            prefijo = "C_P" if tipo_cert == "promoción" else "C_M"
            nombre_limpio = limpiar_nombre(nombre.replace(" ", "_"))
            apellido_limpio = limpiar_nombre(apellido.replace(" ", "_"))
            
            # Si es una sola página en el archivo original, no agregar número
            if total_paginas == 1:
                nombre_archivo = f"{prefijo}_{apellido_limpio}_{nombre_limpio}.pdf"
            else:
                # Si hay múltiples páginas, agregar número de página
                nombre_archivo = f"{prefijo}_{apellido_limpio}_{nombre_limpio}_{idx + 1:02d}.pdf"
            
            ruta_salida = CARPETA_PROCESADA / nombre_archivo
            
            # Evitar duplicados
            contador = 1
            while ruta_salida.exists():
                base_nombre = nombre_archivo.rsplit('.', 1)[0]
                nombre_archivo = f"{base_nombre}_dup{contador}.pdf"
                ruta_salida = CARPETA_PROCESADA / nombre_archivo
                contador += 1
            
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
    archivos_pdf = sorted(list(CARPETA_PDF.glob("*.pdf")))
    
    print(f"Se encontraron {len(archivos_pdf)} archivos PDF\n")
    
    for idx, ruta_pdf in enumerate(archivos_pdf, 1):
        print(f"[{idx}/{len(archivos_pdf)}] Procesando: {ruta_pdf.name}")
        
        # Extraer texto de todas las páginas
        textos = extraer_texto_pdf(ruta_pdf)
        total_certificados_creados = 0
        
        if not textos:
            print("  ❌ No se pudo extraer texto del PDF\n")
            continue
        
        # Procesar cada página del PDF (cada una es potencialmente un certificado)
        for pagina_num, texto_pagina in enumerate(textos):
            # Detectar tipo de certificado
            tipo_cert = detectar_tipo_certificado(texto_pagina)
            nombre, apellido = extraer_nombre_apellido(texto_pagina)
            
            if not tipo_cert or not nombre or not apellido:
                # Si no se pudieron extraer datos, saltar
                continue
            
            # Separar solo la página actual del PDF
            try:
                reader = PdfReader(str(ruta_pdf))
                writer = PdfWriter()
                writer.add_page(reader.pages[pagina_num])
                
                prefijo = "C_P" if tipo_cert == "promoción" else "C_M"
                nombre_limpio = limpiar_nombre(nombre.replace(" ", "_"))
                apellido_limpio = limpiar_nombre(apellido.replace(" ", "_"))
                nombre_archivo = f"{prefijo}_{apellido_limpio}_{nombre_limpio}.pdf"
                
                ruta_salida = CARPETA_PROCESADA / nombre_archivo
                
                # Evitar duplicados
                contador = 1
                while ruta_salida.exists():
                    base_nombre = nombre_archivo.rsplit('.', 1)[0]
                    nombre_archivo = f"{base_nombre}_dup{contador}.pdf"
                    ruta_salida = CARPETA_PROCESADA / nombre_archivo
                    contador += 1
                
                with open(ruta_salida, "wb") as f:
                    writer.write(f)
                
                total_certificados_creados += 1
                print(f"  ✓ Página {pagina_num + 1}: {prefijo}_{nombre_limpio} {apellido_limpio}")
            
            except Exception as e:
                print(f"  ❌ Error procesando página {pagina_num + 1}: {e}")
        
        if total_certificados_creados > 0:
            print(f"  ✓ Total certificados creados: {total_certificados_creados}")
        else:
            print(f"  ⚠️ No se pudieron extraer certificados de este archivo")
        
        print()

if __name__ == "__main__":
    procesar_pdfs()
    print("✅ Procesamiento completado!")
    print(f"Los archivos se encuentran en: {CARPETA_PROCESADA.absolute()}")
