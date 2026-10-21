"""Revisor de cotizaciones: encuentra errores antes de crear el PDF.

Trabaja sobre lo que realmente dice el Excel (líneas, totales, datos del cliente), así que
también detecta errores en cotizaciones armadas a mano.
"""
import math
import re
import unicodedata
from datetime import date, datetime

IVA = 0.19


def redondear(x: float) -> int:
    """Redondeo como Excel (0,5 sube)."""
    return int(math.floor(x + 0.5))


def totales_de(lineas: list[dict]) -> dict:
    """Neto, IVA y total a partir de [{cantidad, precio_neto}]. Misma regla que la plantilla."""
    suma = sum((ln.get("precio_neto") or 0) * ln["cantidad"] for ln in lineas)
    neto = redondear(suma)
    iva = redondear(suma * IVA)
    return {"neto": neto, "iva": iva, "total": neto + iva}


def _norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", t.upper())


def clp(v) -> str:
    return "$" + f"{round(v):,}".replace(",", ".")


def validar_rut(rut) -> bool | None:
    """True/False según el dígito verificador; None si no hay RUT."""
    if not rut or not str(rut).strip():
        return None
    r = re.sub(r"[^0-9kK]", "", str(rut)).upper()
    if len(r) < 2 or not r[:-1].isdigit():
        return False
    suma, factor = 0, 2
    for d in reversed(r[:-1]):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    esperado = 11 - suma % 11
    dv = "0" if esperado == 11 else "K" if esperado == 10 else str(esperado)
    return r[-1] == dv


def _h(nivel: str, texto: str) -> dict:
    return {"nivel": nivel, "texto": texto}


def revisar(d: dict, claves: list[str] | None = None, bib=None) -> list[dict]:
    """Devuelve hallazgos [{nivel: 'error'|'aviso', texto}] ordenados (errores primero).

    d      : resultado de pdf_cotizacion.leer_para_pdf (líneas, totales, datos del cliente)
    claves : código de cada línea (misma cantidad y orden que d['lineas']), para comparar precios
    bib    : Biblioteca, para comparar con el último precio y con el costo
    """
    h = []
    lineas = d["lineas"]
    if not lineas:
        return [_h("error", "La cotización no tiene productos.")]

    # ---- precios y cantidades
    sin_precio = [ln["descripcion"][:40] for ln in lineas if not ln.get("unitario")]
    if sin_precio:
        h.append(_h("error", "Faltan precios en: " + ", ".join(sin_precio) + "."))
    for ln in lineas:
        c = ln["cantidad"]
        if isinstance(c, float) and not c.is_integer():
            h.append(_h("aviso", f"«{ln['descripcion'][:40]}»: la cantidad es {c} (con decimales). ¿Es correcto?"))
        elif c >= 1000:
            h.append(_h("aviso", f"«{ln['descripcion'][:40]}»: la cantidad es {c:g}. Revisa que no sobre un cero."))

    # ---- totales
    suma = sum((ln.get("unitario") or 0) * ln["cantidad"] for ln in lineas)
    tot = {_norm(e): v for e, v in d["totales"]}
    neto, iva, total = tot.get("TOTALNETO"), tot.get("IVA"), tot.get("TOTAL")
    if suma and neto is not None and total is not None:
        esp_neto = redondear(suma)
        esp_iva = redondear(suma * IVA)
        if abs(total - suma) <= 2 and abs(neto - suma / (1 + IVA)) <= 2:
            h.append(_h("error",
                        f"El IVA se está restando en vez de sumarse. La suma de las líneas es {clp(suma)} y la "
                        f"cotización la muestra como total con IVA. Si los precios unitarios son netos, el total "
                        f"con IVA debería ser {clp(esp_neto + esp_iva)} (neto {clp(esp_neto)} + IVA {clp(esp_iva)})."))
        else:
            if abs(neto - esp_neto) > 1:
                h.append(_h("error", f"El Total Neto ({clp(neto)}) no coincide con la suma de las líneas ({clp(esp_neto)})."))
            if iva is not None and abs(iva - redondear(neto * IVA)) > 1:
                h.append(_h("error", f"El IVA ({clp(iva)}) no es el 19 % del neto ({clp(redondear(neto * IVA))})."))
            if iva is not None and abs(total - (neto + iva)) > 1:
                h.append(_h("error", f"El Total ({clp(total)}) no es Neto + IVA ({clp(neto + iva)})."))
    elif suma:
        h.append(_h("aviso", "No pude encontrar los totales de la cotización para comprobarlos."))

    # ---- comparación con lo que ya sabe la biblioteca (último precio y costo)
    if bib is not None and claves and len(claves) == len(lineas):
        for ln, clave in zip(lineas, claves):
            ficha = bib.indice.get(str(clave).strip().upper(), {})
            u, nombre = ln.get("unitario"), ln["descripcion"][:40]
            if not u:
                continue
            costo, previo = ficha.get("costo_neto"), ficha.get("precio_neto")
            if costo and u < costo - 0.5:
                h.append(_h("error", f"«{nombre}»: vendes a {clp(u)} neto, por debajo de tu costo ({clp(costo)})."))
            elif previo and abs(u / previo - 1) > 0.25:
                h.append(_h("aviso", f"«{nombre}»: el precio ({clp(u)} neto) es {abs(u / previo - 1):.0%} "
                                     f"{'mayor' if u > previo else 'menor'} que el último que usaste ({clp(previo)})."))

    # ---- productos repetidos
    vistos = {}
    for i, ln in enumerate(lineas):
        clave = (claves[i] if claves and len(claves) == len(lineas) and str(claves[i]).strip() else _norm(ln["descripcion"]))
        if clave in vistos:
            h.append(_h("aviso", f"«{ln['descripcion'][:40]}» aparece más de una vez."))
        vistos[clave] = True

    # ---- datos del cliente
    cli = {_norm(e): v for e, v in d["izquierda"] + d["derecha"]}
    if not str(cli.get("NOMBRE", "")).strip():
        h.append(_h("error", "Falta el nombre del cliente."))
    rut = str(cli.get("RUT", "")).strip()
    if not rut:
        h.append(_h("aviso", "Falta el RUT del cliente."))
    elif validar_rut(rut) is False:
        h.append(_h("aviso", f"El RUT «{rut}» no parece válido (revisa el dígito verificador)."))
    fecha = cli.get("FECHA")
    if fecha:
        try:
            f = datetime.strptime(str(fecha).strip(), "%d/%m/%Y").date()
            if (date.today() - f).days > 60:
                h.append(_h("aviso", f"La fecha de la cotización ({fecha}) tiene más de 60 días."))
        except ValueError:
            pass

    h.sort(key=lambda x: x["nivel"] != "error")
    return h
