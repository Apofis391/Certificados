#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verifica faltantes comparando `pdf_generados_dev.txt` vs `pdf_procesados/`.

A diferencia del validador incluido en `procesar.py`, este script NO exige que el
apellido/nombre coincidan exactos en el nombre del archivo. Solo pide que exista
match "aproximado" del primer apellido y primer nombre (1 letra de diferencia).

- C_P_... = certificado de promoción (cp)
- C_M_... = certificado de matrícula (cm)

Uso:
  python verificar_faltantes_fuzzy.py
  python verificar_faltantes_fuzzy.py --txt pdf_generados_dev.txt --dir pdf_procesados
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


CONNECTORES = {
    "de",
    "del",
    "la",
    "las",
    "los",
    "y",
}


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ASCII", "ignore").decode("ASCII")


def norm_word(text: str) -> str:
    text = (text or "").strip()
    text = _strip_accents(text)
    text = re.sub(r"[^A-Za-z0-9]+", "", text)
    return text.upper()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # DP con dos filas (eficiente para tokens cortos)
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


def token_match(expected: str, candidate: str, *, max_edit: int = 1, max_extra: int = 2) -> bool:
    e = norm_word(expected)
    c = norm_word(candidate)
    if not e or not c:
        return False

    if e == c:
        return True

    # Permitir 1-2 letras de más / menos
    if e in c and (len(c) - len(e)) <= max_extra:
        return True
    if c in e and (len(e) - len(c)) <= max_extra:
        return True

    if abs(len(e) - len(c)) > max_edit:
        return False

    return levenshtein(e, c) <= max_edit


def _read_text_any_encoding(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return path.read_text(encoding="latin-1", errors="ignore")


def _line_kind(line_norm: str) -> str | None:
    # Retorna 'P' o 'M' si puede inferir tipo
    if "promocion" in line_norm:
        return "P"
    if "matricula" in line_norm:
        return "M"
    return None


def _extract_name_part(raw_line: str) -> str:
    line = (raw_line or "").strip()
    if not line:
        return ""

    # Si hay '-', tomar lo que está a la derecha
    if "-" in line:
        line = line.split("-", 1)[1].strip()

    # Si viene como tabla con TABs, quedarse con la primera columna
    if "\t" in line:
        line = line.split("\t", 1)[0].strip()

    return line


def _extract_words(name_part: str) -> list[str]:
    # Palabras con letras (con tildes/ñ), ignorando números/puntuación
    return re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", name_part)


def _first_surname_and_first_name(words: list[str]) -> tuple[str | None, str | None]:
    if len(words) < 2:
        return None, None

    # Primer apellido: primera palabra (aunque luego haya conectores "de la")
    surname1 = words[0]

    # Heurística: nombres al final (últimos 1-2 tokens)
    if len(words) >= 4:
        given1 = words[-2]
    elif len(words) == 3:
        given1 = words[-1]
    else:  # len == 2
        given1 = words[1]

    # Si given1 es conector raro por OCR, intentar retroceder
    if given1 and given1.strip().lower() in CONNECTORES and len(words) >= 5:
        given1 = words[-3]

    return surname1, given1


@dataclass(frozen=True)
class ExpectedCert:
    kind: str  # 'P' o 'M'
    surname1: str
    given1: str
    raw_line: str
    display_name: str


def parse_expected_certs(txt_path: Path) -> list[ExpectedCert]:
    """Devuelve una entrada por certificado esperado (una línea del TXT).

    Esto hace que el conteo coincida con tu lista de control (ej. 36 líneas).
    """
    contenido = _read_text_any_encoding(txt_path)
    certs: list[ExpectedCert] = []

    for raw in contenido.splitlines():
        raw = (raw or "").strip()
        if not raw:
            continue

        low = _strip_accents(raw).lower()
        if low.startswith("nombre del certificado"):
            continue

        kind = _line_kind(low)
        if not kind:
            continue

        name_part = _extract_name_part(raw)
        words = _extract_words(name_part)
        surname1, given1 = _first_surname_and_first_name(words)
        if not surname1 or not given1:
            continue

        certs.append(
            ExpectedCert(
                kind=kind,
                surname1=surname1,
                given1=given1,
                raw_line=raw,
                display_name=name_part.strip(),
            )
        )

    return certs


@dataclass(frozen=True)
class ProcessedPdf:
    path: Path
    kind: str  # 'P' o 'M'
    tokens: tuple[str, ...]  # tokens del nombre


def list_processed_pdfs(dir_path: Path) -> list[ProcessedPdf]:
    pdfs: list[ProcessedPdf] = []
    for p in sorted(dir_path.glob("*.pdf")):
        m = re.match(r"^C_([PM])_(.+)\.pdf$", p.name, flags=re.IGNORECASE)
        if not m:
            continue
        kind = m.group(1).upper()
        rest = m.group(2)
        raw_tokens = rest.split("_")

        tokens: list[str] = []
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

        pdfs.append(ProcessedPdf(path=p, kind=kind, tokens=tuple(tokens)))
    return pdfs


def _has_match_in_tokens(expected_word: str, tokens: Iterable[str]) -> bool:
    for t in tokens:
        if token_match(expected_word, t):
            return True
    return False


def find_matching_pdf(expected_surname1: str, expected_given1: str, candidates: list[ProcessedPdf]) -> ProcessedPdf | None:
    for pdf in candidates:
        if _has_match_in_tokens(expected_surname1, pdf.tokens) and _has_match_in_tokens(expected_given1, pdf.tokens):
            return pdf
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Valida faltantes (match suave apellido+nombre) vs pdf_procesados")
    ap.add_argument("--txt", default="pdf_generados_dev.txt", help="Archivo de control (tabla) con certificados esperados")
    ap.add_argument("--dir", default="pdf_procesados", help="Carpeta donde están los PDFs separados")
    args = ap.parse_args(argv)

    txt_path = Path(args.txt)
    dir_path = Path(args.dir)

    if not txt_path.exists():
        print(f"❌ No existe: {txt_path}")
        return 2
    if not dir_path.exists():
        print(f"❌ No existe: {dir_path}")
        return 2

    expected = parse_expected_certs(txt_path)
    if not expected:
        print(f"ℹ️  No se encontraron entradas válidas en {txt_path.name}.")
        return 0

    processed = list_processed_pdfs(dir_path)
    by_kind: dict[str, list[ProcessedPdf]] = {"P": [], "M": []}
    for pdf in processed:
        by_kind.setdefault(pdf.kind, []).append(pdf)

    missing: list[tuple[str, str]] = []  # (display_line, falta_hum)

    ok = 0
    # Consumir PDFs: si un PDF satisface una línea, no debe contarse 2 veces
    for cert in expected:
        candidates = by_kind.get(cert.kind, [])
        matched = None
        for i, pdf in enumerate(candidates):
            if _has_match_in_tokens(cert.surname1, pdf.tokens) and _has_match_in_tokens(cert.given1, pdf.tokens):
                matched = (i, pdf)
                break

        if matched is None:
            falta_hum = "cp" if cert.kind == "P" else "cm"
            missing.append((cert.raw_line, falta_hum))
            continue

        idx, _pdf = matched
        # Consumir
        candidates.pop(idx)
        ok += 1

    total = len(expected)
    faltan = len(missing)

    print(f"Total certificados (según txt): {total}")
    print(f"OK (encontrados): {ok}")
    print(f"Faltantes: {faltan}")

    if missing:
        print("\n--- FALTAN ---")
        for raw_line, falta_hum in missing:
            print(f"- {raw_line}  => falta: {falta_hum}")

    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
