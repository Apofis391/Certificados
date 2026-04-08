#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script optimizado para procesar PDFs mixtos de certificados
Detecta y separa: C_P (Promoción) y C_M (Matrícula)
"""

import re
import json
import argparse
import warnings
import contextlib
import io
import sys
import hashlib
from datetime import datetime
from pathlib import Path
import fitz
import easyocr
import cv2
import numpy as np
import unicodedata

# Evitar UnicodeEncodeError en consolas Windows cuando el encoding es cp1252
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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

def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def migrar_src_en_raiz(*, delete_identical: bool = True) -> int:
    """Renombra salidas legacy con '_SRC<pdf_id>_' en la raíz de pdf_procesados/.

    Ej: C_M_SRC20260407_142728_ABAD_..._PAG04.pdf -> C_M_ABAD_..._PAG04.pdf

    - No crea subcarpetas.
    - Si el destino ya existe:
      - si es idéntico (hash igual) y delete_identical=True, borra el SRC.
      - si difiere, no toca nada (evita perder información) y lo reporta.

    Devuelve la cantidad de archivos SRC renombrados o eliminados por ser idénticos.
    """
    try:
        CARPETA_PROCESADA.mkdir(exist_ok=True)
    except Exception:
        pass

    moved_or_deleted = 0
    patron = "*_SRC*_PAG??.pdf"
    candidatos = list(CARPETA_PROCESADA.glob(patron))
    if not candidatos:
        return 0

    print(f"🧹 Migrando nombres legacy SRC en raíz: {len(candidatos)}")
    for src_path in candidatos:
        try:
            name = src_path.name
            m = re.search(r"_SRC([^_]+)_", name, flags=re.IGNORECASE)
            if not m:
                continue
            pdf_id = m.group(1)
            token = f"_SRC{pdf_id}_"
            if token not in name:
                # fallback por diferencias de mayúsculas
                token_ci = re.search(r"_SRC" + re.escape(pdf_id) + r"_", name, flags=re.IGNORECASE)
                if not token_ci:
                    continue
                token = token_ci.group(0)

            new_name = name.replace(token, "_", 1)
            dst_path = CARPETA_PROCESADA / new_name

            if dst_path.exists():
                if delete_identical:
                    try:
                        if _sha256_file(src_path) == _sha256_file(dst_path):
                            src_path.unlink()
                            moved_or_deleted += 1
                            continue
                    except Exception:
                        pass
                print(f"  ⚠️ Conflicto SRC (no se toca): {src_path.name} (ya existe {dst_path.name})")
                continue

            try:
                src_path.rename(dst_path)
                moved_or_deleted += 1
            except Exception as ex:
                print(f"  ⚠️ No se pudo renombrar {src_path.name}: {ex}")
        except Exception:
            continue

    return moved_or_deleted

def _output_exists(rel_path: str) -> bool:
    """Devuelve True si existe el output, ya sea en la ruta relativa guardada
    (p.ej. 'pdf_id/archivo.pdf') o en la raíz de pdf_procesados/ (fallback por basename).

    Esto permite reconocer salidas aunque hayan sido movidas a la raíz.
    """
    if not rel_path:
        return False
    try:
        p = CARPETA_PROCESADA / rel_path
        if p.exists():
            return True
    except Exception:
        pass

    try:
        base = Path(rel_path).name
        if base and (CARPETA_PROCESADA / base).exists():
            return True
    except Exception:
        pass
    return False

def _normalize_output_rel(rel_path: str) -> str:
    """Si rel_path no existe pero su basename sí existe en la raíz, devuelve basename."""
    if not rel_path:
        return rel_path
    if _output_exists(rel_path):
        # Preferir la ruta exacta si existe
        try:
            if (CARPETA_PROCESADA / rel_path).exists():
                return rel_path
        except Exception:
            pass
        return Path(rel_path).name
    return rel_path

def get_ocr_reader():
    global ocr_reader
    if ocr_reader is None:
        print("🔄 Inicializando OCR...")
        with contextlib.redirect_stderr(io.StringIO()):
            ocr_reader = easyocr.Reader(['es', 'en'], gpu=False)
        print("✅ OCR listo\n")
    return ocr_reader

def _page_index_from_filename(nombre_archivo: str):
    """Devuelve índice 0-based según sufijo _PAG##.pdf, o None si no coincide."""
    m = re.search(r"_PAG(\d{2})\.pdf$", str(nombre_archivo), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1)) - 1
    except Exception:
        return None

def _detectar_outputs_en_subdir(pdf_id: str):
    """Busca PDFs separados en pdf_procesados/<pdf_id>/ y arma page_outputs relativos."""
    carpeta = CARPETA_PROCESADA / pdf_id
    if not carpeta.exists() or not carpeta.is_dir():
        return {}

    out = {}
    try:
        for p in carpeta.glob("*.pdf"):
            idx = _page_index_from_filename(p.name)
            if idx is None:
                continue
            # Ruta relativa guardada en registro (para que CARPETA_PROCESADA / rel exista)
            out[str(idx)] = f"{pdf_id}/{p.name}"
    except Exception:
        return {}
    return out

def _detectar_outputs_v2_src(pdf_id: str):
    """Detecta PDFs separados con esquema viejo: C_*_SRC<pdf_id>_*_PAG##.pdf en pdf_procesados/."""
    out = {}
    try:
        patron = f"*_SRC{pdf_id}_*_PAG??.pdf"
        for p in CARPETA_PROCESADA.glob(patron):
            idx = _page_index_from_filename(p.name)
            if idx is None:
                continue
            out[str(idx)] = p.name
    except Exception:
        return {}
    return out

def _migrar_v2_src_a_subdir(pdf_id: str, page_outputs: dict[str, str]):
    """Mueve/renombra salidas v2-src a pdf_procesados/<pdf_id>/ removiendo el segmento '_SRC<pdf_id>_'.

    Devuelve un nuevo dict page_outputs con rutas relativas a subdir cuando se logra.
    """
    carpeta = CARPETA_PROCESADA / pdf_id
    try:
        carpeta.mkdir(exist_ok=True)
    except Exception:
        return page_outputs

    migrated = {}
    token = f"_SRC{pdf_id}_"
    for k, rel in (page_outputs or {}).items():
        try:
            src_path = CARPETA_PROCESADA / rel
            if not src_path.exists():
                migrated[k] = rel
                continue

            new_name = src_path.name.replace(token, "_", 1)
            dst_path = carpeta / new_name
            # Si ya existe el destino, no sobreescribir
            if dst_path.exists():
                migrated[k] = f"{pdf_id}/{dst_path.name}"
                continue

            try:
                src_path.rename(dst_path)
                migrated[k] = f"{pdf_id}/{dst_path.name}"
            except Exception:
                migrated[k] = rel
        except Exception:
            migrated[k] = rel
    return migrated

def _migrar_v2_src_a_root(pdf_id: str, page_outputs: dict[str, str]):
    """Renombra salidas v2-src en la RAÍZ de pdf_procesados/ removiendo '_SRC<pdf_id>_'.

    Devuelve un nuevo dict page_outputs con nombres en raíz cuando se logra.
    """
    migrated: dict[str, str] = {}
    token = f"_SRC{pdf_id}_"
    for k, rel in (page_outputs or {}).items():
        try:
            src_path = CARPETA_PROCESADA / rel
            if not src_path.exists():
                migrated[k] = rel
                continue

            new_name = src_path.name.replace(token, "_", 1)
            dst_path = CARPETA_PROCESADA / new_name

            if dst_path.exists():
                migrated[k] = dst_path.name
                continue

            try:
                src_path.rename(dst_path)
                migrated[k] = dst_path.name
            except Exception:
                migrated[k] = rel
        except Exception:
            migrated[k] = rel
    return migrated

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
        try:
            ARCHIVO_REGISTRO.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        with open(ARCHIVO_REGISTRO, 'w') as f:
            json.dump(registro, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error guardando registro: {e}")

def limpiar_nombre_archivo(texto):
    """Convierte texto a formato válido para nombre de archivo"""
    if not texto:
        return ""
    # Preservar ñ y tildes (Windows soporta Unicode en nombres de archivo)
    texto = unicodedata.normalize('NFC', str(texto))
    texto = texto.strip().upper()
    # Normalizar espacios a underscore
    texto = re.sub(r"\s+", "_", texto)
    # Quitar caracteres inválidos para Windows: <>:"/\|?* y controles
    texto = re.sub(r"[<>:\"/\\|?*]", "", texto)
    texto = "".join(ch for ch in texto if ch.isprintable())
    # Mantener solo letras/dígitos/underscore (\w incluye letras Unicode), más Ñ/á etc.
    texto = re.sub(r"[^\w_]+", "", texto, flags=re.UNICODE)
    # Evitar underscores múltiples
    texto = re.sub(r"_+", "_", texto).strip("_")

    # Evitar rutas demasiado largas en Windows (y salidas con basura de OCR)
    # Mantenerlo conservador para que "..._PAG##.pdf" siempre quepa.
    max_len = 80
    if len(texto) > max_len:
        texto = texto[:max_len].rstrip("_")
    return texto

def _nombre_en_lista_esperados(nombre_archivo: str, esperados: list[dict]) -> bool:
    """Devuelve True si el archivo generado (p.ej. C_P_..._PAG01.pdf) puede corresponder
    a alguna fila de pdf_generados_dev.txt (exact o fuzzy)."""
    if not nombre_archivo or not esperados:
        return False

    # Exacto: fila contiene nombre .pdf
    for it in esperados:
        if it.get("kind") == "exact" and (it.get("name") or "").lower() == nombre_archivo.lower():
            return True

    info = _pdf_tokens_desde_nombre(nombre_archivo)
    if not info:
        return False

    tipo = info.get("tipo")
    tokens = info.get("tokens") or []

    # Fuzzy: primer apellido + primer nombre
    for it in esperados:
        if it.get("kind") != "fuzzy":
            continue
        if it.get("tipo") != tipo:
            continue
        apellido1 = it.get("apellido1")
        nombre1 = it.get("nombre1")
        if not apellido1 or not nombre1:
            continue
        ok_ap = any(_token_match(apellido1, t) for t in tokens)
        ok_no = any(_token_match(nombre1, t) for t in tokens)
        if ok_ap and ok_no:
            return True

    return False

def dedupe_pdf_procesados_por_lista(*, delete: bool = False, yes: bool = False, root: Path = CARPETA_PROCESADA) -> int:
    """Elimina duplicados exactos (mismo nombre de archivo en varias rutas) PERO
    solo para archivos que aparecen (o matchean) en pdf_generados_dev.txt.

    Además, escribe un log de lo eliminado.
    """
    esperados = cargar_esperados_desde_txt(ARCHIVO_ESPERADOS)
    if not esperados:
        print(f"ℹ️  No hay entradas válidas en {ARCHIVO_ESPERADOS.name}. No se puede deduplicar por lista.")
        return 0

    try:
        all_pdfs = [p for p in root.rglob("*.pdf") if p.is_file() and _es_pdf_con_pagina(p.name)]
    except Exception:
        all_pdfs = []

    by_name: dict[str, list[Path]] = {}
    for p in all_pdfs:
        by_name.setdefault(p.name, []).append(p)

    # Duplicados exactos por nombre
    dups = {n: ps for n, ps in by_name.items() if len(ps) > 1}

    # Duplicados por persona ignorando PAG (mismo C_<TIPO>_<APELLIDO>_<NOMBRE> con PAG diferente)
    by_persona: dict[str, list[Path]] = {}
    for p in all_pdfs:
        k = _persona_key_desde_nombre(p.name)
        if not k:
            continue
        by_persona.setdefault(k, []).append(p)
    persona_dups = {k: ps for k, ps in by_persona.items() if len(ps) > 1}

    # Filtrar a los que están en la lista de esperados
    dups_filtrados = {n: ps for n, ps in dups.items() if _nombre_en_lista_esperados(n, esperados)}
    persona_dups_filtrados = {
        k: ps for k, ps in persona_dups.items()
        if any(_nombre_en_lista_esperados(p.name, esperados) for p in ps)
    }

    if not dups_filtrados and not persona_dups_filtrados:
        if not dups and not persona_dups:
            print("✅ No hay duplicados (por nombre ni por persona) según lista.")
        else:
            print("✅ Hay duplicados, pero ninguno aparece/matchea en la lista de esperados. No se elimina nada.")
        return 0

    log_path = root / "duplicados_eliminados_por_lista.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    deleted = 0
    modo = "APLICADO" if (delete and yes) else "REPORTE"
    total_claves = len(dups_filtrados) + len(persona_dups_filtrados)
    if modo == "APLICADO":
        print(f"\n🧹 Duplicados (según lista) detectados: {total_claves} (se eliminarán)")
    else:
        print(f"\n🧹 Duplicados (según lista) detectados: {total_claves} (modo reporte; no se elimina)")

    try:
        f_log = open(log_path, "a", encoding="utf-8")
        f_log.write(
            f"\n[{ts}] DEDUPE POR LISTA ({modo}): {total_claves} claves (nombre={len(dups_filtrados)}, persona={len(persona_dups_filtrados)})\n"
        )
    except Exception:
        f_log = None

    for filename in sorted(dups_filtrados.keys()):
        paths = dups_filtrados[filename]
        keep = _dedupe_prefer_path(root, paths)
        print(f"  🔁 {filename} (x{len(paths)})")
        if f_log:
            f_log.write(f"- {filename} (x{len(paths)})\n")

        for p in sorted(paths, key=lambda x: str(x)):
            tag = "KEEP" if p == keep else "DEL"
            print(f"    - {tag} {p}")
            if f_log:
                f_log.write(f"    - {tag} {p}\n")

        if delete:
            if not yes:
                print("    ⚠️  No se eliminó nada: falta --yes")
                if f_log:
                    f_log.write("    - SKIP (falta --yes)\n")
                continue
            for p in paths:
                if p == keep:
                    continue
                try:
                    p.unlink()
                    deleted += 1
                except Exception as ex:
                    print(f"    ❌ No se pudo eliminar {p}: {ex}")
                    if f_log:
                        f_log.write(f"    - ERROR eliminando {p}: {ex}\n")

    # Duplicados por persona ignorando PAG
    for persona_key in sorted(persona_dups_filtrados.keys()):
        paths = persona_dups_filtrados[persona_key]
        keep = _prefer_keep_persona(root, paths)
        print(f"  🔁 {persona_key} (ignora PAG) (x{len(paths)})")
        if f_log:
            f_log.write(f"- {persona_key} (ignora PAG) (x{len(paths)})\n")

        for p in sorted(paths, key=lambda x: str(x)):
            tag = "KEEP" if p == keep else "DEL"
            pn = _page_num_desde_nombre(p.name)
            print(f"    - {tag} {p} (PAG={pn})")
            if f_log:
                f_log.write(f"    - {tag} {p} (PAG={pn})\n")

        if delete:
            if not yes:
                print("    ⚠️  No se eliminó nada: falta --yes")
                if f_log:
                    f_log.write("    - SKIP (falta --yes)\n")
                continue
            for p in paths:
                if p == keep:
                    continue
                try:
                    p.unlink()
                    deleted += 1
                except Exception as ex:
                    print(f"    ❌ No se pudo eliminar {p}: {ex}")
                    if f_log:
                        f_log.write(f"    - ERROR eliminando {p}: {ex}\n")

    if f_log:
        if modo == "APLICADO":
            f_log.write(f"\nEliminados: {deleted} (aplicado)\n")
        else:
            f_log.write(f"\nEliminados: {deleted} (modo reporte; use --dedupe-delete --yes para aplicar)\n")
        f_log.close()

    if delete and yes:
        print(f"✅ Deduplicación por lista completada. Archivos eliminados: {deleted}")
        print(f"📝 Log: {log_path}\n")
    else:
        print(f"ℹ️  Para eliminar estos duplicados: python procesar.py --solo-validar --dedupe-por-lista --dedupe-delete --yes")
        print(f"📝 Log: {log_path}\n")

    return deleted

def _norm_token_para_match(texto: str) -> str:
    """Normaliza una palabra para comparar contra tokens de nombres en archivos."""
    t = normalizar_texto(texto)
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t.upper()

def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            ins = curr[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(ins, dele, sub))
        prev = curr
    return prev[-1]

def _token_match(expected: str, candidate: str, *, max_edit: int = 1, max_extra: int = 2) -> bool:
    e = _norm_token_para_match(expected)
    c = _norm_token_para_match(candidate)
    if not e or not c:
        return False
    if e == c:
        return True

    # Permitir 1-2 letras de más/menos
    if e in c and (len(c) - len(e)) <= max_extra:
        return True
    if c in e and (len(e) - len(c)) <= max_extra:
        return True

    if abs(len(e) - len(c)) > max_edit:
        return False
    return _levenshtein(e, c) <= max_edit

def _pdf_tokens_desde_nombre(nombre_archivo: str):
    """Extrae tokens comparables desde un nombre tipo C_P_..._PAG01.pdf"""
    m = re.match(r"^C_([PM])_(.+)\.pdf$", nombre_archivo, flags=re.IGNORECASE)
    if not m:
        return None
    tipo = m.group(1).upper()
    resto = m.group(2)
    raw_tokens = resto.split("_")
    tokens = []
    for t in raw_tokens:
        if not t:
            continue
        up = t.upper()
        if re.fullmatch(r"PAG\d+", up):
            continue
        if up == "CDULA":
            continue
        if up.isdigit():
            continue
        tokens.append(up)
    return {"tipo": tipo, "tokens": tokens, "name": nombre_archivo}

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
    - Una fila tipo: 'CERTIFICADO DE PROMOCIÓN/MATRÍCULA - ...'
      (se valida por match *fuzzy* del primer apellido + primer nombre)
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

        # Extraer palabras (incluye tildes/ñ) y tomar primer apellido + primer nombre (nombres al final)
        palabras = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", nombre_parte)
        if len(palabras) < 2:
            continue
        primer_apellido = palabras[0]
        if len(palabras) >= 4:
            primer_nombre = palabras[-2]
        elif len(palabras) == 3:
            primer_nombre = palabras[-1]
        else:
            primer_nombre = palabras[1]

        esperados.append({
            "kind": "fuzzy",
            "tipo": tipo,
            "apellido1": primer_apellido,
            "nombre1": primer_nombre,
            "raw": linea,
            "display": f"C_{tipo}_*_{primer_apellido}_{primer_nombre}_*.pdf",
        })

    return esperados

def _dedupe_esperados(esperados: list[dict]) -> tuple[list[dict], list[dict]]:
    """Elimina duplicados del listado esperado.

    En la práctica el archivo pdf_generados_dev.txt suele tener filas repetidas.
    Por defecto, la validación debe tratar esas repeticiones como duplicados (no como
    requerimientos adicionales de impresión).

    Retorna: (unicos, duplicados).
    """
    seen: set[tuple] = set()
    unicos: list[dict] = []
    duplicados: list[dict] = []

    for it in esperados or []:
        kind = it.get("kind")
        if kind == "exact":
            key = ("exact", (it.get("name") or "").strip().lower())
        else:
            # fuzzy
            key = (
                "fuzzy",
                (it.get("tipo") or ""),
                _norm_token_para_match(it.get("apellido1") or ""),
                _norm_token_para_match(it.get("nombre1") or ""),
            )

        if key in seen:
            duplicados.append(it)
            continue
        seen.add(key)
        unicos.append(it)

    return unicos, duplicados

def _es_pdf_con_pagina(nombre_archivo: str) -> bool:
    return bool(re.search(r"(?i)_PAG\d{2}\.pdf$", str(nombre_archivo)))

def _dedupe_prefer_path(root: Path, paths: list[Path]) -> Path:
    """Elige qué archivo conservar cuando hay duplicados exactos (mismo nombre).

    Regla: preferir el que está en la RAÍZ de pdf_procesados/.

    Motivo: el usuario quiere trabajar sin subcarpetas cuando sea posible.
    """
    def score(p: Path):
        try:
            rel = p.relative_to(root)
            depth = len(rel.parts)
        except Exception:
            depth = 1
        # depth==1 significa raíz
        in_root = 1 if depth == 1 else 0
        try:
            mtime = p.stat().st_mtime
        except Exception:
            mtime = 0
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        # preferir raíz, luego más nuevo, luego más grande, luego ruta estable
        return (in_root, mtime, size, str(p))

    return sorted(paths, key=score, reverse=True)[0]

def _prefer_keep_persona(root: Path, paths: list[Path]) -> Path:
    """Elige qué archivo conservar dentro de un grupo por persona (ignorando PAG).

    Preferencias:
    - Raíz de pdf_procesados/
    - Menor número de PAG
    - Más nuevo
    - Más grande
    """
    def key(p: Path):
        try:
            rel = p.relative_to(root)
            depth = len(rel.parts)
        except Exception:
            depth = 999
        in_root = 1 if depth == 1 else 0
        pag = _page_num_desde_nombre(p.name)
        pag_sort = pag if pag is not None else 999
        try:
            st = p.stat()
            mtime = st.st_mtime
            size = st.st_size
        except Exception:
            mtime = 0
            size = 0

        # Orden ascendente: primero lo "mejor"
        return (-in_root, pag_sort, -mtime, -size, str(p))

    return sorted(paths, key=key)[0]

def _persona_key_desde_nombre(nombre_archivo: str) -> str | None:
    """Devuelve una clave estable para agrupar ignorando PAG.

    Ej: C_M_ARMIJOS_POVEDA_ANA_MABEL_PAG07.pdf -> C_M_ARMIJOS_POVEDA_ANA_MABEL
    """
    if not nombre_archivo:
        return None
    m = re.match(r"^(C_[PMX]_.+)_PAG\d{2}\.pdf$", nombre_archivo, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).upper()

def _page_num_desde_nombre(nombre_archivo: str) -> int | None:
    m = re.search(r"_PAG(\d{2})\.pdf$", nombre_archivo, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def _dhash_from_pdf(path: Path, *, hash_size: int = 8) -> str:
    """Hash perceptual (dHash) renderizando la primera página.

    Útil para detectar duplicados "iguales" aunque el PDF difiera en bytes o el nombre cambie.
    """
    doc = fitz.open(str(path))
    try:
        page = doc[0]
        # render pequeño para que sea rápido
        pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
        elif pix.n == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)
        diff = resized[:, 1:] > resized[:, :-1]
        # Convertir a hex
        bits = 0
        for v in diff.flatten():
            bits = (bits << 1) | int(bool(v))
        width = hash_size * hash_size
        return f"{bits:0{width // 4}x}"
    finally:
        doc.close()

def dedupe_pdf_procesados_por_persona(*, delete: bool = False, yes: bool = False, root: Path = CARPETA_PROCESADA, force: bool = False) -> int:
    """Deduplicación ignorando PAG##.

    Caso: hay 2+ PDFs del mismo tipo/persona, pero con distinto PAG (ej. PAG04 y PAG15).

    - Por defecto (force=False): solo elimina si el contenido es visualmente idéntico (dHash igual).
    - Con force=True: deja solo 1 por persona aunque el dHash difiera (más agresivo).
    """
    try:
        all_pdfs = [p for p in root.rglob("*.pdf") if p.is_file() and _es_pdf_con_pagina(p.name)]
    except Exception:
        all_pdfs = []

    by_persona: dict[str, list[Path]] = {}
    for p in all_pdfs:
        key = _persona_key_desde_nombre(p.name)
        if not key:
            continue
        by_persona.setdefault(key, []).append(p)

    grupos = {k: ps for k, ps in by_persona.items() if len(ps) > 1}
    if not grupos:
        print("✅ No hay duplicados por persona (ignorando PAG).")
        return 0

    log_path = root / "duplicados_eliminados_por_persona.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        f_log = open(log_path, "a", encoding="utf-8")
        f_log.write(f"\n[{ts}] DEDUPE POR PERSONA (apply={delete and yes}, force={force}) grupos={len(grupos)}\n")
    except Exception:
        f_log = None

    print(f"\n🧹 Duplicados por PERSONA detectados: {len(grupos)} (ignorando PAG)")
    deleted = 0

    for persona_key in sorted(grupos.keys()):
        paths = grupos[persona_key]
        # Para decidir, preferir raíz y menor PAG
        keep_default = _prefer_keep_persona(root, paths)
        print(f"  🔁 {persona_key} (x{len(paths)})")
        if f_log:
            f_log.write(f"- {persona_key} (x{len(paths)})\n")

        if force:
            keep = keep_default
            for p in sorted(paths, key=lambda x: str(x)):
                tag = "KEEP" if p == keep else "DEL"
                pn = _page_num_desde_nombre(p.name)
                print(f"    - {tag} {p} (PAG={pn})")
                if f_log:
                    f_log.write(f"    - {tag} {p} (PAG={pn})\n")

            if delete:
                if not yes:
                    print("    ⚠️  No se eliminó nada: falta --yes")
                    if f_log:
                        f_log.write("    - SKIP (falta --yes)\n")
                    continue
                for p in paths:
                    if p == keep:
                        continue
                    try:
                        p.unlink()
                        deleted += 1
                    except Exception as ex:
                        print(f"    ❌ No se pudo eliminar {p}: {ex}")
                        if f_log:
                            f_log.write(f"    - ERROR eliminando {p}: {ex}\n")
            continue

        # Modo seguro: agrupar por dHash dentro de la misma persona
        by_hash: dict[str, list[Path]] = {}
        for p in paths:
            try:
                h = _dhash_from_pdf(p)
            except Exception:
                h = "ERROR"
            by_hash.setdefault(h, []).append(p)

        # Solo considerar duplicados si el hash coincide y hay 2+
        dup_hashes = {h: ps for h, ps in by_hash.items() if h != "ERROR" and len(ps) > 1}
        if not dup_hashes:
            print("    ℹ️  No se borró nada: hashes distintos (usa --dedupe-persona-force si quieres dejar solo 1).")
            if f_log:
                f_log.write("    - INFO: hashes distintos, no se elimina (modo seguro)\n")
            continue

        for h, ps in sorted(dup_hashes.items(), key=lambda x: x[0]):
            keep = _prefer_keep_persona(root, ps)
            print(f"    🧩 HASH {h[:10]}... (x{len(ps)})")
            if f_log:
                f_log.write(f"    - HASH {h} (x{len(ps)})\n")
            for p in sorted(ps, key=lambda x: str(x)):
                tag = "KEEP" if p == keep else "DEL"
                pn = _page_num_desde_nombre(p.name)
                print(f"      - {tag} {p} (PAG={pn})")
                if f_log:
                    f_log.write(f"      - {tag} {p} (PAG={pn})\n")
            if delete:
                if not yes:
                    print("      ⚠️  No se eliminó nada: falta --yes")
                    if f_log:
                        f_log.write("      - SKIP (falta --yes)\n")
                    continue
                for p in ps:
                    if p == keep:
                        continue
                    try:
                        p.unlink()
                        deleted += 1
                    except Exception as ex:
                        print(f"      ❌ No se pudo eliminar {p}: {ex}")
                        if f_log:
                            f_log.write(f"      - ERROR eliminando {p}: {ex}\n")

    if f_log:
        f_log.write(f"Eliminados: {deleted}\n")
        f_log.close()

    if delete and yes:
        print(f"✅ Deduplicación por persona completada. Archivos eliminados: {deleted}")
    else:
        print("ℹ️  Para eliminar: python procesar.py --solo-validar --dedupe-persona --dedupe-delete --yes")
        print("ℹ️  Para forzar (agresivo): python procesar.py --solo-validar --dedupe-persona --dedupe-persona-force --dedupe-delete --yes")
    print(f"📝 Log: {log_path}\n")

    return deleted

def dedupe_pdf_procesados_por_hash(*, delete: bool = False, yes: bool = False, root: Path = CARPETA_PROCESADA) -> int:
    """Deduplicación por contenido (hash).

    Detecta PDFs con bytes idénticos aunque tengan distinto nombre o estén en subcarpetas.
    Es una limpieza segura porque solo elimina copias exactas.
    """
    try:
        all_pdfs = [p for p in root.rglob("*.pdf") if p.is_file() and _es_pdf_con_pagina(p.name)]
    except Exception:
        all_pdfs = []

    if not all_pdfs:
        return 0

    # Primero agrupar por tamaño para evitar hashear todo innecesariamente.
    by_size: dict[int, list[Path]] = {}
    for p in all_pdfs:
        try:
            by_size.setdefault(int(p.stat().st_size), []).append(p)
        except Exception:
            continue

    candidates = [ps for ps in by_size.values() if len(ps) > 1]
    if not candidates:
        return 0

    by_hash: dict[tuple[int, str], list[Path]] = {}
    for group in candidates:
        for p in group:
            try:
                st = p.stat()
                key = (int(st.st_size), _sha256_file(p))
                by_hash.setdefault(key, []).append(p)
            except Exception:
                continue

    dups = {k: ps for k, ps in by_hash.items() if len(ps) > 1}
    if not dups:
        return 0

    print(f"\n🧹 Duplicados por HASH detectados: {len(dups)} (copias exactas por contenido)")
    deleted = 0
    for (size, h), paths in sorted(dups.items(), key=lambda x: (x[0][0], x[0][1])):
        keep = _dedupe_prefer_path(root, paths)
        print(f"  🔁 HASH {h[:10]}... size={size} (x{len(paths)})")
        for p in sorted(paths, key=lambda x: str(x)):
            tag = "✅ KEEP" if p == keep else "🗑️  DEL"
            print(f"    - {tag} {p}")
        if delete:
            if not yes:
                print("    ⚠️  No se eliminó nada: falta --yes")
                continue
            for p in paths:
                if p == keep:
                    continue
                try:
                    p.unlink()
                    deleted += 1
                except Exception as ex:
                    print(f"    ❌ No se pudo eliminar {p}: {ex}")

    if delete and yes:
        print(f"✅ Deduplicación por hash completada. Archivos eliminados: {deleted}\n")
    else:
        print("ℹ️  Para eliminar estos duplicados por hash: python procesar.py --solo-validar --dedupe-hash --dedupe-delete --yes\n")

    return deleted

def reparar_nombres_incompletos_por_ocr(*, apply: bool = False, root: Path = CARPETA_PROCESADA) -> int:
    """Repara nombres incompletos como 'C_M_ARMIJOS_POVEDA_PAG07.pdf' usando OCR.

    Criterio de "incompleto": el nombre tiene <= 2 tokens de persona (solo apellidos) o contiene SIN_NOMBRE.
    """
    try:
        all_pdfs = [p for p in root.rglob("*.pdf") if p.is_file() and _es_pdf_con_pagina(p.name)]
    except Exception:
        all_pdfs = []

    targets: list[Path] = []
    for p in all_pdfs:
        name = p.name
        if "SIN_NOMBRE" in name.upper():
            targets.append(p)
            continue
        info = _pdf_tokens_desde_nombre(name)
        if not info:
            continue
        if len(info.get("tokens") or []) <= 2:
            targets.append(p)

    if not targets:
        print("✅ No se encontraron PDFs con nombres incompletos para reparar.")
        return 0

    log_path = root / "reparaciones_nombres.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = 0

    try:
        f_log = open(log_path, "a", encoding="utf-8")
        f_log.write(f"\n[{ts}] REPARAR NOMBRES (apply={apply})\n")
        f_log.write(f"Targets detectados: {len(targets)}\n")
        for t in sorted(targets, key=lambda x: str(x)):
            f_log.write(f"- TARGET {t}\n")
    except Exception:
        f_log = None

    print(f"\n🛠️  Reparación por OCR: {len(targets)} candidatos")
    for p in sorted(targets, key=lambda x: str(x)):
        try:
            info_nombre = _pdf_tokens_desde_nombre(p.name) or {}
            tokens_nombre = info_nombre.get("tokens") or []
            force_full = ("SIN_NOMBRE" in p.name.upper()) or (len(tokens_nombre) <= 2)

            # OCR: superior primero, si no alcanza, full
            doc = fitz.open(str(p))
            try:
                texto_sup = extraer_texto_pagina_ocr(doc, 0, solo_superior=True)
                texto = texto_sup
                tipo = detectar_tipo_certificado(texto)
                nombre, apellido = extraer_nombre_apellido(texto)
                if force_full or (not tipo) or (not nombre) or (not apellido):
                    texto_full = extraer_texto_pagina_ocr(doc, 0, solo_superior=False)
                    if texto_full:
                        texto = texto_full
                    # Recalcular en base al texto completo (especialmente cuando el nombre era incompleto)
                    tipo = detectar_tipo_certificado(texto) or tipo
                    if force_full:
                        nombre, apellido = extraer_nombre_apellido(texto)
                    else:
                        if (not nombre) or (not apellido):
                            nombre, apellido = extraer_nombre_apellido(texto)
            finally:
                doc.close()

            if not nombre or not apellido:
                print(f"  ⚠️  SKIP (sin nombre/apellido OCR): {p}")
                if f_log:
                    f_log.write(f"- SKIP (sin nombre/apellido OCR): {p}\n")
                continue

            # Mantener número de página original
            m_pag = re.search(r"_PAG(\d{2})\.pdf$", p.name, flags=re.IGNORECASE)
            pag_num = 1
            if m_pag:
                try:
                    pag_num = int(m_pag.group(1))
                except Exception:
                    pag_num = 1
            suf_pag = f"_PAG{pag_num:02d}.pdf"

            # Tipo: si OCR no detecta, conservar el que tenga el nombre
            m_tipo = re.match(r"^C_([PMX])_", p.name, flags=re.IGNORECASE)
            tipo_actual = (m_tipo.group(1).upper() if m_tipo else "X")
            tipo_final = (tipo or tipo_actual).upper()
            if tipo_final not in {"P", "M"}:
                tipo_final = tipo_actual

            apellido_l = limpiar_nombre_archivo(apellido)
            nombre_l = limpiar_nombre_archivo(nombre)
            nuevo = f"C_{tipo_final}_{apellido_l}_{nombre_l}{suf_pag}"
            if nuevo.lower() == p.name.lower():
                continue

            dst = p.with_name(nuevo)
            if dst.exists():
                print(f"  ⚠️  CONFLICTO: ya existe {dst.name} (no se renombra {p.name})")
                if f_log:
                    f_log.write(f"- CONFLICTO: {p} -> {dst} (ya existe)\n")
                continue

            print(f"  {'RENAME' if apply else 'PLAN'} {p.name} -> {dst.name}")
            if f_log:
                f_log.write(f"- {('RENAME' if apply else 'PLAN')}: {p} -> {dst}\n")

            if apply:
                p.rename(dst)
                changed += 1
        except Exception as ex:
            print(f"  ❌ Error reparando {p}: {ex}")
            if f_log:
                f_log.write(f"- ERROR: {p}: {ex}\n")

    if f_log:
        f_log.write(f"Cambios: {changed}\n")
        f_log.close()

    if apply:
        print(f"✅ Reparación completada. Renombrados: {changed}")
    else:
        print("ℹ️  Para aplicar renombres: python procesar.py --solo-validar --reparar-nombres --yes")
    print(f"📝 Log: {log_path}\n")

    return changed

def mover_pdfs_a_raiz(*, apply: bool = False, root: Path = CARPETA_PROCESADA) -> int:
    """Mueve PDFs generados desde subcarpetas hacia la raíz de pdf_procesados/.

    Si hay conflicto por mismo nombre:
    - Si el contenido es idéntico (hash), elimina la copia en subcarpeta (solo si apply=True).
    - Si difiere, NO toca nada y reporta conflicto.
    """
    moved = 0
    try:
        pdfs = [p for p in root.rglob("*.pdf") if p.is_file() and _es_pdf_con_pagina(p.name)]
    except Exception:
        pdfs = []

    sub_pdfs = []
    for p in pdfs:
        try:
            rel = p.relative_to(root)
            if len(rel.parts) > 1:
                sub_pdfs.append(p)
        except Exception:
            continue

    if not sub_pdfs:
        print("✅ No hay PDFs en subcarpetas para mover a la raíz.")
        return 0

    log_path = root / "mover_a_raiz.log"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        f_log = open(log_path, "a", encoding="utf-8")
        f_log.write(f"\n[{ts}] MOVER A RAÍZ (apply={apply})\n")
    except Exception:
        f_log = None

    print(f"\n📦 Mover a raíz: {len(sub_pdfs)} archivos encontrados")
    for p in sorted(sub_pdfs, key=lambda x: str(x)):
        dst = root / p.name
        try:
            if dst.exists():
                # Si es copia exacta, eliminar duplicado en subcarpeta (solo apply)
                same = False
                try:
                    st1 = p.stat()
                    st2 = dst.stat()
                    if int(st1.st_size) == int(st2.st_size):
                        same = (_sha256_file(p) == _sha256_file(dst))
                except Exception:
                    same = False

                if same:
                    msg = f"DUP-IDENTICO {p} -> {dst}"
                    print(f"  {'DEL' if apply else 'PLAN'} {msg}")
                    if f_log:
                        f_log.write(f"- {('DEL' if apply else 'PLAN')} {msg}\n")
                    if apply:
                        p.unlink()
                        moved += 1
                else:
                    msg = f"CONFLICTO {p} (ya existe {dst.name} y difiere)"
                    print(f"  ⚠️  {msg}")
                    if f_log:
                        f_log.write(f"- {msg}\n")
                continue

            msg = f"MOVE {p} -> {dst}"
            print(f"  {'MOVE' if apply else 'PLAN'} {p} -> {dst}")
            if f_log:
                f_log.write(f"- {('MOVE' if apply else 'PLAN')} {p} -> {dst}\n")
            if apply:
                p.rename(dst)
                moved += 1
        except Exception as ex:
            print(f"  ❌ Error moviendo {p}: {ex}")
            if f_log:
                f_log.write(f"- ERROR {p}: {ex}\n")

    # Intentar borrar carpetas vacías (best-effort)
    if apply:
        try:
            # Ordenar por profundidad para borrar primero las más profundas
            dirs = sorted([d for d in root.rglob("*") if d.is_dir()], key=lambda d: len(d.parts), reverse=True)
            for d in dirs:
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                except Exception:
                    pass
        except Exception:
            pass

    if f_log:
        f_log.write(f"Acciones: {moved}\n")
        f_log.close()

    if apply:
        print(f"✅ Mover a raíz completado. Acciones: {moved}")
    else:
        print("ℹ️  Para aplicar: python procesar.py --solo-validar --mover-a-raiz --yes")
    print(f"📝 Log: {log_path}\n")
    return moved

def dedupe_pdf_procesados(*, delete: bool = False, yes: bool = False, root: Path = CARPETA_PROCESADA) -> int:
    """Detecta duplicados exactos en pdf_procesados/.

    Duplicado exacto aquí = mismo nombre de archivo (.name) aparece en más de una ruta.
    Esto es el caso típico cuando existe una copia en la raíz y otra en subcarpeta.

    Retorna cuántos archivos se eliminaron.
    """
    try:
        all_pdfs = [p for p in root.rglob("*.pdf") if p.is_file() and _es_pdf_con_pagina(p.name)]
    except Exception:
        all_pdfs = []

    by_name: dict[str, list[Path]] = {}
    for p in all_pdfs:
        by_name.setdefault(p.name, []).append(p)

    dups = {n: ps for n, ps in by_name.items() if len(ps) > 1}
    if not dups:
        return 0

    print(f"\n🧹 Duplicados exactos detectados: {len(dups)} (mismo archivo en varias rutas)")

    deleted = 0
    for filename in sorted(dups.keys()):
        paths = dups[filename]
        keep = _dedupe_prefer_path(root, paths)
        print(f"  🔁 {filename} (x{len(paths)})")
        for p in sorted(paths, key=lambda x: str(x)):
            tag = "✅ KEEP" if p == keep else "🗑️  DEL"
            print(f"    - {tag} {p}")

        if delete:
            if not yes:
                print("    ⚠️  No se eliminó nada: falta --yes")
                continue
            for p in paths:
                if p == keep:
                    continue
                try:
                    p.unlink()
                    deleted += 1
                except Exception as ex:
                    print(f"    ❌ No se pudo eliminar {p}: {ex}")

    if delete and yes:
        print(f"✅ Deduplicación completada. Archivos eliminados: {deleted}\n")
    else:
        print("ℹ️  Para eliminar estos duplicados: python procesar.py --dedupe-delete --yes\n")

    return deleted

def validar_esperados_y_log(*, dedupe: bool = False, dedupe_hash: bool = False, dedupe_delete: bool = False, dedupe_yes: bool = False, dedupe_por_lista: bool = False, contar_repetidos: bool = False, return_items: bool = False):
    """Valida que los PDFs esperados estén en pdf_procesados/.

    Escribe un log en pdf_procesados/faltantes.log si falta alguno.
    """
    # Como forma de validación: detectar (y opcionalmente eliminar) duplicados exactos.
    if dedupe or dedupe_delete:
        if dedupe_por_lista:
            dedupe_pdf_procesados_por_lista(delete=dedupe_delete, yes=dedupe_yes)
        else:
            dedupe_pdf_procesados(delete=dedupe_delete, yes=dedupe_yes)

    # Limpieza adicional segura: duplicados por contenido (hash)
    if dedupe_hash or (dedupe_delete and dedupe_hash):
        dedupe_pdf_procesados_por_hash(delete=dedupe_delete, yes=dedupe_yes)

    esperados = cargar_esperados_desde_txt(ARCHIVO_ESPERADOS)
    if not esperados:
        print(f"ℹ️  No hay entradas válidas en {ARCHIVO_ESPERADOS.name} para validar.")
        return

    # Por defecto ignorar repetidos del TXT (suelen ser duplicados de la lista)
    dups_esperados: list[dict] = []
    if not contar_repetidos:
        esperados, dups_esperados = _dedupe_esperados(esperados)
        if dups_esperados:
            log_dups = CARPETA_PROCESADA / "esperados_duplicados.txt"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with open(log_dups, "a", encoding="utf-8") as f:
                    f.write(f"\n[{ts}] Duplicados ignorados en {ARCHIVO_ESPERADOS.name}: {len(dups_esperados)}\n")
                    for it in dups_esperados:
                        raw = it.get("raw") or it.get("display") or ""
                        f.write(f"- {raw}\n")
            except Exception:
                pass
            print(f"ℹ️  Se ignoraron {len(dups_esperados)} filas repetidas del TXT (ver {log_dups}).")

    try:
        # Buscar recursivamente porque los PDFs pueden estar en subcarpetas por fuente
        existentes = [p.name for p in CARPETA_PROCESADA.rglob("*.pdf")]
    except Exception:
        existentes = []

    # Consumir (remove) archivos para que duplicados cuenten
    disponibles_nombres = list(existentes)
    disponibles_pdfs = []
    for n in existentes:
        info = _pdf_tokens_desde_nombre(n)
        if info:
            disponibles_pdfs.append(info)
    faltantes: list[str] = []
    faltantes_items: list[dict] = []

    for item in esperados:
        raw_line = item.get("raw") or item.get("display")

        if item["kind"] == "exact":
            nombre = item["name"]
            try:
                idx = disponibles_nombres.index(nombre)
            except ValueError:
                faltantes.append(raw_line)
                continue
            disponibles_nombres.pop(idx)
            # también consumir en disponibles_pdfs si aplica
            for j, info in enumerate(disponibles_pdfs):
                if info.get("name") == nombre:
                    disponibles_pdfs.pop(j)
                    break
            continue

        # kind == fuzzy
        tipo = item.get("tipo")
        apellido1 = item.get("apellido1")
        nombre1 = item.get("nombre1")
        found_idx = None
        for i, info in enumerate(disponibles_pdfs):
            if info.get("tipo") != tipo:
                continue
            tokens = info.get("tokens") or []
            ok_ap = any(_token_match(apellido1, t) for t in tokens)
            ok_no = any(_token_match(nombre1, t) for t in tokens)
            if ok_ap and ok_no:
                found_idx = i
                break

        if found_idx is None:
            faltantes.append(raw_line)
            faltantes_items.append(item)
        else:
            # consumir para que duplicados cuenten
            consumed_name = disponibles_pdfs[found_idx].get("name")
            disponibles_pdfs.pop(found_idx)
            if consumed_name in disponibles_nombres:
                try:
                    disponibles_nombres.remove(consumed_name)
                except ValueError:
                    pass

    if not faltantes:
        print("✅ Validación OK: no faltan PDFs esperados.")
        return [] if return_items else None

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

    return faltantes_items if return_items else None

def _tokens_nombre_desde_raw(raw_line: str) -> list[str]:
    """Extrae tokens de nombre desde una fila del TXT (parte después del '-')."""
    if not raw_line:
        return []
    linea = raw_line
    if "-" in linea:
        linea = linea.split("-", 1)[1].strip()
    if "\t" in linea:
        linea = linea.split("\t", 1)[0].strip()
    palabras = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", linea)
    return [p for p in palabras if p]

def generar_faltantes_desde_fuentes(faltantes_items: list[dict], *, carpeta_pdf: Path = CARPETA_PDF, carpeta_out: Path = CARPETA_PROCESADA) -> int:
    """Busca y exporta SOLO los certificados faltantes, escaneando PDFs en carpeta_pdf.

    Exporta en la RAÍZ de pdf_procesados/ (sin crear subcarpetas).
    """
    if not faltantes_items:
        return 0

    fuentes = sorted(list(carpeta_pdf.glob("*.pdf")))
    if not fuentes:
        print("❌ No hay PDFs fuente en la carpeta 'pdf' para buscar faltantes.")
        return 0

    log_path = carpeta_out / "generados_faltantes.txt"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        f_log = open(log_path, "a", encoding="utf-8")
        f_log.write(f"\n[{ts}] GENERAR FALTANTES: {len(faltantes_items)}\n")
    except Exception:
        f_log = None

    generados = 0
    for it in faltantes_items:
        if it.get("kind") != "fuzzy":
            continue
        tipo_req = (it.get("tipo") or "").upper()
        apellido1 = it.get("apellido1") or ""
        nombre1 = it.get("nombre1") or ""
        raw = it.get("raw") or it.get("display") or ""

        # Preferir buscar también con los dos primeros apellidos si están en la línea
        tokens_raw = _tokens_nombre_desde_raw(raw)
        ap2 = tokens_raw[1] if len(tokens_raw) >= 2 else ""

        print(f"\n🔎 Generando faltante: {raw}")
        if f_log:
            f_log.write(f"- BUSCAR: {raw}\n")

        found = False
        for src in fuentes:
            # No tocar PDFs ya divididos
            if src.name.upper().startswith("C_P_") or src.name.upper().startswith("C_M_"):
                continue

            try:
                doc = fitz.open(str(src))
            except Exception:
                continue

            try:
                for page_idx in range(len(doc)):
                    # OCR superior para filtrar
                    texto_sup = extraer_texto_pagina_ocr(doc, page_idx, solo_superior=True)
                    if not texto_sup:
                        continue
                    tipo = detectar_tipo_certificado(texto_sup)
                    if tipo != tipo_req:
                        continue

                    nombre, apellido = extraer_nombre_apellido(texto_sup)
                    # Si no alcanza, OCR completo de esta página
                    if (not nombre) or (not apellido):
                        texto_full = extraer_texto_pagina_ocr(doc, page_idx, solo_superior=False)
                        tipo2 = detectar_tipo_certificado(texto_full) if texto_full else None
                        if tipo2 and tipo2 != tipo_req:
                            continue
                        if texto_full:
                            nombre, apellido = extraer_nombre_apellido(texto_full)

                    if not nombre or not apellido:
                        continue

                    # Validar match contra apellido1+nombre1 (y opcionalmente 2do apellido)
                    tokens_ap = (apellido or "").split()
                    tokens_no = (nombre or "").split()
                    ok_ap1 = any(_token_match(apellido1, t) for t in tokens_ap) if apellido1 else False
                    ok_no1 = any(_token_match(nombre1, t) for t in tokens_no) if nombre1 else False
                    ok_ap2 = True
                    if ap2:
                        ok_ap2 = any(_token_match(ap2, t) for t in tokens_ap)

                    if not (ok_ap1 and ok_no1 and ok_ap2):
                        continue

                    # Construir nombre de salida (en raíz)
                    apellido_l = limpiar_nombre_archivo(apellido)
                    nombre_l = limpiar_nombre_archivo(nombre)
                    out_name = f"C_{tipo_req}_{apellido_l}_{nombre_l}_PAG{page_idx + 1:02d}.pdf"
                    out_path = carpeta_out / out_name

                    if out_path.exists():
                        print(f"  ⏭️  Ya existe: {out_path.name}")
                        if f_log:
                            f_log.write(f"  - YA EXISTE: {out_path}\n")
                        found = True
                        break

                    # Exportar página
                    try:
                        out_pdf = fitz.open()
                        out_pdf.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
                        out_pdf.save(str(out_path))
                        out_pdf.close()
                        generados += 1
                        found = True
                        print(f"  ✅ Generado: {out_path.name} (desde {src.name} pág {page_idx + 1})")
                        if f_log:
                            f_log.write(f"  - OK: {out_path} (src={src.name} pag={page_idx + 1})\n")
                        break
                    except Exception as ex:
                        print(f"  ❌ Error exportando página: {ex}")
                        if f_log:
                            f_log.write(f"  - ERROR exportando: {ex}\n")
                        # seguir buscando en otras páginas
                        continue

                if found:
                    break
            finally:
                try:
                    doc.close()
                except Exception:
                    pass

        if not found:
            print("  ⚠️  No se encontró en PDFs fuente.")
            if f_log:
                f_log.write("  - NO ENCONTRADO\n")

    if f_log:
        f_log.write(f"Generados: {generados}\n")
        f_log.close()

    print(f"\n📝 Log: {log_path}")
    return generados

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
            candidato = linea.split(':', 1)[1].strip()
            if ',' in candidato:
                candidato = candidato.split(',', 1)[0].strip()
            corte = re.split(r"\b(titular|cedula|c[eé]dula|identidad)\b", candidato, flags=re.IGNORECASE)
            candidato = corte[0].strip() if corte else candidato
            candidatos.append(candidato)
            continue

        # Caso 2: "estudiante: APELLIDOS NOMBRES,"
        if 'estudiante' in low:
            candidato = None
            if ':' in linea:
                candidato = linea.split(':', 1)[1].strip()
            else:
                # A veces OCR no pone ':' o usa '-'
                m = re.search(r"estudiante\s*[:\-]?\s*(.+)$", linea, flags=re.IGNORECASE)
                if m:
                    candidato = m.group(1).strip()

            if candidato:
                if ',' in candidato:
                    candidato = candidato.split(',', 1)[0].strip()
                # Cortes adicionales por si OCR no puso coma
                corte = re.split(r"\b(titular|cedula|c[eé]dula|identidad)\b", candidato, flags=re.IGNORECASE)
                candidato = corte[0].strip() if corte else candidato
                candidatos.append(candidato)
                continue

    # Último intento: buscar patrón con regex dentro del texto completo
    if not candidatos:
        m = re.search(r"estudiante\s*[:\-]?\s*([^,\n]+)", texto, flags=re.IGNORECASE)
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

    # Esquema de nombres/salida:
    # - v2-src: archivos en pdf_procesados/ con prefijo SRC... (legacy)
    # - v3-subdir: archivos en pdf_procesados/<pdf_stem>/ (legacy)
    # - v4-root: archivos en la RAÍZ de pdf_procesados/ (SIN subcarpetas)
    naming_version = "v4-root"

    # Si el registro ya tiene page_outputs completo y los archivos existen, saltar rápido.
    # IMPORTANTE: aceptar también v2-src para NO regenerar duplicados.
    if anterior and anterior.get('mtime') == mtime and anterior.get('size') == size and anterior.get('naming') in {"v2-src", "v3-subdir", "v4-root"}:
        try:
            page_outputs_prev = anterior.get('page_outputs') if isinstance(anterior.get('page_outputs'), dict) else {}
            total_prev = int(anterior.get('paginas') or 0)
            if total_prev > 0 and len(page_outputs_prev) == total_prev:
                all_exist = all(_output_exists(v) for v in page_outputs_prev.values())
                if all_exist:
                    print(f"  ⏭️  PDF ya fue procesado. Saltando...")
                    return None
        except Exception:
            pass
    
    try:
        pdf_doc = fitz.open(str(ruta_pdf))
        total_paginas = len(pdf_doc)
        
        print(f"  Total de páginas: {total_paginas}")

        pdf_id = ruta_pdf.stem

        page_outputs: dict[str, str] = {}
        prev_naming = None
        if isinstance(anterior, dict):
            prev_naming = anterior.get('naming')
            if isinstance(anterior.get('page_outputs'), dict) and prev_naming in {"v2-src", "v3-subdir", "v4-root"}:
                prev = anterior.get('page_outputs')
                if isinstance(prev, dict):
                    try:
                        page_outputs = {str(k): _normalize_output_rel(str(v)) for k, v in prev.items() if v}
                    except Exception:
                        page_outputs = {}

        # Si NO hay registro completo (o es viejo), intentar detectar salidas en disco para NO regenerar.
        # 1) Preferir esquema actual en subcarpeta.
        disk_subdir = _detectar_outputs_en_subdir(pdf_id)
        for k, v in disk_subdir.items():
            # Preferir lo que realmente existe en disco.
            cur = page_outputs.get(k)
            if not cur or not _output_exists(cur):
                page_outputs[k] = v

        # 2) Detectar esquema viejo v2-src y migrarlo a RAÍZ sin SRC.
        disk_v2 = _detectar_outputs_v2_src(pdf_id)
        if disk_v2:
            migrated = _migrar_v2_src_a_root(pdf_id, disk_v2)
            for k, v in migrated.items():
                cur = page_outputs.get(k)
                if not cur or not _output_exists(cur):
                    page_outputs[k] = v

        # Si ya están TODAS las páginas en disco, registrar y salir sin OCR.
        try:
            if total_paginas > 0 and len(page_outputs) >= total_paginas:
                all_exist = all(_output_exists(page_outputs.get(str(i), "")) for i in range(total_paginas))
                if all_exist:
                    registro[nombre_pdf] = {
                        'mtime': mtime,
                        'size': size,
                        'paginas': total_paginas,
                        'naming': naming_version,
                        'page_outputs': {str(i): page_outputs[str(i)] for i in range(total_paginas)}
                    }
                    pdf_doc.close()
                    print("  ⏭️  Ya está generado en disco. Saltando...")
                    return None
        except Exception:
            pass

        try:
            for num_pagina in range(total_paginas):
                # Si esta página ya se exportó (por registro), saltar
                prev_out = page_outputs.get(str(num_pagina))
                if prev_out and _output_exists(prev_out):
                    page_outputs[str(num_pagina)] = _normalize_output_rel(prev_out)
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
                # Siempre guardar en la RAÍZ (sin subcarpetas)
                ruta_salida = CARPETA_PROCESADA / nombre_archivo
                rel_out = nombre_archivo

                if ruta_salida.exists():
                    page_outputs[str(num_pagina)] = rel_out
                    continue

                # Guardar página como PDF
                try:
                    # Asegurar que exista la carpeta de salida (puede haberse borrado o no haberse creado)
                    try:
                        ruta_salida.parent.mkdir(parents=True, exist_ok=True)
                    except Exception as mk_ex:
                        print(f"  ❌ Error creando carpeta de salida: {ruta_salida.parent} ({mk_ex})")
                        continue

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

                    page_outputs[str(num_pagina)] = rel_out

                except Exception as e:
                    print(f"  ❌ Error guardando página {num_pagina + 1}: {e}")

        except KeyboardInterrupt:
            # Guardar progreso parcial para evitar regenerar lo ya exportado.
            registro[nombre_pdf] = {
                'mtime': mtime,
                'size': size,
                'paginas': total_paginas,
                'naming': naming_version,
                'page_outputs': page_outputs,
                'incompleto': True,
            }
            try:
                pdf_doc.close()
            except Exception:
                pass
            print("\n⚠️ Interrumpido por el usuario. Progreso parcial guardado.")
            raise
        
        # Marcar PDF como procesado
        registro[nombre_pdf] = {
            'mtime': mtime,
            'size': size,
            'paginas': total_paginas,
            'naming': naming_version,
            'page_outputs': page_outputs,
            'incompleto': False,
        }
        
        pdf_doc.close()
    
    except Exception as e:
        print(f"❌ Error procesando {ruta_pdf.name}: {e}")
    
    return certificados_procesados

def main():
    """Función principal"""
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument(
        "--solo-validar",
        "--validate-only",
        dest="solo_validar",
        action="store_true",
        help="Ejecuta solo validación (y dedupe si aplica), sin procesar/OCR/exportar PDFs",
    )
    ap.add_argument("--dedupe", action="store_true", help="Detecta duplicados exactos en pdf_procesados/ antes de validar")
    ap.add_argument("--dedupe-hash", action="store_true", help="Detecta duplicados por contenido (hash) en pdf_procesados/")
    ap.add_argument("--dedupe-persona", action="store_true", help="Detecta duplicados por persona ignorando PAG## (modo seguro por hash visual)")
    ap.add_argument("--dedupe-persona-force", action="store_true", help="(Peligroso) Deja solo 1 por persona aunque el contenido no sea idéntico")
    ap.add_argument("--dedupe-delete", action="store_true", help="Elimina duplicados exactos en pdf_procesados/ antes de validar")
    ap.add_argument("--dedupe-por-lista", action="store_true", help="Deduplicación basada en pdf_generados_dev.txt (solo elimina duplicados de esa lista)")
    ap.add_argument("--reparar-nombres", action="store_true", help="Repara nombres incompletos en pdf_procesados/ usando OCR (requiere --yes para aplicar)")
    ap.add_argument("--mover-a-raiz", action="store_true", help="Mueve PDFs de subcarpetas hacia la raíz de pdf_procesados/ (requiere --yes para aplicar)")
    ap.add_argument("--generar-faltantes", action="store_true", help="Busca y genera SOLO los faltantes (según pdf_generados_dev.txt) escaneando PDFs en /pdf")
    ap.add_argument("--contar-repetidos", action="store_true", help="(Avanzado) Cuenta filas repetidas del TXT como requerimientos adicionales")
    ap.add_argument("--yes", action="store_true", help="Confirmación para eliminar (usar con --dedupe-delete)")
    args = ap.parse_args()

    # Limpieza rápida: si existen archivos legacy con SRC en la raíz, renombrarlos.
    # Esto evita duplicados por cambios de nombre y evita re-generar cuando ya existe.
    migrar_src_en_raiz(delete_identical=True)

    # Modo validación solamente: no tocar PDFs ni registro.
    if args.solo_validar:
        if args.reparar_nombres:
            reparar_nombres_incompletos_por_ocr(apply=args.yes)
        if args.mover_a_raiz:
            mover_pdfs_a_raiz(apply=args.yes)

        if args.dedupe_persona:
            dedupe_pdf_procesados_por_persona(
                delete=args.dedupe_delete,
                yes=args.yes,
                force=args.dedupe_persona_force,
            )

        faltantes_items = validar_esperados_y_log(
            dedupe=args.dedupe,
            dedupe_hash=args.dedupe_hash,
            dedupe_delete=args.dedupe_delete,
            dedupe_yes=args.yes,
            dedupe_por_lista=args.dedupe_por_lista,
            contar_repetidos=args.contar_repetidos,
            return_items=args.generar_faltantes,
        )

        if args.generar_faltantes:
            if not faltantes_items:
                return
            generar_faltantes_desde_fuentes(faltantes_items)
            # Re-validar después de generar
            validar_esperados_y_log(
                dedupe=False,
                dedupe_hash=False,
                dedupe_delete=False,
                dedupe_yes=False,
                dedupe_por_lista=False,
                contar_repetidos=args.contar_repetidos,
            )
        return

    archivos_pdf = sorted(list(CARPETA_PDF.glob("*.pdf")))
    if not archivos_pdf:
        print("❌ No hay archivos PDF en la carpeta 'pdf'")
        return
    
    # Cargar registro de PDFs ya procesados
    registro = cargar_registro_procesados()
    
    print(f"📂 Encontrados {len(archivos_pdf)} archivos PDF\n")
    
    total_p = 0
    total_m = 0
    
    try:
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

            # Persistir después de cada PDF para que una interrupción no regenere todo.
            guardar_registro_procesados(registro)

    except KeyboardInterrupt:
        guardar_registro_procesados(registro)
        print("\n⏹️ Proceso cancelado. Registro guardado.")
        return

    # Guardar registro actualizado (redundante, pero seguro)
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
    validar_esperados_y_log(
        dedupe=args.dedupe,
        dedupe_hash=args.dedupe_hash,
        dedupe_delete=args.dedupe_delete,
        dedupe_yes=args.yes,
        dedupe_por_lista=args.dedupe_por_lista,
        contar_repetidos=args.contar_repetidos,
    )

if __name__ == "__main__":
    main()
