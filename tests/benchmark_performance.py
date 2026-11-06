"""Deterministic microbenchmarks for Giovalia's hot backend paths.

Usage: python bench_performance.py <checkout> <result.json>
All database and catalog files are synthetic and live in a temporary directory.
"""
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


def measure(fn, repeats=7):
    fn()  # warmup
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    ordered = sorted(samples)
    return {
        "n": repeats,
        "mean_ms": round(statistics.mean(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "min_ms": round(min(samples), 3),
    }


def exhaustive_match(bib, descripcion, limite=2):
    consulta = ct_tokens(descripcion, True)
    if not consulta:
        return []
    resultados = []
    for clave, ficha in bib.indice.items():
        if bib_identity_conflict(clave, ficha):
            continue
        variantes = [ficha.get("nombre", clave), clave, *(ficha.get("aliases", []) or [])]
        mejor = 0.0
        for variante in variantes:
            tokens = ct_tokens(variante, False)
            if not tokens or product_conflict(consulta, tokens):
                continue
            comunes = consulta & tokens
            if not comunes:
                continue
            score = 2 * len(comunes) / (len(consulta) + len(tokens))
            marcas_consulta = consulta & product_brands
            marcas_candidata = tokens & product_brands
            if marcas_consulta and not marcas_candidata:
                score *= 0.75
            if len(comunes) == 1 and len(consulta) > 1:
                score *= 0.65
            mejor = max(mejor, score)
        if mejor:
            resultados.append({"clave": clave, "nombre": ficha.get("nombre", clave), "score": mejor})
    return sorted(resultados, key=lambda x: (-x["score"], x["clave"]))[:max(1, limite)]


def main():
    checkout = Path(sys.argv[1]).resolve()
    result_file = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(checkout))
    import cotizador as ct
    import historial as hs
    import buscador as bs
    global ct_tokens, bib_identity_conflict, product_conflict, product_brands
    ct_tokens = ct._tokens_producto
    bib_identity_conflict = ct.identidad_conflictiva
    product_conflict = ct._conflicto_producto
    product_brands = ct._MARCAS_PRODUCTO

    n_products = 600
    n_records = 100
    with tempfile.TemporaryDirectory(prefix="giovalia-bench-", ignore_cleanup_errors=True) as tmp:
        library = Path(tmp) / "biblioteca"
        library.mkdir()
        index = {}
        for i in range(n_products):
            key = f"SKU-{i:05d}"
            aliases = [f"PRODUCTO OFICINA {i:05d} RESMA CARTA"]
            if i < 60:
                aliases.append(f"CODIGO ANTERIOR {i:05d}")
            index[key] = {"nombre": f"Producto oficina {i:05d} resma carta", "aliases": aliases,
                          "precio_neto": 1000 + i}
        (library / "indice.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        for i in range(400):
            (library / f"LEGACY-{i:05d}").mkdir()
        for i in range(60):
            old = library / f"CODIGO ANTERIOR {i:05d}"
            old.mkdir()
            (old / "foto.jpg").write_bytes(b"synthetic-photo")

        init = measure(lambda: ct.Biblioteca(library), repeats=5)
        bib = ct.Biblioteca(library)
        refrescar = getattr(bib, "refrescar_si_cambio", None)
        rerun_service = measure(
            refrescar if callable(refrescar) else lambda: ct.Biblioteca(library), repeats=7
        )
        keys = [f"SKU-{i:05d}" for i in range(60)]
        files = measure(lambda: [bib.archivos(key) for key in keys], repeats=5)
        descriptions = [f"Producto oficina {i:05d} resma carta compatible" for i in range(25)]
        for query in descriptions[:5]:
            assert bib.coincidencias_semanticas(query, limite=2) == exhaustive_match(bib, query)
        matching = measure(lambda: [bib.coincidencias_semanticas(d, limite=2) for d in descriptions], repeats=3)

        buscador_original = bs._buscador
        descargar_original = bs.descargar_imagen

        class BuscadorSimulado:
            def images(self, consulta, max_results):
                return [{"image": f"https://images.example.test/{i}.jpg", "width": 640,
                         "height": 640, "title": f"foto {i}"} for i in range(18)]

        def descargar_simulado(url):
            i = int(url.rsplit("/", 1)[-1].split(".")[0])
            time.sleep(0.003 if i < 6 else 0.04)
            return b"synthetic-image", ".jpg", 640, 640

        bs._buscador = BuscadorSimulado
        bs.descargar_imagen = descargar_simulado
        image_search = measure(lambda: bs.buscar_fotos("producto genérico", max_resultados=6), repeats=5)
        bs._buscador = buscador_original
        bs.descargar_imagen = descargar_original

        hist = hs.Historial(library)
        for i in range(n_records):
            hist.crear({"nombre": f"Cliente {i}", "rut": ""},
                       [{"cantidad": 2, "descripcion": f"Producto {i}", "unitario": 1000}],
                       {"neto": 2000, "iva": 380, "total": 2380}, fecha=None)
        listing = measure(lambda: hist.listar(), repeats=5)
        ids = [r["id"] for r in hist.listar()]
        lookup = measure(lambda: [hist.obtener(i) for i in ids], repeats=3)
        update = measure(lambda: hist.actualizar(ids[-1], notas="benchmark"), repeats=5)

        output = {
            "checkout": str(checkout),
            "products": n_products,
            "legacy_folders": 820,
            "records": n_records,
            "workloads": {
                "library_init": init,
                "library_service_per_rerun": rerun_service,
                "asset_lookup_60_products": files,
                "semantic_match_25_descriptions": matching,
                "image_search_mock_18_candidates": image_search,
                "history_list_100_records": listing,
                "history_fetch_100_records": lookup,
                "single_history_update": update,
            },
        }
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
