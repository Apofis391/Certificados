import fitz
import easyocr
import cv2
import numpy as np
from pathlib import Path

# Inicializar OCR
print("Inicializando OCR...")
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)

# Leer el primer PDF
pdf_path = Path("pdf") / "20260327_110603.pdf"
print(f"\nLeyendo: {pdf_path.name}\n")

pdf_document = fitz.open(str(pdf_path))

# Ver las primeras 3 páginas
for page_num in range(min(3, len(pdf_document))):
    print(f"\n{'='*80}")
    print(f"PÁGINA {page_num + 1}")
    print(f"{'='*80}")
    
    pagina = pdf_document[page_num]
    pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    
    if pix.n == 4:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
    
    # Extraer texto
    resultados = ocr_reader.readtext(img_array, detail=1)  # detail=1 para ver confianza también
    
    # Mostrar todo el texto
    print("\nTEXTO EXTRAÍDO:")
    for (bbox, texto, confianza) in resultados:
        print(f"[{confianza:.2f}] {texto}")

pdf_document.close()
