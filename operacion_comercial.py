"""Cálculos y registros de la operación comercial de Giovalia."""
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import PurePosixPath
import math
import re
import sqlite3
import stat
import tempfile
import unicodedata
import zipfile

import pandas as pd


def _plano(valor):
    return unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode().upper()


def conflicto_presentacion(pedido, ficha):
    """Incompatibilidades explícitas, sin interpretar la cantidad pedida como pack."""
    nombre = _plano(ficha.get("nombre"))
    datos = " ".join(_plano(ficha.get(c)) for c in
                     ("unidad_venta", "compatibilidad", "atributos_tecnicos", "descripcion"))
    entrada = _plano(pedido)
    candidato = f"{nombre} {datos}"
    patrones = (
        r"\b(\d+)\s*HOJAS?\b",
        r"\b(?:PACK|CAJA|SET|BLISTER)(?:\s+DE|\s+X)?\s*(\d+)\s*(?:UNIDADES?|RESMAS?)?\b",
        r"\b(A[345]|CARTA|OFICIO)\b",
        r"\b(?:COMPATIBLE CON |PARA IMPRESORA )([A-Z]{1,7}[- ]?\d{2,6}[A-Z0-9]*)\b",
    )
    for patron in patrones:
        a = {v.replace("-", "").replace(" ", "") for v in re.findall(patron, entrada)}
        b = {v.replace("-", "").replace(" ", "") for v in re.findall(patron, candidato)}
        if a and b and a.isdisjoint(b):
            return True
    original = r"\b(?:ORIGINAL(?:ES)?|OEM)\b"
    alternativo = r"\b(?:ALTERNATIV[OA]S?|REMANUFACTURAD[OA]S?|GENERIC[OA]S?)\b"
    opcion_a = (bool(re.search(original, entrada)), bool(re.search(alternativo, entrada)))
    opcion_b = (bool(re.search(original, candidato)), bool(re.search(alternativo, candidato)))
    return opcion_a in {(True, False), (False, True)} and opcion_a == opcion_b[::-1]


def monto(valor):
    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if isinstance(valor, str) and "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif isinstance(valor, str) and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", texto):
        texto = texto.replace(".", "")
    try:
        n = Decimal(texto)
    except InvalidOperation as exc:
        raise ValueError("Monto inválido.") from exc
    if not n.is_finite() or n < 0 or n > 1_000_000_000_000:
        raise ValueError("Monto fuera de rango.")
    return n


def calcular_margen(lineas, indice, despacho=0, descuentos=0, otros=0, objetivo=20):
    bruto = costos = Decimal(0)
    faltantes = []
    for linea in lineas:
        if not linea.get("incluir", True):
            continue
        cantidad = monto(linea.get("cantidad", 0))
        precio = linea.get("unitario")
        if precio is None:
            precio = linea.get("precio_neto")
        clave = linea.get("clave_auto", "")
        costo = linea.get("costo_neto_cotizado", indice.get(clave, {}).get("costo_neto"))
        etiqueta = linea.get("descripcion") or clave
        if precio is None:
            faltantes.append(f"Precio pendiente: {etiqueta}")
        else:
            bruto += cantidad * monto(precio)
        if costo is None:
            faltantes.append(f"Costo pendiente: {etiqueta}")
        else:
            costos += cantidad * monto(costo)
    egresos = costos + monto(despacho) + monto(otros)
    rebaja = monto(descuentos)
    tasa = monto(objetivo) / 100
    if tasa >= 1 or rebaja > bruto:
        raise ValueError("El margen debe ser menor a 100% y el descuento no puede superar la venta.")
    venta = bruto - rebaja
    utilidad = venta - egresos
    return {
        "venta": venta, "egresos": egresos, "faltantes": faltantes,
        "utilidad": None if faltantes else utilidad,
        "margen": None if faltantes or not venta else utilidad * 100 / venta,
        "minimo": None if faltantes else math.ceil(egresos / (1 - tasa)),
    }


def cargar_costos(nombre, datos):
    """Lee una lista neta con columnas codigo y costo_neto."""
    if len(datos) > 8 * 1024 * 1024:
        raise ValueError("La lista de precios supera 8 MB.")
    if nombre.lower().endswith(".xlsx"):
        with zipfile.ZipFile(BytesIO(datos)) as z:
            if sum(item.file_size for item in z.infolist()) > 60_000_000:
                raise ValueError("La planilla descomprimida supera 60 MB.")
        tabla = pd.read_excel(BytesIO(datos), dtype=str)
    else:
        tabla = pd.read_csv(BytesIO(datos), encoding="utf-8-sig", sep=None, engine="python", dtype=str)
    tabla.columns = [_plano(col).strip().lower().replace(" ", "_") for col in tabla.columns]
    if not {"codigo", "costo_neto"}.issubset(tabla.columns):
        raise ValueError("La lista requiere las columnas codigo y costo_neto.")
    if len(tabla) > 5000:
        raise ValueError("Carga listas de hasta 5.000 productos.")
    salida = {}
    for fila in tabla[["codigo", "costo_neto"]].itertuples(index=False):
        clave = str(fila.codigo).strip().upper()
        if pd.isna(fila.codigo) or not clave or clave in salida:
            raise ValueError(f"Código vacío o duplicado: {clave}.")
        salida[clave] = float(monto(fila.costo_neto))
    if not salida:
        raise ValueError("La lista no contiene productos.")
    return salida


def comparar_costos(indice, lista, previa):
    salida = []
    for clave, costo in lista.items():
        ficha = indice.get(clave, {})
        anterior = previa.get(clave)
        margen = ficha.get("margen_pct")
        factor = 1 + monto(margen) / 100 if margen is not None else None
        actual = ficha.get("costo_neto")
        sugerido = round(monto(costo) * factor) if factor is not None else None
        salida.append({"Código": clave, "Producto": ficha.get("nombre", "Código sin ficha"),
                       "Costo anterior proveedor": anterior, "Costo catálogo": actual,
                       "Costo nuevo neto": costo, "Variación proveedor": None if anterior is None else costo - anterior,
                       "Variación catálogo": None if actual is None else costo - float(monto(actual)),
                       "Sugerido anterior neto": round(monto(actual) * factor) if actual is not None and factor is not None else None,
                       "Sugerido nuevo neto": sugerido})
    return salida


def identidad_cliente(nombre, rut):
    limpio = re.sub(r"[^0-9K]", "", str(rut or "").upper())
    return f"rut:{limpio}" if limpio else "nombre:" + " ".join(_plano(nombre).split())


def estado_seguimiento(vencimiento, hoy=None):
    if not vencimiento:
        return "Sin vigencia"
    dias = (date.fromisoformat(vencimiento) - (hoy or date.today())).days
    if dias < 0:
        return "Vencida"
    return "Vence pronto" if dias <= 3 else "Vigente"


def mensaje_seguimiento(numero, cliente, vencimiento):
    saludo = f"Hola, {cliente.strip()}." if cliente and cliente.strip() else "Hola."
    fecha = f" La vigencia indicada es hasta el {date.fromisoformat(vencimiento):%d/%m/%Y}." if vencimiento else ""
    return (f"{saludo} Te escribimos de Gioval para saber si pudiste revisar la cotización {numero}."
            f"{fecha} Si necesitas ajustar cantidades o tienes alguna consulta, quedamos atentos.")


def verificar_respaldo(contenido):
    """Restaura únicamente en una carpeta desechable y comprueba sus referencias."""
    with tempfile.TemporaryDirectory(prefix="giovalia-verificacion-") as temporal, \
         zipfile.ZipFile(BytesIO(contenido)) as archivo:
        from pathlib import Path
        root = Path(temporal)
        nombres = {}
        total = 0
        for item in archivo.infolist():
            nombre = item.filename.replace("\\", "/")
            ruta = PurePosixPath(nombre)
            if (not ruta.parts or ruta.is_absolute() or ".." in ruta.parts
                    or any(":" in p or p.endswith((" ", ".")) for p in ruta.parts)
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("El ZIP contiene rutas o enlaces no permitidos.")
            if item.is_dir():
                continue
            clave = ruta.as_posix().casefold()
            if clave in nombres:
                raise ValueError("El ZIP contiene rutas duplicadas.")
            nombres[clave] = (ruta, item)
            total += item.file_size
        if total > 500 * 1024 * 1024 or len(nombres) > 15000:
            raise ValueError("El respaldo supera el límite de extracción.")
        bases = [ruta for ruta, _ in nombres.values() if ruta.name == "cotizador.db"]
        if len(bases) != 1:
            raise ValueError("El respaldo debe contener una sola base cotizador.db.")
        import shutil
        for ruta, item in nombres.values():
            destino = root.joinpath(*ruta.parts)
            if not destino.resolve().is_relative_to(root):
                raise ValueError("La ruta del archivo no pertenece al respaldo.")
            destino.parent.mkdir(parents=True, exist_ok=True)
            with archivo.open(item) as origen, destino.open("wb") as salida:
                shutil.copyfileobj(origen, salida)
        db = root.joinpath(*bases[0].parts)
        faltantes = []
        import json
        from contextlib import closing
        from anexos_cotizacion import limpiar_referencias
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite no superó la comprobación de integridad.")
            filas = con.execute("SELECT numero, xlsx, pdf, datos FROM cotizaciones").fetchall()
            datos_versiones = []
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='versiones_cotizacion'").fetchone():
                datos_versiones = [json.loads(v[0]).get("datos", {}) for v in
                                   con.execute("SELECT contenido FROM versiones_cotizacion")]
        base = db.parent
        for numero, xlsx, pdf, _ in filas:
            for ruta in filter(None, (xlsx, pdf)):
                partes = PurePosixPath(ruta.replace("\\", "/")).parts
                candidata = base.joinpath(*partes[-3:]) if len(partes) >= 3 and partes[-3] == "cotizaciones" else None
                if not candidata or not candidata.resolve().is_relative_to(base) or not candidata.is_file():
                    faltantes.append(f"{numero}: falta el documento {partes[-1]}")
        for datos in [*(json.loads(fila[3] or "{}") for fila in filas), *datos_versiones]:
            for clave, seleccion in limpiar_referencias(datos.get("anexos")).items():
                for campo in ("foto", "ficha"):
                    if seleccion.get(campo) and not (base / "anexos_cotizaciones" / seleccion[campo]).is_file():
                        faltantes.append(f"{clave}: falta {campo} personalizado")
        indice = base / "indice.json"
        productos = json.loads(indice.read_text("utf-8")) if indice.is_file() else {}
        if not isinstance(productos, dict):
            raise ValueError("El índice de productos no es válido.")
        return {"cotizaciones": len(filas), "productos": len(productos), "versiones": len(datos_versiones),
                "fotos": sum(ruta.stem == "foto" or ruta.parent.name == "anexos_cotizaciones" and ruta.suffix != ".pdf"
                             for ruta, _ in nombres.values()),
                "fichas": sum(ruta.name == "ficha.pdf" or ruta.parent.name == "anexos_cotizaciones" and ruta.suffix == ".pdf"
                              for ruta, _ in nombres.values()),
                "faltantes": sorted(set(faltantes))}
