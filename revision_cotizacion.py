"""Comparación de versiones y revisión del documento renderizado."""
from io import BytesIO

from pypdf import PdfReader


def comparar(anterior, nuevo):
    resultado = []

    def anotar(seccion, nombre, antes, despues):
        if antes != despues:
            resultado.append({"Sección": seccion, "Campo": nombre,
                              "Antes": "—" if antes is None else str(antes),
                              "Después": "—" if despues is None else str(despues)})

    for campo in ("fecha", "cliente", "rut", "neto", "iva", "total", "con_iva"):
        anotar("Cotización", campo, anterior.get(campo), nuevo.get(campo))
    datos_a, datos_b = anterior.get("datos", {}), nuevo.get("datos", {})
    cli_a, cli_b = datos_a.get("cliente", {}), datos_b.get("cliente", {})
    for campo in ("direccion", "ciudad", "telefono", "email"):
        anotar("Cliente", campo, cli_a.get(campo), cli_b.get(campo))
    lineas_a, lineas_b = datos_a.get("lineas", []), datos_b.get("lineas", [])
    for i in range(max(len(lineas_a), len(lineas_b))):
        a = lineas_a[i] if i < len(lineas_a) else {}
        b = lineas_b[i] if i < len(lineas_b) else {}
        for campo in ("descripcion", "cantidad", "clave_auto", "incluir"):
            anotar(f"Línea {i + 1}", campo, a.get(campo), b.get(campo))
        def precio(linea):
            valor = linea.get("unitario")
            return linea.get("precio_neto") if valor is None else valor
        anotar(f"Línea {i + 1}", "Precio neto unitario", precio(a), precio(b))
    anexos_a, anexos_b = datos_a.get("anexos", {}), datos_b.get("anexos", {})
    for codigo in sorted(anexos_a.keys() | anexos_b.keys()):
        for campo in ("foto", "ficha", "ficha_opcional"):
            a = anexos_a.get(codigo, {}).get(campo, "Biblioteca")
            b = anexos_b.get(codigo, {}).get(campo, "Biblioteca")
            if a != b:
                def describir(valor):
                    if valor is None:
                        return "Sin anexo"
                    if isinstance(valor, str) and valor != "Biblioteca":
                        return f"Archivo {valor[:8]}"
                    return valor
                anotar(codigo, campo, describir(a), describir(b))
    return resultado


def revisar_paginas(contenido, paginas_cotizacion):
    """Señala páginas vacías o posiciones de texto fuera del área visible."""
    avisos = []
    for i, pagina in enumerate(PdfReader(BytesIO(contenido)).pages, 1):
        if i > paginas_cotizacion:
            break
        visto = False
        fuera = False
        caja = pagina.cropbox

        def visitar(texto, matriz, posicion, *_):
            nonlocal visto, fuera
            if texto.strip():
                visto = True
                x, y = posicion[4:6]
                px = x * matriz[0] + y * matriz[2] + matriz[4]
                py = x * matriz[1] + y * matriz[3] + matriz[5]
                fuera |= not (float(caja.left) - 2 <= px <= float(caja.right) + 2
                              and float(caja.bottom) - 2 <= py <= float(caja.top) + 2)

        pagina.extract_text(visitor_text=visitar)
        if not visto:
            avisos.append(f"Página {i}: no pude detectar texto en la cotización.")
        elif fuera:
            avisos.append(f"Página {i}: posible texto fuera del área visible. Revisa los bordes en el visor.")
    return avisos
