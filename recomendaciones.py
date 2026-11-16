"""Sugerencias con procedencia: precio anterior, costo o comparables públicos."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from html import unescape
import json
import math
from pathlib import Path
import re
import sqlite3
from statistics import median
from urllib.parse import urlparse
from contextlib import contextmanager

from clasificacion_comercial import clasificar

IVA = 1.19
FUENTES = (
    {"nombre": "JCR Retail Chile", "host": "www.tiendasjcretailchile.cl",
     "api": "https://www.tiendasjcretailchile.cl/wp-json/wc/store/v1/products",
     "iva_verificado": True,
     "evidencia_iva": "https://www.tiendasjcretailchile.cl/pagina-de-inicio/"},
    {"nombre": "LoQueBuscabas", "host": "loquebuscabas.cl",
     "api": "https://loquebuscabas.cl/wp-json/wc/store/v1/products",
     "iva_verificado": True,
     "evidencia_iva": "https://loquebuscabas.cl/categoria-producto/insumos-impresoras-tintas-toner/toners-y-tambores/toner-alternativo/"},
)


def positivo(valor):
    try:
        valor = float(valor)
        return valor if math.isfinite(valor) and valor > 0 else None
    except (TypeError, ValueError):
        return None


def firma(clave, ficha):
    identidad = {"version": 2, "clave": clave, "nombre": ficha.get("nombre"), "aliases": ficha.get("aliases", [])}
    return sha256(json.dumps(identidad, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Referencias:
    def __init__(self, carpeta):
        self.ruta = Path(carpeta) / "referencias_precios.db"
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.execute("CREATE TABLE IF NOT EXISTS referencias (firma TEXT PRIMARY KEY, fecha TEXT NOT NULL, datos TEXT NOT NULL)")

    @contextmanager
    def _con(self):
        con = sqlite3.connect(self.ruta, timeout=15)
        try:
            with con:
                yield con
        finally:
            con.close()

    def leer(self, clave, ficha):
        with self._con() as con:
            fila = con.execute("SELECT fecha,datos FROM referencias WHERE firma=?", (firma(clave, ficha),)).fetchone()
        if not fila:
            return None
        datos = json.loads(fila[1])
        datos["fecha"] = fila[0]
        datos["vigente"] = datetime.fromisoformat(fila[0]) >= datetime.now(timezone.utc) - timedelta(days=7)
        return datos

    def guardar(self, clave, ficha, datos, fecha=None):
        fecha = datetime.fromisoformat(fecha).isoformat() if fecha else datetime.now(timezone.utc).isoformat()
        with self._con() as con:
            con.execute("INSERT INTO referencias VALUES (?,?,?) ON CONFLICT(firma) DO UPDATE SET fecha=excluded.fecha,datos=excluded.datos",
                        (firma(clave, ficha), fecha, json.dumps(datos, ensure_ascii=False)))
        return self.leer(clave, ficha)


def _normalizar(texto):
    import unicodedata
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().lower())


def _texto_oferta(oferta):
    nombre = unescape(str(oferta.get("name", "")))
    return re.sub(r"<[^>]+>", " ", nombre)


def _resmas_equivalentes(ficha, oferta):
    a = _normalizar(ficha.get("nombre", ""))
    b = _normalizar(_texto_oferta(oferta))
    formato_a = next((x for x in ("oficio", "carta", "a4", "a3") if x in a), None)
    formato_b = next((x for x in ("oficio", "carta", "a4", "a3") if x in b), None)
    gramos_a = re.search(r"(\d{2,3})(?:grs?|gramos?|g)(?![a-z])", a)
    gramos_b = re.search(r"(\d{2,3})(?:grs?|gramos?|g)(?![a-z])", b)
    # Sobre texto normalizado, aceptar grs seguido de puntuación eliminada o atributos.
    gramos_a = gramos_a or re.search(r"(\d{2,3})(?:grs|gramos|gr)", a)
    gramos_b = gramos_b or re.search(r"(\d{2,3})(?:grs|gramos|gr)", b)
    if not formato_a or formato_a != formato_b or not gramos_a or not gramos_b or gramos_a[1] != gramos_b[1]:
        return False
    marcas = ("chamex", "ovella", "report", "equalit", "access", "scribe", "winpaq")
    requerida = next((marca for marca in marcas if marca in a), None)
    if requerida and requerida not in b:
        return False
    colores = ("blanco", "blanca", "amarillo", "rosado", "azul", "verde")
    color = next((c for c in colores if c in a and c not in {"blanco", "blanca"}), None)
    return not color or color in b


def comparar(clave, ficha, oferta):
    """Exigir identidad suficiente; las categorías por sí solas no validan un precio."""
    objetivo = clasificar(clave, ficha)
    candidato = clasificar(str(oferta.get("sku", "")), {"nombre": _texto_oferta(oferta)})
    if re.search(r"^\s*[2-9]\d*\s+(?:t[oó]ners?|tintas?|cartuchos?)\b", _texto_oferta(oferta), re.I) or re.search(r"[*×]\s*[2-9]\d*\b", str(oferta.get("sku", ""))):
        return False, "Cantidad múltiple o pack: no es una unidad"
    if objetivo["familia"] != candidato["familia"]:
        return False, "Familia diferente"
    if objetivo.get("modelo"):
        mismo_modelo = _normalizar(objetivo["modelo"]) == _normalizar(candidato.get("modelo", ""))
        # El nombre puede contener simultáneamente el número comercial y el SKU.
        mencionado = _normalizar(objetivo["modelo"]) in _normalizar(_texto_oferta(oferta))
        if not candidato.get("modelo") or not (mismo_modelo or mencionado):
            return False, "Modelo diferente o no verificable"
    elif objetivo["familia"] == "resmas" and _resmas_equivalentes(ficha, oferta):
        pass
    else:
        return False, "Falta un modelo preciso para comparar automáticamente"
    variante = objetivo.get("variante")
    if objetivo["familia"] in {"toner", "tinta", "tambor"}:
        if variante not in {"original", "alternativo"}:
            return False, "Original o alternativo sin definir"
        if candidato.get("variante") != variante:
            return False, "Variante diferente o no verificable"
    presentacion = _normalizar(objetivo.get("presentacion") or "unidad")
    presentacion_candidata = _normalizar(candidato.get("presentacion") or "unidad")
    if presentacion != presentacion_candidata:
        return False, "Presentación o pack diferente"
    return True, "Formato, gramaje y presentación coinciden" if objetivo["familia"] == "resmas" else "Modelo, variante y presentación coinciden"


def consultar_fuente(clave, ficha, fuente, obtener=None):
    import requests
    obtener = obtener or requests.get
    identidad = clasificar(clave, ficha)
    modelo = identidad.get("modelo", "")
    if modelo.startswith("HP"):
        modelo = modelo[2:]
    elif modelo.startswith("EPSON"):
        modelo = re.sub(r"[^0-9]", "", modelo)
    elif modelo.startswith(("TN", "TK", "DR")):
        modelo = re.sub(r"[^0-9]", "", modelo)
    if not modelo and identidad["familia"] == "resmas":
        nombre = str(ficha.get("nombre", "")).lower()
        modelo = "resma " + next((f for f in ("oficio", "carta", "a4", "a3") if f in nombre), "")
    if not modelo:
        return [], [], ["No hay un modelo preciso para la búsqueda automática."]
    respuesta = obtener(fuente["api"], params={"search": modelo, "per_page": 12},
                        timeout=(4, 7), headers={"User-Agent": "Giovalia/1.0 (comparacion de catalogo)", "Accept": "application/json"})
    respuesta.raise_for_status()
    if len(respuesta.content) > 2_000_000:
        raise ValueError("La respuesta de precios supera el tamaño permitido.")
    productos = respuesta.json()
    if not isinstance(productos, list):
        raise ValueError("La fuente no devolvió un catálogo válido.")
    aceptadas, descartadas = [], []
    for producto in productos[:12]:
        if not isinstance(producto, dict):
            continue
        valida, motivo = comparar(clave, ficha, producto)
        precios = producto.get("prices") or {}
        moneda = precios.get("currency_code")
        menor = precios.get("currency_minor_unit")
        precio_bruto = positivo(precios.get("price"))
        url = producto.get("permalink", "")
        host = urlparse(url).hostname
        if moneda != "CLP" or menor != 0 or precio_bruto is None:
            valida, motivo = False, "Moneda o precio no verificable"
        if not producto.get("is_in_stock", False):
            valida, motivo = False, "Sin stock"
        if producto.get("type") not in {"simple", None}:
            valida, motivo = False, "Precio variable: elegir la variante exacta"
        if host != fuente["host"] or urlparse(url).scheme != "https":
            valida, motivo = False, "Enlace no verificable"
        if not fuente.get("iva_verificado"):
            valida, motivo = False, "Base de IVA sin confirmar"
        referencia = {
            "nombre": unescape(str(producto.get("name", ""))), "fuente": fuente["nombre"],
            "url": url, "precio_con_iva": precio_bruto,
            "precio_neto": precio_bruto / IVA if precio_bruto else None,
            "sku": producto.get("sku", ""), "motivo": motivo,
            "evidencia_iva": fuente["evidencia_iva"],
        }
        (aceptadas if valida else descartadas).append(referencia)
    return aceptadas, descartadas, []


def buscar_referencias(clave, ficha, fuentes=None, obtener=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    fuentes = tuple(fuentes or FUENTES)
    aceptadas, descartadas, avisos = [], [], []
    with ThreadPoolExecutor(max_workers=min(3, max(1, len(fuentes)))) as pool:
        trabajos = {pool.submit(consultar_fuente, clave, ficha, fuente, obtener): fuente for fuente in fuentes}
        for trabajo in as_completed(trabajos):
            try:
                referencias, descartes, mensajes = trabajo.result()
                aceptadas.extend(referencias)
                descartadas.extend(descartes)
                avisos.extend(mensajes)
            except Exception:
                avisos.append("No se pudo consultar " + trabajos[trabajo]["nombre"] + ". Puedes reintentar.")
    return {"referencias": aceptadas, "descartadas": descartadas, "avisos": list(dict.fromkeys(avisos))}


def auditar_final(ficha, referencia=None, flete=0, recargo=20, usar_precio_como_costo=False):
    """Escenario automático; nunca convierte un precio histórico en costo confirmado."""
    costo = positivo(ficha.get("costo_neto"))
    previo = positivo(ficha.get("precio_neto"))
    base = costo or previo
    if base is None:
        return {"final": None, "estado": "Falta precio o costo", "utilidad": None}
    flete, recargo = float(flete), float(recargo)
    if not all(math.isfinite(x) and x >= 0 for x in (flete, recargo)):
        raise ValueError("Flete y recargo deben ser finitos y no negativos")
    confirmado = bool(costo or usar_precio_como_costo)
    efectivo = base + flete
    mercado = positivo(referencia)
    if not confirmado:
        # Una venta anterior ya contiene utilidad: no volver a aplicarle recargo.
        neto = (mercado or previo) + flete
        final = math.ceil(neto * IVA - 1e-9)
        return {"final": final, "neto": final / IVA, "base": previo,
                "flete": flete, "iva": final - final / IVA, "utilidad": None,
                "margen": None, "mercado": mercado * IVA if mercado else None,
                "estado": "Mercado + flete; falta costo" if mercado else "Venta anterior + flete; falta costo",
                "base_tipo": "Referencia de venta; utilidad ya incluida"}
    objetivo = efectivo * (1 + recargo / 100)
    neto = max(objetivo, mercado or 0)
    final = math.ceil(neto * IVA - 1e-9)
    neto = final / IVA
    return {"final": final, "neto": neto, "base": base, "flete": flete,
            "iva": final - neto, "utilidad": neto - efectivo if confirmado else None,
            "margen": (neto - efectivo) / neto * 100 if confirmado else None,
            "mercado": mercado * IVA if mercado else None,
            "estado": ("Sobre referencia de mercado" if mercado and objetivo > mercado else "Con comparable de mercado") if mercado else "Sin comparable externo",
            "base_tipo": "Costo proveedor" if costo else ("Neto confirmado como costo" if usar_precio_como_costo else "Escenario sobre venta anterior")}


def recomendar(clave, ficha, consulta=None, modo="habitual"):
    categoria = clasificar(clave, ficha)
    previo = positivo(ficha.get("precio_neto"))
    costo = positivo(ficha.get("costo_neto"))
    aceptado = positivo(ficha.get("precio_recomendado_neto"))
    refs = (consulta or {}).get("referencias", []) if (consulta or {}).get("vigente", False) else []
    # Una fuente cuenta una vez aunque publique varias ofertas del mismo producto.
    por_fuente = {}
    for ref in refs:
        precio = positivo(ref.get("precio_neto"))
        if precio:
            por_fuente.setdefault(ref["fuente"], []).append(precio)
    valores = sorted(median(v) for v in por_fuente.values())
    resultado = {
        **categoria, "precio_previo": previo, "costo": costo, "precio_adoptado": aceptado,
        "precio_neto": None, "precio_con_iva": None, "origen": "Sin referencia",
        "confianza": "Sin datos", "margen_pct": None, "referencias": refs,
        "fecha": (consulta or {}).get("fecha"), "fuentes": len(valores), "advertencia": "",
    }
    if valores:
        if modo == "mercado_publico" and len(valores) >= 2:
            posicion = (len(valores) - 1) * .25
            inferior = int(posicion)
            fraccion = posicion - inferior
            candidato = valores[inferior] * (1-fraccion) + valores[min(inferior+1, len(valores)-1)] * fraccion
        else:
            candidato = median(valores)
        resultado.update(precio_neto=candidato, origen="Comparables externos",
                         confianza="Referencia única" if len(valores) == 1 else "Varias fuentes")
        resultado["advertencia"] = "No predice adjudicación. Compara condiciones de despacho, cantidad y plazo."
        if costo and candidato <= costo:
            resultado.update(precio_neto=None, origen="Referencia bajo el costo",
                             advertencia="La referencia no deja margen sobre el costo del proveedor; revisa la compra antes de ofertar.")
    elif aceptado:
        resultado.update(precio_neto=aceptado, origen="Sugerencia guardada", confianza="Decisión guardada")
    elif costo and ficha.get("margen_pct") is not None and math.isfinite(float(ficha["margen_pct"])) and float(ficha["margen_pct"]) >= 0:
        resultado.update(precio_neto=costo * (1 + float(ficha["margen_pct"]) / 100),
                         origen="Costo y recargo guardados", confianza="Criterio propio")
    elif previo:
        resultado.update(precio_neto=previo, origen="Precio anterior", confianza="Referencia interna",
                         advertencia="Aún no hay comparables externos verificados. Es tu precio anterior, no una validación de mercado.")
    if resultado["precio_neto"] is not None:
        # Precio final en pesos enteros y siempre redondeado hacia arriba.
        bruto = math.ceil(resultado["precio_neto"] * IVA - 1e-9)
        resultado["precio_con_iva"] = bruto
        resultado["precio_neto"] = bruto / IVA
        if costo:
            resultado["margen_pct"] = (resultado["precio_neto"] - costo) / resultado["precio_neto"] * 100
    return resultado
