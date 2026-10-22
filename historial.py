"""Historial de cotizaciones, clientes guardados y numeración correlativa.

Todo vive en un archivo (biblioteca/cotizador.db) y los archivos de cada cotización
(Excel y PDF) se copian a biblioteca/cotizaciones/<número>/.
"""
import json
import os
import re
import shutil
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path

ESTADOS = ["Borrador", "Enviada", "Aceptada", "Rechazada"]
FORMATO_DEFECTO = "{n:04d}-COT{aa}"
_CAMPOS_COT = {"fecha", "cliente", "rut", "neto", "iva", "total", "estado", "notas", "con_iva",
               "origen", "xlsx", "pdf"}
_CAMPOS_CLI = ("nombre", "rut", "direccion", "ciudad", "telefono", "email")

# El servidor central activa este modo con GIOVAL_REQUIRE_ACCESS=true.
# Las instalaciones locales independientes conservan el comportamiento anterior.
PRIVACIDAD_COMPARTIDA = os.environ.get("GIOVAL_REQUIRE_ACCESS", "").strip().lower() in {
    "1", "true", "yes", "on"
}
_CAMPOS_LINEA_COMPARTIDA = {
    "fila", "cantidad", "descripcion", "unitario", "precio_neto", "es_producto",
    "clave_auto", "origen", "incluir",
}
_CAMPOS_ORDEN_COMPARTIDA = ("Incluir", "Cantidad", "Producto", "Precio unitario")


def _datos_compartidos(datos: dict | None) -> dict:
    """Lista blanca de los datos comerciales permitidos en la biblioteca compartida."""
    if not isinstance(datos, dict):
        return {}
    salida = {}
    lineas = datos.get("lineas")
    if isinstance(lineas, list):
        salida["lineas"] = [
            {k: v for k, v in linea.items() if k in _CAMPOS_LINEA_COMPARTIDA}
            for linea in lineas if isinstance(linea, dict)
        ]
    for campo in ("origen_pedido", "precios_modo", "paso", "editado_manual"):
        if campo in datos:
            salida[campo] = datos[campo]
    # Se conserva solo la tabla normalizada de productos; nunca el texto original
    # ni un documento de origen que pudiera contener datos del cliente.
    orden = datos.get("orden_df")
    if isinstance(orden, list):
        salida["orden_df"] = [
            {k: fila.get(k) for k in _CAMPOS_ORDEN_COMPARTIDA if k in fila}
            for fila in orden if isinstance(fila, dict)
        ]
    return salida


def _norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", t.upper())


def _s(v) -> str:
    """Texto limpio: None y NaN (de las tablas) pasan a ''."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v).strip()


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False,
                      default=lambda o: o.isoformat() if isinstance(o, (date, datetime)) else str(o))


def _ahora() -> str:
    # Microsegundos para que dos guardados en el mismo segundo tengan versiones distintas.
    return datetime.now().isoformat(timespec="microseconds")


class ConflictoActualizacion(RuntimeError):
    """La cotización cambió desde que esta sesión la abrió."""


class Historial:
    def __init__(self, carpeta="biblioteca"):
        self.dir = Path(carpeta)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = self.dir / "cotizador.db"
        self.archivos = self.dir / "cotizaciones"
        with self._con() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT);
                CREATE TABLE IF NOT EXISTS clientes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, rut TEXT DEFAULT '',
                    direccion TEXT DEFAULT '', ciudad TEXT DEFAULT '', telefono TEXT DEFAULT '',
                    email TEXT DEFAULT '', creado TEXT);
                CREATE TABLE IF NOT EXISTS cotizaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, numero TEXT UNIQUE NOT NULL, fecha TEXT,
                    cliente TEXT DEFAULT '', rut TEXT DEFAULT '', neto REAL DEFAULT 0, iva REAL DEFAULT 0,
                    total REAL DEFAULT 0, estado TEXT DEFAULT 'Borrador', notas TEXT DEFAULT '',
                    con_iva INTEGER DEFAULT 1, origen TEXT DEFAULT 'pedido', datos TEXT, xlsx TEXT,
                    pdf TEXT, creado TEXT, actualizado TEXT);
            """)
        if PRIVACIDAD_COMPARTIDA:
            self._migrar_privacidad_compartida()

    def _migrar_privacidad_compartida(self):
        """Elimina PII heredada y archivos de cotización antiguos una sola vez."""
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            marcado = con.execute(
                "SELECT valor FROM config WHERE clave='privacidad_compartida_v1'"
            ).fetchone()
            if marcado:
                return
            filas = con.execute("SELECT id, datos FROM cotizaciones").fetchall()
            for fila in filas:
                try:
                    datos = json.loads(fila["datos"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    datos = {}
                con.execute(
                    "UPDATE cotizaciones SET cliente='', rut='', notas='', xlsx=NULL, pdf=NULL, datos=? WHERE id=?",
                    (_json(_datos_compartidos(datos)), fila["id"]),
                )
            con.execute("DELETE FROM clientes")
            # Los PDF/Excel históricos pueden contener RUT, dirección, correo o teléfono.
            # Se eliminan los archivos y se conserva solo el historial comercial saneado.
            shutil.rmtree(self.archivos, ignore_errors=True)
            self.archivos.mkdir(parents=True, exist_ok=True)
            con.execute(
                "INSERT INTO config(clave, valor) VALUES('privacidad_compartida_v1', '1')"
            )
        # Compacta SQLite para que la migración no deje registros antiguos en páginas libres.
        # El archivo y sus posibles WAL quedan reducidos a la versión saneada de la biblioteca.
        with self._con() as con:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _con(self):
        con = sqlite3.connect(self.db, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA secure_delete=ON")
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------ configuración
    def config(self, clave: str, defecto=None):
        with self._con() as con:
            fila = con.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
        return fila["valor"] if fila else defecto

    def set_config(self, clave: str, valor):
        with self._con() as con:
            con.execute("INSERT INTO config(clave, valor) VALUES(?, ?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, str(valor)))

    # ------------------------------------------------------------ numeración
    def formatear(self, n: int, fecha: date | None = None, formato: str | None = None) -> str:
        fecha = fecha or date.today()
        formato = formato or self.config("formato_numero", FORMATO_DEFECTO)
        try:
            return formato.format(n=n, aa=f"{fecha.year % 100:02d}", aaaa=fecha.year)
        except (KeyError, ValueError, IndexError):
            return FORMATO_DEFECTO.format(n=n, aa=f"{fecha.year % 100:02d}", aaaa=fecha.year)

    def siguiente_correlativo(self) -> int:
        return int(self.config("siguiente_correlativo", 1))

    def previsualizar_numero(self, fecha: date | None = None) -> str:
        n = self.siguiente_correlativo()
        while self._existe(self.formatear(n, fecha)):
            n += 1
        return self.formatear(n, fecha)

    def _existe(self, numero: str) -> bool:
        with self._con() as con:
            return con.execute("SELECT 1 FROM cotizaciones WHERE numero=?", (numero,)).fetchone() is not None

    def renumerar(self, cot_id: int, nuevo_numero: str) -> str:
        """Cambia el número de una cotización y renombra los archivos asociados."""
        reg = self.obtener(cot_id)
        if not reg:
            raise ValueError("No encontré la cotización que quieres renumerar.")
        nuevo_numero = _s(nuevo_numero)
        if not nuevo_numero:
            raise ValueError("El número de cotización no puede quedar vacío.")
        if len(nuevo_numero) > 80 or any(ord(c) < 32 for c in nuevo_numero):
            raise ValueError("El número de cotización no es válido.")
        anterior = reg["numero"]
        if nuevo_numero == anterior:
            return anterior
        if self._existe(nuevo_numero):
            raise ValueError(f"Ya existe una cotización con el número «{nuevo_numero}».")

        carpeta_anterior = self._carpeta(anterior)
        carpeta_nueva = self._carpeta(nuevo_numero)
        if carpeta_nueva.exists():
            raise ValueError("Ya existe una carpeta con ese número. Elige otro número.")
        campos = {"numero": nuevo_numero}
        if carpeta_anterior.exists():
            carpeta_anterior.rename(carpeta_nueva)
            nombre_anterior = re.sub(r"[^A-Za-z0-9._-]", "_", anterior)
            nombre_nuevo = re.sub(r"[^A-Za-z0-9._-]", "_", nuevo_numero)
            for campo, extension in (("xlsx", ".xlsx"), ("pdf", ".pdf")):
                archivo_anterior = carpeta_nueva / f"{nombre_anterior}{extension}"
                if archivo_anterior.exists():
                    archivo_nuevo = carpeta_nueva / f"{nombre_nuevo}{extension}"
                    archivo_anterior.rename(archivo_nuevo)
                    campos[campo] = str(archivo_nuevo)
                elif reg.get(campo):
                    ruta = Path(reg[campo])
                    if ruta.exists() and ruta.parent == carpeta_anterior:
                        campos[campo] = str(carpeta_nueva / ruta.name)
        with self._con() as con:
            columnas = ["numero=?", "actualizado=?"]
            valores = [nuevo_numero, _ahora()]
            for campo in ("xlsx", "pdf"):
                if campo in campos:
                    columnas.append(f"{campo}=?")
                    valores.append(campos[campo])
            valores.append(cot_id)
            con.execute(f"UPDATE cotizaciones SET {', '.join(columnas)} WHERE id=?", valores)
        return nuevo_numero

    def numero_libre(self, base: str) -> str:
        """Para cotizaciones externas: si el nombre ya existe, agrega -2, -3…"""
        numero, i = base, 2
        while self._existe(numero):
            numero, i = f"{base}-{i}", i + 1
        return numero

    # ------------------------------------------------------------ cotizaciones
    def crear(self, cliente: dict, lineas: list[dict], totales: dict, con_iva: bool = True,
              fecha: date | None = None, origen: str = "pedido", numero: str | None = None) -> tuple[int, str]:
        """Registra una cotización con numeración atómica cuando hay varios usuarios simultáneos."""
        fecha = fecha or date.today()
        datos_obj = {"lineas": lineas, "cliente": cliente}
        if PRIVACIDAD_COMPARTIDA:
            datos_obj = _datos_compartidos(datos_obj)
        datos = _json(datos_obj)
        nombre_cliente = "" if PRIVACIDAD_COMPARTIDA else cliente.get("nombre", "")
        rut_cliente = "" if PRIVACIDAD_COMPARTIDA else cliente.get("rut", "")
        with self._con() as con:
            # Reservar el correlativo y crear la cotización en la misma transacción evita que
            # dos sesiones lean el mismo número antes de que alguna de ellas lo guarde.
            con.execute("BEGIN IMMEDIATE")
            if numero is None:
                fila_sig = con.execute(
                    "SELECT valor FROM config WHERE clave='siguiente_correlativo'"
                ).fetchone()
                n = int(fila_sig["valor"]) if fila_sig else 1
                fila_fmt = con.execute(
                    "SELECT valor FROM config WHERE clave='formato_numero'"
                ).fetchone()
                formato = fila_fmt["valor"] if fila_fmt else FORMATO_DEFECTO

                def numero_formateado(correlativo: int) -> str:
                    try:
                        return formato.format(
                            n=correlativo,
                            aa=f"{fecha.year % 100:02d}",
                            aaaa=fecha.year,
                        )
                    except (KeyError, ValueError, IndexError):
                        return FORMATO_DEFECTO.format(
                            n=correlativo,
                            aa=f"{fecha.year % 100:02d}",
                            aaaa=fecha.year,
                        )

                numero = numero_formateado(n)
                while con.execute(
                    "SELECT 1 FROM cotizaciones WHERE numero=?", (numero,)
                ).fetchone():
                    n += 1
                    numero = numero_formateado(n)
                con.execute(
                    "INSERT INTO config(clave, valor) VALUES('siguiente_correlativo', ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                    (str(n + 1),),
                )

            cur = con.execute(
                "INSERT INTO cotizaciones(numero, fecha, cliente, rut, neto, iva, total, con_iva, origen, datos, "
                "creado, actualizado) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (numero, fecha.isoformat(), nombre_cliente, rut_cliente,
                 totales.get("neto", 0), totales.get("iva", 0), totales.get("total", 0), int(con_iva), origen,
                 datos, _ahora(), _ahora()))
        return cur.lastrowid, numero

    def actualizar(self, cot_id: int, datos: dict | None = None, **campos):
        """Actualiza los campos permitidos sin control de versión (para acciones puntuales)."""
        self._actualizar(cot_id, datos=datos, version_esperada=None, **campos)

    def actualizar_si_version(self, cot_id: int, version_esperada: str,
                              datos: dict | None = None, **campos):
        """Guarda solo si nadie cambió la cotización desde la versión que leyó el usuario."""
        if not version_esperada:
            raise ConflictoActualizacion(
                "No conozco la versión que abriste. Recarga la cotización antes de guardar para evitar sobrescribir cambios."
            )
        self._actualizar(cot_id, datos=datos, version_esperada=version_esperada, **campos)

    def _actualizar(self, cot_id: int, datos: dict | None = None,
                    version_esperada: str | None = None, **campos):
        sets, valores = [], []
        for k, v in campos.items():
            if PRIVACIDAD_COMPARTIDA and k in {"cliente", "rut", "notas", "xlsx", "pdf"}:
                continue
            if k in _CAMPOS_COT:
                sets.append(f"{k}=?")
                valores.append(v.isoformat() if isinstance(v, date) else v)
        if datos is not None:
            if PRIVACIDAD_COMPARTIDA:
                datos = _datos_compartidos(datos)
            sets.append("datos=?")
            valores.append(_json(datos))
        if not sets:
            return
        marca_tiempo = _ahora()
        sets.append("actualizado=?")
        valores += [marca_tiempo, cot_id]
        with self._con() as con:
            # Bloquea brevemente otros escritores mientras compara la versión y guarda.
            con.execute("BEGIN IMMEDIATE")
            if version_esperada is not None:
                fila = con.execute(
                    "SELECT actualizado FROM cotizaciones WHERE id=?", (cot_id,)
                ).fetchone()
                if not fila:
                    raise ConflictoActualizacion(
                        "La cotización ya no existe. Recarga el historial antes de continuar."
                    )
                version_actual = fila["actualizado"] or ""
                if version_actual != version_esperada:
                    raise ConflictoActualizacion(
                        "Otra sesión modificó esta cotización desde que la abriste. "
                        "Tus cambios siguen en esta pantalla; recarga la versión compartida "
                        "o guárdalos como una cotización nueva."
                    )
            cur = con.execute(f"UPDATE cotizaciones SET {', '.join(sets)} WHERE id=?", valores)
            if cur.rowcount != 1:
                raise ConflictoActualizacion("No pude guardar la cotización porque ya no existe.")

    def obtener(self, cot_id: int) -> dict | None:
        with self._con() as con:
            fila = con.execute("SELECT * FROM cotizaciones WHERE id=?", (cot_id,)).fetchone()
        if not fila:
            return None
        reg = dict(fila)
        reg["datos"] = json.loads(reg["datos"] or "{}")
        if PRIVACIDAD_COMPARTIDA:
            reg["cliente"] = reg["rut"] = reg["notas"] = ""
            reg["xlsx"] = reg["pdf"] = None
            reg["datos"] = _datos_compartidos(reg["datos"])
        return reg

    def listar(self, texto: str = "", estado: str | None = None) -> list[dict]:
        consulta, args = "SELECT * FROM cotizaciones", []
        cond = []
        if estado and estado != "Todos":
            cond.append("estado=?")
            args.append(estado)
        if texto.strip():
            cond.append("(numero LIKE ? OR cliente LIKE ? OR rut LIKE ? OR notas LIKE ?)")
            args += [f"%{texto.strip()}%"] * 4
        if cond:
            consulta += " WHERE " + " AND ".join(cond)
        with self._con() as con:
            filas = con.execute(consulta + " ORDER BY id DESC", args).fetchall()
        salida = [dict(f) for f in filas]
        if PRIVACIDAD_COMPARTIDA:
            for reg in salida:
                reg["cliente"] = reg["rut"] = reg["notas"] = ""
                reg["xlsx"] = reg["pdf"] = None
                try:
                    reg["datos"] = _json(_datos_compartidos(json.loads(reg.get("datos") or "{}")))
                except (TypeError, json.JSONDecodeError):
                    reg["datos"] = "{}"
        return salida

    def eliminar(self, cot_id: int):
        reg = self.obtener(cot_id)
        with self._con() as con:
            con.execute("DELETE FROM cotizaciones WHERE id=?", (cot_id,))
        if reg:
            shutil.rmtree(self._carpeta(reg["numero"]), ignore_errors=True)

    # ------------------------------------------------------------ archivos
    def _carpeta(self, numero: str) -> Path:
        return self.archivos / re.sub(r"[^A-Za-z0-9._-]", "_", numero)

    def guardar_archivos(self, cot_id: int, xlsx: str | Path | None = None, pdf: bytes | None = None):
        # En modo compartido los documentos llevan datos introducidos en cada sesión;
        # se descargan desde la pantalla, pero nunca se archivan en el servidor común.
        if PRIVACIDAD_COMPARTIDA:
            return
        reg = self.obtener(cot_id)
        if not reg:
            return
        carpeta = self._carpeta(reg["numero"])
        carpeta.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"[^A-Za-z0-9._-]", "_", reg["numero"])
        campos = {}
        if xlsx:
            destino = carpeta / f"{base}.xlsx"
            shutil.copy(xlsx, destino)
            campos["xlsx"] = str(destino)
        if pdf:
            destino = carpeta / f"{base}.pdf"
            destino.write_bytes(pdf)
            campos["pdf"] = str(destino)
        if campos:
            self.actualizar(cot_id, **campos)

    # ------------------------------------------------------------ clientes
    def clientes(self) -> list[dict]:
        if PRIVACIDAD_COMPARTIDA:
            return []
        with self._con() as con:
            return [dict(f) for f in con.execute("SELECT * FROM clientes ORDER BY nombre COLLATE NOCASE").fetchall()]

    def guardar_cliente(self, cliente: dict) -> int | None:
        """Crea o actualiza (por RUT, o por nombre si no hay RUT). Devuelve el id."""
        if PRIVACIDAD_COMPARTIDA:
            return None
        nombre = (cliente.get("nombre") or "").strip()
        if not nombre:
            return None
        valores = {k: (cliente.get(k) or "").strip() for k in _CAMPOS_CLI}
        existente = None
        for c in self.clientes():
            if (valores["rut"] and _norm(c["rut"]) == _norm(valores["rut"])) or \
                    (not valores["rut"] and _norm(c["nombre"]) == _norm(nombre)):
                existente = c
                break
        with self._con() as con:
            if existente:
                # no pisa con vacíos lo que ya se sabía
                final = {k: valores[k] or existente[k] for k in _CAMPOS_CLI}
                con.execute("UPDATE clientes SET nombre=?, rut=?, direccion=?, ciudad=?, telefono=?, email=? "
                            "WHERE id=?", (*[final[k] for k in _CAMPOS_CLI], existente["id"]))
                return existente["id"]
            cur = con.execute("INSERT INTO clientes(nombre, rut, direccion, ciudad, telefono, email, creado) "
                              "VALUES(?,?,?,?,?,?,?)", (*[valores[k] for k in _CAMPOS_CLI], _ahora()))
            return cur.lastrowid

    def reemplazar_clientes(self, filas: list[dict]):
        """Aplica lo editado en la tabla: actualiza, agrega y borra los que ya no están."""
        if PRIVACIDAD_COMPARTIDA:
            return
        ids_finales = set()
        with self._con() as con:
            for f in filas:
                nombre = _s(f.get("nombre"))
                if not nombre:
                    continue
                vals = [_s(f.get(k)) for k in _CAMPOS_CLI]
                cid = f.get("id")
                if _s(cid) not in ("", "nan", "None"):
                    con.execute("UPDATE clientes SET nombre=?, rut=?, direccion=?, ciudad=?, telefono=?, email=? "
                                "WHERE id=?", (*vals, int(cid)))
                    ids_finales.add(int(cid))
                else:
                    cur = con.execute("INSERT INTO clientes(nombre, rut, direccion, ciudad, telefono, email, creado) "
                                      "VALUES(?,?,?,?,?,?,?)", (*vals, _ahora()))
                    ids_finales.add(cur.lastrowid)
            existentes = {r["id"] for r in con.execute("SELECT id FROM clientes").fetchall()}
            for cid in existentes - ids_finales:
                con.execute("DELETE FROM clientes WHERE id=?", (cid,))
