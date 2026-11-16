"""Deshacer cambios de una fila sin revertir cambios de otras filas."""
from uuid import uuid4

ID = "_editor_id"


def identificar(lineas):
    return [linea if linea.get(ID) else {**linea, ID: uuid4().hex} for linea in lineas]


def valores_visibles(linea):
    if linea is None:
        return None
    precio = linea.get("unitario")
    return (
        linea.get("descripcion", ""), linea.get("cantidad", 1), linea.get("clave_auto", ""),
        linea.get("incluir", linea.get("es_producto", True)),
        linea.get("precio_neto") if precio is None else precio,
        linea.get("disponibilidad") or "Por confirmar", linea.get("plazo_dias"),
    )


def cambios(anteriores, nuevas):
    antes = {linea[ID]: linea for linea in anteriores}
    despues = {linea[ID]: linea for linea in nuevas}
    orden = list(antes)
    for fila in dict.fromkeys((*antes, *despues)):
        original, nueva = antes.get(fila), despues.get(fila)
        if valores_visibles(original) == valores_visibles(nueva):
            continue
        posicion = orden.index(fila) if original is not None else len(orden)
        yield {
            "id": fila, "antes": original, "despues": nueva, "posicion": posicion,
            "anteriores": orden[:posicion], "posteriores": orden[posicion + 1:],
        }


def debe_deshacerse(cambio, por_id):
    return valores_visibles(por_id.get(cambio["id"])) == valores_visibles(cambio["despues"])


def deshacer(lineas, cambio):
    indices = {fila[ID]: i for i, fila in enumerate(lineas)}
    anterior = cambio["antes"]
    posicion = indices.get(cambio["id"])
    salida = list(lineas)
    if posicion is not None:
        if anterior is None:
            salida.pop(posicion)
        else:
            salida[posicion] = anterior
    else:
        posterior = next((indices[i] for i in cambio["posteriores"] if i in indices), None)
        previo = next((indices[i] + 1 for i in reversed(cambio["anteriores"]) if i in indices), None)
        destino = posterior if posterior is not None else previo
        salida.insert(destino if destino is not None else cambio["posicion"], anterior)
    return salida
