"""Identidad comercial conservadora. No calcula precios ni presume costos.

Las claves descriptivas internas no se tratan como SKU del fabricante.
preciso significa que hay atributos para comprobar un comparable; nunca
confirma por sí mismo la equivalencia de una página web.
"""
from __future__ import annotations
import re
import unicodedata


def _normalizar(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    return re.sub(r"\s+", " ", texto.encode("ascii", "ignore").decode().upper()).strip()


MARCAS = ("HP", "EPSON", "BROTHER", "KYOCERA", "SAMSUNG", "CANON", "LEXMARK",
          "XEROX", "RICOH", "LOGITECH", "KINGSTON", "DURACELL", "ENERGIZER",
          "EQUALIT", "CHAMEX", "REPORT", "FABER CASTELL", "STABILO", "BIC",
          "TORRE", "FULTONS")


def _marca(texto):
    return next((m for m in MARCAS if re.search(r"\b" + re.escape(m) + r"\b", texto)), "")


def _familia(texto):
    if re.search(r"\bTINTAS?\s+(?:PARA\s+)?TIMBRES?\b", texto):
        return "escritura"
    if re.search(r"\bBOLSAS?\b.*\bEPSON\b.*\bDURABRITE\b", texto):
        return "tinta"
    if re.search(r"\b(?:SOPORTES?|BASE|ELEVADOR|ATRIL)\b.*\b(?:MONITOR|NOTEBOOK)\b|\b(?:ALFOMBRILLA|APOYA MUNECAS|REPOSAMUNECAS)\b", texto):
        return "perifericos"
    if re.search(r"\bPISTOLA\b.*\bSILICONA\b", texto):
        return "oficina"
    reglas = (
        ("tambor", r"\b(?:DRUM|TAMBOR|UNIDAD DE IMAGEN)\b"),
        ("toner", r"\bTONER(?:S)?\b"),
        ("tinta", r"\b(?:TINTA|CARTUCHO)(?:S)?\b"),
        ("resmas", r"\bRESMAS?\b"),
        ("equipos_impresion", r"\b(?:IMPRESORAS?|PLOTTER)\b"),
        ("perifericos", r"\b(?:MOUSE|TECLADOS?|MICROFONOS?|AUDIFONOS?|AURICULARES?|PENDRIVES?|MEMORIA USB|PUNTEROS?|CABLE USB|ADAPTADOR USB|HUB USB)\b"),
        ("tecnologia", r"\b(?:NOTEBOOK|LAPTOP|MONITOR(?:ES)?|PROYECTOR(?:ES)?|SSD|DISCO DURO|MICRO SD|MICROSD|UNIDAD DE ALMACENAMIENTO)\b"),
        ("pilas", r"\b(?:PILAS?|BATERIA ALCALINA)\b"),
        ("escritura", r"\b(?:LAPIZ|LAPICES|LAPICER[AO]S?|PORTAMINAS?|REPUESTO DE MINAS|PLUMON(?:ES)?|DESTACADOR(?:ES)?|BOLIGRAFOS?|MARCADOR(?:ES)?|CORRECTOR(?:ES)?|CORRECTORAS?|GOMAS? DE BORRAR|BORRADOR(?:ES)?(?: DE PIZARRA)?)\b"),
        ("organizacion", r"\b(?:ARCHIVADOR(?:ES)?|CARPETAS?|CUADERNOS?|LIBRETAS?|SOBRES?|FUNDAS?|ANOTADOR(?:ES)?|CAJAS? DE ARCHIVO|ORGANIZADOR(?:ES)?|ORGANIZADORAS?|BANDEJAS?|PORTALAPICES|CALENDARIOS?|BANDERITAS|SEPARADOR(?:ES)?)\b|\bTABLA CON APRETADOR\b"),
        ("adhesivos", r"\b(?:PEGAMENTOS?|ADHESIV[OA]S?|CINTAS?|CORCHETES?|GRAPAS?|CLIPS?|ACCOCLIP|SILICONA|STICK FIX|STIK FIX)\b"),
        ("manualidades", r"\bGOMAS? EVA\b"),
        ("oficina", r"\b(?:TIJERAS?|CUCHILLOS? CARTONEROS?|REGLAS?|CORCHETERAS?|ENGRAPADORAS?|SACACORCHETES|CALCULADORAS?|HUELLEROS?|TIMBRES?|PLASTIFICADORAS?|LAMINADORAS?|TERMOLAMINAR|PLASTIFICADORAS?|GUILLOTINAS?|ALFILER|PIZARRAS?)\b"),
        ("papel", r"\b(?:PAPEL|HOJAS?|OPALINA|CARTULINA|ETIQUETAS?|BLOK|BLOC)\b"),
    )
    familia = next((familia for familia, patron in reglas if re.search(patron, texto)), "general")
    if familia == "general" and re.search(r"\bHP\b.{0,40}?\b\d{1,3}\s*[- ]?\s*[AX]\b", texto):
        # Nombres heredados como «HP Laser Jet Print Carriage 55-A».
        return "toner"
    return familia


def _modelo(texto, familia):
    if familia in {"toner", "tinta", "tambor"}:
        patrones = (
            r"\b((?:CF|CE|CB|CC|CH|CZ|Q|W)\s*[- ]?\s*\d{3,7}[A-Z]{1,3})\b",
            r"\b((?:TN|DR|TK)\s*[- ]?\s*\d{3,5}[A-Z]{0,2})\b",
            r"\b(MLT\s*[- ]?\s*D\d{3}[A-Z]{0,2})\b",
            r"\b(T\d{3,7}[A-Z]{0,3})\b",
        )
        for patron in patrones:
            encontrado = re.search(patron, texto)
            if encontrado:
                return re.sub(r"[\s-]", "", encontrado.group(1))
        hp = re.search(r"\bHP\b.{0,40}?\b(\d{1,3})\s*[- ]?\s*([AX](?:L)?)\b", texto)
        if hp:
            color = next((c for c in ("NEGRO", "COLOR", "TRICOLOR") if re.search(r"\b" + c + r"\b", texto)), "") if familia == "tinta" else ""
            return "HP" + hp.group(1) + hp.group(2) + (("-" + color) if color else "")
        epson = re.search(r"\bEPSON\s*(?:N(?:RO)?[. ]*)?(\d{3})\b", texto)
        if epson:
            color = next((c for c in ("BK", "NEGRO", "CIAN", "MAGENTA", "AMARILLO", "C", "M", "Y")
                          if re.search(r"\b" + c + r"\b", texto[epson.end():])), "")
            return "EPSON" + epson.group(1) + (("-" + color) if color else "")
        samsung = re.search(r"\bSAMSUNG\s*(\d{3})\s*([LSUE])\b", texto)
        if samsung:
            return "SAMSUNG" + samsung.group(1) + samsung.group(2)
    if familia == "perifericos" and _marca(texto):
        candidato = re.search(r"\b((?:M|MX|K|MK|H|C|G|B)\d{2,4}[A-Z]?)\b", texto)
        if candidato:
            return candidato.group(1)
    return ""


def _variante(texto):
    if re.search(r"\b(?:O\s+(?:SU\s+)?EQUIVALENTE|O\s+SIMILAR|ORIGINAL\s+O|ORIGINAL/)\b", texto):
        return "no_especificada", True
    if re.search(r"\b(?:ALTERNATIV[OA]S?|COMPATIBLES?|GENERIC[OA]S?|REMANUFACTURAD[OA])\b", texto):
        return "alternativo", False
    if re.search(r"\b(?:ORIGINAL(?:ES)?|OEM|GENUIN[OA])\b", texto):
        return "original", False
    return "no_especificada", False


def _presentacion(texto, familia):
    avisos = []
    limpio = texto
    pedido = re.search(r"\b(?:SE REQUIERE(?: LA)?(?: COMPRAR)?|SE SOLICITA|SE NECESITA|COMPRAR|CANTIDAD SOLICITADA)\s*(?:DE\s*)?\d+", limpio)
    if pedido:
        limpio = limpio[:pedido.start()]
        avisos.append("La cantidad del pedido no se interpreta como presentación del producto.")
    if re.match(r"^\d+\s+(?:ARCHIVADORES|RESMAS|LAPICES|CARPETAS|UNIDADES)\b", limpio):
        limpio = re.sub(r"^\d+\s+", "", limpio)
        avisos.append("La cantidad inicial parece corresponder al pedido, no a un pack.")
    hojas = re.search(r"\b(\d{2,5})\s*HOJAS\b", limpio)
    cantidad_hojas = int(hojas.group(1)) if hojas else None
    caja = re.search(r"\bCAJA(?:\s+DE)?\s*\(?\s*(?:X\s*)?(\d{1,5})\s*(UNIDADES|RESMAS|HOJAS)?\b", limpio)
    pack = re.search(r"\b(PACK|SET|JUEGO|BLISTER)(?:\s+DE)?\s*\(?\s*(?:X\s*)?(\d{1,4})\s*(UNIDADES|COLORES|PIEZAS)?\b", limpio)
    if caja:
        cantidad = int(caja.group(1))
        unidad = (caja.group(2) or "unidades").lower()
        if familia == "resmas" and unidad != "hojas":
            unidad = "resmas"
        valor = f"caja de {cantidad} {unidad}"
        if cantidad_hojas and unidad == "resmas":
            valor += f" de {cantidad_hojas} hojas"
        return valor, cantidad > 0, avisos
    if pack:
        valor = f"{pack.group(1).lower()} de {int(pack.group(2))} {(pack.group(3) or 'unidades').lower()}"
        return valor, int(pack.group(2)) > 0, avisos
    recipiente = re.search(r"\b(PACK|SET|JUEGO|BLISTER|CAJA)\b", limpio)
    if recipiente:
        colores = sum(bool(re.search(r"\b" + c + r"\b", limpio))
                      for c in ("NEGRO", "CIAN", "MAGENTA", "AMARILLO"))
        if recipiente.group(1) == "PACK" and colores == 4:
            return "pack de 4 colores", True, avisos
        avisos.append("Falta la cantidad que incluye el pack o la caja.")
        return recipiente.group(1).lower() + " (cantidad no especificada)", False, avisos
    parentesis = re.search(r"\(\s*(\d{1,5})\s*UNIDADES?\s*\)", limpio)
    if parentesis:
        return f"presentación de {int(parentesis.group(1))} unidades", True, avisos
    if familia == "resmas":
        if cantidad_hojas:
            return f"resma de {cantidad_hojas} hojas", True, avisos
        avisos.append("Falta indicar cuántas hojas incluye cada resma.")
        return "resma (hojas no especificadas)", False, avisos
    if familia == "papel" and cantidad_hojas:
        return f"presentación de {cantidad_hojas} hojas", True, avisos
    volumen = re.search(r"\b(\d{1,4}(?:[.,]\d+)?)\s*(ML|LITROS?|GRAMOS?|GR|G)\b", limpio)
    if volumen:
        return f"unidad de {volumen.group(1)} {volumen.group(2).lower()}", True, avisos
    if re.search(r"\b(?:1\s+UNIDAD|POR UNIDAD|UNIDAD INDIVIDUAL)\b", limpio):
        return "unidad", True, avisos
    if familia in {"toner", "tinta", "tambor", "perifericos"}:
        avisos.append("Presentación individual inferida; descartar comparables que sean packs.")
        return "unidad", True, avisos
    avisos.append("La presentación no está suficientemente especificada.")
    return "no_especificada", False, avisos


def clasificar(clave, ficha):
    ficha = ficha if isinstance(ficha, dict) else {}
    nombre = _normalizar(ficha.get("nombre"))
    aliases = ficha.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    aliases = [_normalizar(a) for a in aliases if isinstance(a, str)]
    principal = nombre or (aliases[0] if aliases else "")
    familia = _familia(principal)
    if familia == "general":
        familia = next((_familia(a) for a in aliases if _familia(a) != "general"), "general")
    variante, ambiguo = _variante(principal)
    advertencias = []
    if ambiguo:
        advertencias.append("«O equivalente» no permite distinguir original de alternativo.")
    modelo = _modelo(principal, familia)
    if not modelo:
        modelo = next((_modelo(a, familia) for a in aliases if _modelo(a, familia)), "")
    if variante == "no_especificada" and not ambiguo:
        variantes_alias = {_variante(a)[0] for a in aliases} - {"no_especificada"}
        if len(variantes_alias) == 1:
            variante = variantes_alias.pop()
        elif len(variantes_alias) > 1:
            advertencias.append("Los alias contienen variantes originales y alternativas; revisar.")
    presentacion, clara, avisos = _presentacion(principal, familia)
    advertencias.extend(avisos)
    if not modelo:
        advertencias.append("No se identificó un modelo de fabricante suficiente para comparar precios automáticamente.")
    if familia == "tambor" and modelo.startswith("TN"):
        advertencias.append("TN suele identificar el tóner; falta confirmar el modelo del tambor.")
    impresion = familia in {"toner", "tinta", "tambor"}
    rubro = ("Impresión y consumibles" if impresion else
             "Equipos de impresión" if familia == "equipos_impresion" else
             "Tecnología y periféricos" if familia == "perifericos" else
             "Tecnología y computación" if familia == "tecnologia" else
             "Papelería y manualidades" if familia == "manualidades" else
             "Suministros de oficina" if familia != "general" else "Productos generales")
    preciso = bool(modelo and clara and familia != "general")
    if impresion and (variante == "no_especificada" or (familia == "tambor" and modelo.startswith("TN"))):
        preciso = False
    consulta = re.sub(r"\b(?:SE REQUIERE|SE SOLICITA|SE NECESITA|COMPRAR)\b.*$", "", principal).strip(" ,.;")
    if not consulta:
        consulta = _normalizar(clave).replace("-", " ")
    if modelo and modelo not in re.sub(r"[\s-]", "", consulta):
        consulta += " " + modelo
    if variante != "no_especificada" and not re.search(r"\b(?:ORIGINAL|ALTERNATIV[OA]|COMPATIBLE)\b", consulta):
        consulta += " " + variante.upper()
    return {"rubro": rubro, "familia": familia, "modelo": modelo,
            "variante": variante, "presentacion": presentacion,
            "consulta": consulta[:300], "preciso": preciso,
            "advertencias": list(dict.fromkeys(advertencias))}
