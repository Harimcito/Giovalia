"""Disponibilidad comercial y búsqueda en el espacio de trabajo."""
import json
import math

from cotizador import _alias_de_ficha, normalizar

POR_CONFIRMAR, EN_STOCK, A_PEDIDO = DISPONIBILIDADES = ("Por confirmar", "En stock", "A pedido")
COL_STOCK, COL_PLAZO = "Disponibilidad", "Plazo (días)"


def datos_entrega(estado, plazo):
    try:
        dias = float(plazo)
        dias = int(dias) if math.isfinite(dias) and dias >= 0 and dias.is_integer() else None
    except (TypeError, ValueError):
        dias = None
    return {"disponibilidad": estado if isinstance(estado, str) and estado in DISPONIBILIDADES else POR_CONFIRMAR,
            "plazo_dias": dias}


def columnas_entrega(lineas):
    return {
        COL_STOCK: [ln.get("disponibilidad", POR_CONFIRMAR) for ln in lineas],
        COL_PLAZO: [ln.get("plazo_dias") for ln in lineas],
    }


def plazo_efectivo(linea):
    entrega = datos_entrega(linea.get("disponibilidad"), linea.get("plazo_dias"))
    dias = entrega["plazo_dias"]
    if dias is None and entrega["disponibilidad"] == EN_STOCK:
        return 0
    return dias


def texto_plazo(dias):
    if dias is None:
        return "Plazo por confirmar"
    if dias == 0:
        return "Entrega inmediata"
    return f"{dias} {'día' if dias == 1 else 'días'}"


def descripcion_oferta(linea):
    descripcion = linea.get("descripcion", "")
    estado = linea.get("disponibilidad", POR_CONFIRMAR)
    dias = plazo_efectivo(linea)
    if estado == POR_CONFIRMAR and dias is None:
        return descripcion
    return f"{descripcion} · {estado}, {texto_plazo(dias).lower()}"


def buscar(consulta, bib, hist, *, privado=False, limite=8):
    termino = normalizar(consulta)
    if not termino:
        return [], [], []

    def coincide(*valores):
        return any(termino in normalizar(v or "") for v in valores)

    productos = [
        (codigo, ficha.get("nombre") or codigo)
        for codigo, ficha in bib.indice.items()
        if coincide(codigo, ficha.get("nombre"), ficha.get("descripcion"), *_alias_de_ficha(ficha))
    ]
    productos.sort(key=lambda item: (normalizar(item[0]) != termino, item[0]))
    clientes = [] if privado else [
        cli for cli in hist.clientes() if coincide(cli["nombre"], cli["rut"])
    ]
    cotizaciones = []
    for registro in hist.listar():
        cabecera = (registro["numero"],) if privado else (registro["numero"], registro["cliente"], registro["rut"])
        try:
            datos = json.loads(registro["datos"] or "{}")
        except (TypeError, json.JSONDecodeError):
            datos = {}
        lineas = datos.get("lineas") or []
        coincidencias = [
            ln.get("descripcion") or ln.get("clave_auto") for ln in lineas
            if coincide(ln.get("descripcion"), ln.get("clave_auto"))
        ]
        if coincidencias or coincide(*cabecera):
            cotizaciones.append((registro["id"], registro["numero"], coincidencias))
    cotizaciones.sort(key=lambda item: normalizar(item[1]) != termino)
    return productos[:limite], clientes[:limite], cotizaciones[:limite]
