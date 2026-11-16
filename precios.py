"""Costos efectivos y margen sobre venta. IVA separado de la rentabilidad."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json

IVA = Decimal("0.19")
CLAVE_COSTOS_FIJOS = "costos_fijos_mensuales_v1"


@dataclass(frozen=True)
class CostosFijos:
    monto: Decimal
    base: Decimal
    por_costo: bool
    estimado: bool

    @classmethod
    def crear(cls, monto, base, por_costo=False, estimado=True):
        monto, base = numero(monto, "Gasto fijo mensual"), numero(base, "Base mensual")
        if base <= 0:
            raise ValueError("La base mensual debe ser mayor que cero.")
        return cls(monto, base, por_costo, estimado)

    @classmethod
    def leer(cls, valor):
        if not valor:
            return None
        return cls.crear(**json.loads(valor))

    def guardar(self):
        return json.dumps({"monto": str(self.monto), "base": str(self.base),
                           "por_costo": self.por_costo, "estimado": self.estimado})

    @property
    def tasa(self):
        return self.monto / self.base

    def por_unidad(self, costo_directo):
        return self.tasa * numero(costo_directo, "Costo directo") if self.por_costo else self.tasa


def sumar_costos_fijos(costos, plan):
    """Asigna el gasto mensual al lote y conserva el desembolso inicial de la compra."""
    fijo = plan.por_unidad(costos["costo_unitario"])
    return {**costos, "costo_unitario": costos["costo_unitario"] + fijo,
            "costo_total": costos["costo_total"] + fijo * costos["unidades"],
            "fijo_unitario": fijo}


def numero(valor, nombre="Importe"):
    try:
        resultado = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{nombre}: ingresa un número válido.") from None
    if not resultado.is_finite() or resultado < 0:
        raise ValueError(f"{nombre}: debe ser finito y mayor o igual a cero.")
    return resultado


def costo_efectivo(costo_proveedor_neto, unidades, transporte_pagado, otros_costos=0,
                   iva_producto_recuperable=True):
    """Transporte y otros gastos son costos efectivos del lote, sin crédito adicional."""
    costo = numero(costo_proveedor_neto, "Costo del proveedor")
    cantidad = numero(unidades, "Unidades")
    if cantidad <= 0 or cantidad != cantidad.to_integral_value():
        raise ValueError("Unidades: ingresa un entero mayor que cero.")
    transporte = numero(transporte_pagado, "Transporte")
    otros = numero(otros_costos, "Otros costos")
    iva_compra = costo * cantidad * IVA
    credito = iva_compra if iva_producto_recuperable else Decimal(0)
    total = costo * cantidad + iva_compra - credito + transporte + otros
    return {
        "unidades": cantidad,
        "costo_total": total,
        "costo_unitario": total / cantidad,
        "transporte_unitario": transporte / cantidad,
        "iva_compra": iva_compra,
        "credito_iva_compra": credito,
        "desembolso_inicial": costo * cantidad + iva_compra + transporte + otros,
    }


def evaluar_precio(costos, precio_unitario, incluye_iva=True):
    precio = numero(precio_unitario, "Precio de venta")
    neto = precio / (1 + IVA) if incluye_iva else precio
    bruto = precio if incluye_iva else precio * (1 + IVA)
    ingreso = neto * costos["unidades"]
    resultado = ingreso - costos["costo_total"]
    return {
        "precio_neto": neto,
        "precio_con_iva": bruto,
        "ingreso_neto": ingreso,
        "iva_venta": ingreso * IVA,
        "resultado_pedido": resultado,
        "resultado_unitario": resultado / costos["unidades"],
        "margen_pct": resultado / ingreso * 100 if ingreso else None,
        "recargo_pct": resultado / costos["costo_total"] * 100 if costos["costo_total"] else None,
    }


def precio_para_margen(costos, margen_pct, *, redondear_con_iva=True):
    margen = numero(margen_pct, "Margen")
    if margen >= 100:
        raise ValueError("El margen debe ser menor que 100 %.")
    neto = costos["costo_unitario"] / (1 - margen / 100)
    # Redondear hacia arriba el precio final evita sugerir una pérdida por redondeo.
    base = neto * (1 + IVA) if redondear_con_iva else neto
    precio = base.quantize(Decimal(1), rounding=ROUND_CEILING)
    return evaluar_precio(costos, precio, incluye_iva=redondear_con_iva)
