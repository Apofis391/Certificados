#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Detecta y (opcionalmente) elimina PDFs duplicados en pdf_procesados/.

Se considera duplicado cuando se repite exactamente el mismo nombre base,
ignorando únicamente el sufijo de página: _PAG##.pdf

Ejemplo:
  C_P_VEGA_OROZCO_ROSARIO_DEL_CARMEN_PAG01.pdf
  C_P_VEGA_OROZCO_ROSARIO_DEL_CARMEN_PAG02.pdf

=> clave duplicada: C_P_VEGA_OROZCO_ROSARIO_DEL_CARMEN

Por seguridad, por defecto solo reporta. Para eliminar:
  python limpiar_duplicados.py --delete --yes
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


_PAG_SUFFIX_RE = re.compile(r"(?i)_PAG(\d{2})\.pdf$")


@dataclass(frozen=True)
class PdfEntry:
    path: Path
    key: str
    pag: int | None


def _extract_key_and_pag(filename: str) -> tuple[str, int | None] | None:
    """Devuelve (key, pag) donde key es el nombre sin _PAG##.pdf.

    Si no termina en _PAG##.pdf, retorna None (no entra en dedupe exacto).
    """
    m = _PAG_SUFFIX_RE.search(filename)
    if not m:
        return None
    try:
        pag = int(m.group(1))
    except Exception:
        pag = None

    key = _PAG_SUFFIX_RE.sub("", filename)
    # key no incluye .pdf
    if key.lower().endswith(".pdf"):
        key = key[:-4]
    return key, pag


def _make_group_key(entry: PdfEntry, *, mode: str) -> str:
    if mode == "base":
        return entry.key
    if mode == "base+pag":
        pag = entry.pag if entry.pag is not None else -1
        return f"{entry.key}__PAG{pag:02d}"
    if mode == "stem":
        return entry.path.stem
    raise ValueError(f"mode inválido: {mode}")


def find_duplicates(root: Path, *, recursive: bool = True, mode: str = "base+pag") -> dict[str, list[PdfEntry]]:
    pattern_iter = root.rglob("*.pdf") if recursive else root.glob("*.pdf")

    groups: dict[str, list[PdfEntry]] = defaultdict(list)
    for p in pattern_iter:
        if not p.is_file():
            continue
        res = _extract_key_and_pag(p.name)
        if not res:
            continue
        key, pag = res
        entry = PdfEntry(path=p, key=key, pag=pag)
        gk = _make_group_key(entry, mode=mode)
        groups[gk].append(entry)

    # solo duplicados
    return {k: v for k, v in groups.items() if len(v) > 1}


def _is_in_subdir(root: Path, p: Path) -> bool:
    try:
        rel = p.relative_to(root)
        return len(rel.parts) > 1
    except Exception:
        return False


def choose_keep(root: Path, entries: list[PdfEntry], keep: str) -> PdfEntry:
    if keep == "prefer-subdir":
        # Preferir el que está dentro de una subcarpeta (pdf_procesados/<pdf_id>/...)
        return sorted(
            entries,
            key=lambda e: (
                not _is_in_subdir(root, e.path),
                e.pag is None,
                e.pag or 10**9,
                str(e.path),
            ),
        )[0]
    if keep == "lowest-pag":
        return sorted(entries, key=lambda e: (e.pag is None, e.pag or 10**9, str(e.path)))[0]
    if keep == "highest-pag":
        return sorted(entries, key=lambda e: (e.pag is None, -(e.pag or -1), str(e.path)))[0]
    if keep == "newest":
        return sorted(entries, key=lambda e: (e.path.stat().st_mtime, str(e.path)), reverse=True)[0]
    if keep == "oldest":
        return sorted(entries, key=lambda e: (e.path.stat().st_mtime, str(e.path)))[0]
    if keep == "first":
        return sorted(entries, key=lambda e: str(e.path))[0]
    if keep == "last":
        return sorted(entries, key=lambda e: str(e.path), reverse=True)[0]
    raise ValueError(f"keep inválido: {keep}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Busca duplicados exactos por nombre base (sin _PAG##) en pdf_procesados/")
    ap.add_argument("--dir", default="pdf_procesados", help="Carpeta donde están los PDFs separados")
    ap.add_argument("--no-recursive", action="store_true", help="No buscar dentro de subcarpetas")
    ap.add_argument(
        "--mode",
        default="base+pag",
        choices=["base+pag", "stem", "base"],
        help=(
            "Cómo agrupar duplicados: "
            "base+pag (más seguro), stem (nombre completo sin .pdf), o base (agresivo: ignora página)"
        ),
    )
    ap.add_argument("--delete", action="store_true", help="Eliminar duplicados (dejar solo 1 por clave)")
    ap.add_argument("--yes", action="store_true", help="Confirmación para eliminar (requerida con --delete)")
    ap.add_argument(
        "--keep",
        default="prefer-subdir",
        choices=["prefer-subdir", "lowest-pag", "highest-pag", "newest", "oldest", "first", "last"],
        help="Qué archivo conservar cuando hay duplicados",
    )

    args = ap.parse_args()

    root = Path(args.dir)
    if not root.exists() or not root.is_dir():
        print(f"❌ Carpeta no existe: {root}")
        return 2

    dups = find_duplicates(root, recursive=not args.no_recursive, mode=args.mode)
    if not dups:
        print("✅ No hay duplicados exactos por nombre base.")
        return 0

    total_keys = len(dups)
    total_files = sum(len(v) for v in dups.values())
    print(f"⚠️  Duplicados detectados: {total_keys} claves ({total_files} archivos involucrados)")

    deleted_count = 0
    for group_key in sorted(dups.keys()):
        entries = dups[group_key]
        keep_entry = choose_keep(root, entries, args.keep)
        print(f"\n🔁 {group_key}  (x{len(entries)})")
        for e in sorted(entries, key=lambda x: (x.pag is None, x.pag or 10**9, str(x.path))):
            tag = "✅ KEEP" if e.path == keep_entry.path else "🗑️  DEL"
            pag_txt = f"PAG{e.pag:02d}" if e.pag is not None else "PAG??"
            print(f"  - {tag} {pag_txt}  {e.path}")

        if args.delete:
            if not args.yes:
                print("  ⚠️  No se eliminó nada: falta --yes")
                continue
            for e in entries:
                if e.path == keep_entry.path:
                    continue
                try:
                    e.path.unlink()
                    deleted_count += 1
                except Exception as ex:
                    print(f"  ❌ No se pudo eliminar {e.path}: {ex}")

    if args.delete and args.yes:
        print(f"\n✅ Eliminación completada. Archivos eliminados: {deleted_count}")
    else:
        print("\nℹ️  Modo reporte. Para eliminar: python limpiar_duplicados.py --delete --yes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
