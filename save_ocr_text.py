import fitz
import easyocr
import cv2
import numpy as np
from pathlib import Path

# Inicializar OCR
ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)

# Leer el segundo PDF
pdf_path = Path("pdf") / "20260327_110724.pdf"
pdf_document = fitz.open(str(pdf_path))

# Procesar páginas 1 y 2 (índices 0 y 1)
for page_num in range(2):
    pagina = pdf_document[page_num]
    pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    
    if pix.n == 4:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
    
    # Extraer texto
    resultados = ocr_reader.readtext(img_array, detail=0)
    texto_completo = "\n".join(resultados)
    
    # Guardar en archivo
    with open(f"page_{page_num+1}_text.txt", "w", encoding="utf-8") as f:
        f.write(f"PAGE {page_num+1}\n")
        f.write(texto_completo)
    
    print(f"Pagina {page_num+1} guardada en page_{page_num+1}_text.txt")

pdf_document.close()
print("Listo. Ahora revisa los archivos generados.")
