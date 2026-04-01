import fitz
import easyocr
import cv2
import numpy as np
from pathlib import Path

# Inicializar OCR
print("Inicializando OCR...")
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)

# Leer el segundo PDF
pdf_path = Path("pdf") / "20260327_110724.pdf"
print(f"\nLeyendo: {pdf_path.name}")

pdf_document = fitz.open(str(pdf_path))

# Ver solo la página 2 (índice 1)
page_num = 1
print(f"\n[PAGINA {page_num + 1}]")

pagina = pdf_document[page_num]
pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

if pix.n == 4:
    img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)

# Extraer texto
resultados = ocr_reader.readtext(img_array, detail=0)
texto_completo = "\n".join(resultados)

print("\nTODO EL TEXTO:")
print(texto_completo)

print("\n\n=== BUSQUEDAS ===")
print("- 'PROMOCION' en texto:", "PROMOCION" in texto_completo.upper())
print("- 'MATRICULA' en texto:", "MATRICULA" in texto_completo.upper())
print("- 'MATRÍCULA' en texto:", "MATRÍCULA" in texto_completo.upper())

# Buscar lineas que contengan estas palabras
print("\n\nLineas con TIPO DE CERTIFICADO:")
for linea in texto_completo.split('\n'):
    if 'MOTIVO' in linea.upper() or 'CERTIFICADO' in linea.upper() or 'MATRICULA' in linea.upper() or 'MATRICUL' in linea.upper() or 'PROMOCION' in linea.upper():
        print(f"  > {linea}")

pdf_document.close()
