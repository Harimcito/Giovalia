"""Prepara una copia del índice con UUID globales, sin tocar la biblioteca original."""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path


def preparar_indice(indice: dict) -> tuple[dict, int]:
    if not isinstance(indice, dict):
        raise ValueError("El índice debe ser un objeto JSON.")
    salida = copy.deepcopy(indice)
    generados = 0
    for clave, ficha in salida.items():
        if not isinstance(ficha, dict):
            raise ValueError(f"La ficha {clave!r} no es un objeto JSON.")
        id_global = str(ficha.get("id_global") or "").strip()
        if id_global:
            try:
                uuid.UUID(id_global)
            except ValueError as exc:
                raise ValueError(f"La ficha {clave!r} tiene un id_global que no es UUID.") from exc
            continue
        ficha["id_global"] = str(uuid.uuid4())
        generados += 1
    return salida, generados


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Índice original indice.json")
    parser.add_argument("--output", required=True, help="Ruta nueva para el índice preparado")
    args = parser.parse_args()

    origen = Path(args.input).expanduser().resolve()
    destino = Path(args.output).expanduser().resolve()
    if origen == destino:
        parser.error("La salida debe ser una copia nueva; no se sobrescribe el índice original.")
    indice = json.loads(origen.read_text(encoding="utf-8-sig"))
    preparado, generados = preparar_indice(indice)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(preparado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"UUID nuevos preparados: {generados}")
    print("El índice original quedó intacto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
