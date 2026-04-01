import pdfplumber
from pathlib import Path

# Revisar los primeros dos PDFs para ver qué texto contienen
CARPETA_PDF = Path("pdf")
archivos = sorted(list(CARPETA_PDF.glob("*.pdf")))[:2]

for archivo in archivos:
    print(f"\n{'='*80}")
    print(f"Archivo: {archivo.name}")
    print(f"{'='*80}")
    
    try:
        with pdfplumber.open(archivo) as pdf:
            print(f"Total de páginas: {len(pdf.pages)}\n")
            
            for idx, pagina in enumerate(pdf.pages, 1):
                print(f"\n--- PÁGINA {idx} ---")
                texto = pagina.extract_text() or ""
                print(texto[:1500])  # Primeros 1500 caracteres
                print(f"\n... (total: {len(texto)} caracteres)")
    
    except Exception as e:
        print(f"Error: {e}")
