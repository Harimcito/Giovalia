"""Confirmaciones de disponibilidad de los productos de la biblioteca."""
from datetime import date

from flujo_cotizacion import DISPONIBILIDADES, POR_CONFIRMAR


def datos_stock(ficha):
    estado = ficha.get("estado_stock")
    if estado not in DISPONIBILIDADES or estado == POR_CONFIRMAR:
        return {"estado_stock": POR_CONFIRMAR, "stock_confirmado_el": None, "stock_fuente": ""}
    try:
        fecha = date.fromisoformat(ficha.get("stock_confirmado_el") or "")
    except (TypeError, ValueError):
        fecha = None
    return {"estado_stock": estado, "stock_confirmado_el": fecha, "stock_fuente": ficha.get("stock_fuente") or ""}


def campos_stock(estado, fecha, fuente):
    if estado not in DISPONIBILIDADES:
        raise ValueError("Selecciona un estado de stock válido.")
    if estado == POR_CONFIRMAR:
        return {"estado_stock": estado, "stock_confirmado_el": None, "stock_fuente": None}
    if not isinstance(fecha, date) or fecha > date.today():
        raise ValueError("Ingresa la fecha de confirmación del stock. Debe ser hoy o anterior.")
    return {"estado_stock": estado, "stock_confirmado_el": fecha.isoformat(), "stock_fuente": fuente.strip()}
