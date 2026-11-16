"""Selección de líneas cotizadas dentro de un presupuesto con IVA."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import NamedTuple

from cotizacion_opciones import cantidad_valida
from revisor import IVA


class Opcion(NamedTuple):
    codigo: str
    neto: Decimal
    existencias: Decimal | None
    linea: dict


class Pedido(NamedTuple):
    imprescindible: bool
    cantidad: Decimal
    opciones: list[Opcion]


@dataclass(frozen=True)
class Canasta:
    elegidas: tuple
    neto: Decimal
    imprescindibles: int
    cubiertas: int

    @property
    def total(self):
        return total_con_iva(self.neto)


def total_con_iva(neto):
    iva = neto * Decimal(str(IVA))
    return int(neto.quantize(Decimal(1), rounding=ROUND_HALF_UP) + iva.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def costo_linea(linea):
    precio = linea.get("unitario")
    precio = cantidad_valida(linea.get("precio_neto") if precio is None else precio)
    cantidad = cantidad_valida(linea.get("cantidad"))
    return None if precio is None or not cantidad else precio * cantidad


def _calcular(pedidos, presupuesto, base, *, hoy=False):
    """Por combinación de cobertura y stock acumulado conserva el costo menor."""
    repeticiones = Counter(codigo for p in pedidos for codigo in {o.codigo for o in p.opciones if o.codigo})
    compartidos = tuple(sorted(c for c, n in repeticiones.items() if n > 1)) if hoy else ()
    posiciones = {c: i for i, c in enumerate(compartidos)}
    estados = {(0, 0, (Decimal(0),) * len(compartidos)): Canasta((), base, 0, 0)}
    for pedido in pedidos:
        nuevos = {}
        for (_, _, usado), previo in estados.items():
            for opcion in (None, *pedido.opciones):
                stock = usado
                if hoy and opcion:
                    indice = posiciones.get(opcion.codigo)
                    suma = pedido.cantidad + (usado[indice] if indice is not None else 0)
                    if opcion.existencias is None or suma > opcion.existencias:
                        continue
                    if indice is not None:
                        stock = (*usado[:indice], suma, *usado[indice + 1:])
                neto = previo.neto + (opcion.neto if opcion else 0)
                if total_con_iva(neto) > presupuesto:
                    continue
                obligatorias = previo.imprescindibles + int(bool(opcion) and pedido.imprescindible)
                total = previo.cubiertas + int(bool(opcion))
                clave = obligatorias, total, stock
                if clave not in nuevos or neto < nuevos[clave].neto:
                    nuevos[clave] = Canasta((*previo.elegidas, opcion), neto, obligatorias, total)
                if len(nuevos) > 12000:
                    raise ValueError("Hay demasiadas combinaciones para revisar hoy. Reduce los reemplazos por línea.")
        estados = nuevos
    return [estado for estado in estados.values() if estado.cubiertas]


def planificar(pedidos, presupuesto, base=Decimal(0)):
    if not pedidos or not presupuesto or total_con_iva(base) > presupuesto:
        return {}
    con_cobertura = _calcular(pedidos, presupuesto, base)
    if not con_cobertura:
        return {}
    disponibles = _calcular(pedidos, presupuesto, base, hoy=True)
    menor = min(con_cobertura, key=lambda c: (-c.imprescindibles, c.neto, -c.cubiertas))
    cobertura = min(con_cobertura, key=lambda c: (-c.imprescindibles, -c.cubiertas, c.neto))
    inmediata = min(disponibles, key=lambda c: (-c.imprescindibles, -c.cubiertas, c.neto), default=None)
    return {"Menor costo": menor, "Entrega hoy": inmediata, "Mayor cobertura": cobertura}
