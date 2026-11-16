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
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from anexos_cotizacion import limpiar_referencias

ESTADOS = ["Borrador", "Enviada", "Aceptada", "Rechazada"]
FORMATO_DEFECTO = "{n:04d}-COT{aa}"
_CAMPOS_COT = {"fecha", "cliente", "rut", "neto", "iva", "total", "estado", "notas", "con_iva",
               "origen", "xlsx", "pdf"}
_CAMPOS_CLI = ("nombre", "rut", "direccion", "ciudad", "telefono", "email")
_SIN_VERSION_CONFIG = object()

# El servidor central activa este modo con GIOVAL_REQUIRE_ACCESS=true.
# Las instalaciones locales independientes conservan el comportamiento anterior.
PRIVACIDAD_COMPARTIDA = os.environ.get("GIOVAL_REQUIRE_ACCESS", "").strip().lower() in {
    "1", "true", "yes", "on"
}

PERFILES = os.getenv("GIOVAL_PROFILES", "").strip().lower() in {"1", "true", "yes", "on"}
if PERFILES:
    PRIVACIDAD_COMPARTIDA = False

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
    if "anexos" in datos:
        salida["anexos"] = limpiar_referencias(datos["anexos"])
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
        self.actor = "Equipo"
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
                CREATE TABLE IF NOT EXISTS versiones_cotizacion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, cot_id INTEGER NOT NULL,
                    fecha TEXT NOT NULL, etiqueta TEXT NOT NULL, contenido TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS versiones_por_cotizacion
                    ON versiones_cotizacion(cot_id, id);
                CREATE TABLE IF NOT EXISTS costos_proveedor (
                    proveedor TEXT PRIMARY KEY, actualizado TEXT NOT NULL, costos TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS seguimiento_cotizacion (
                    cot_id INTEGER PRIMARY KEY, vence TEXT, ultimo_contacto TEXT);
                CREATE TABLE IF NOT EXISTS cambios_comerciales (
                    id INTEGER PRIMARY KEY, fecha TEXT NOT NULL, actor TEXT NOT NULL,
                    entidad TEXT NOT NULL, objeto TEXT NOT NULL, campo TEXT NOT NULL,
                    anterior TEXT, nuevo TEXT);
            """)
        if PRIVACIDAD_COMPARTIDA:
            self._migrar_privacidad_compartida()
            self._sanear_versiones()

    def _sanear_versiones(self):
        """Aplica el mismo límite de privacidad a versiones creadas en otro modo."""
        clave = "privacidad_versiones_v1"
        if self.config(clave):
            return
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            filas = con.execute("SELECT id, contenido FROM versiones_cotizacion").fetchall()
            for fila in filas:
                contenido = json.loads(fila["contenido"])
                contenido.update(cliente="", rut="", datos=_datos_compartidos(contenido.get("datos")))
                con.execute("UPDATE versiones_cotizacion SET contenido=? WHERE id=?",
                            (_json(contenido), fila["id"]))
            con.execute("INSERT OR IGNORE INTO config(clave, valor) VALUES(?, '1')", (clave,))

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

    @contextmanager
    def _con(self):
        con = sqlite3.connect(self.db, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA secure_delete=ON")
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ------------------------------------------------------------ configuración
    def config(self, clave: str, defecto=None):
        with self._con() as con:
            fila = con.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
        return fila["valor"] if fila else defecto

    def set_config(self, clave: str, valor, *, anterior=_SIN_VERSION_CONFIG):
        with self._con() as con:
            if anterior is not _SIN_VERSION_CONFIG:
                con.execute("BEGIN IMMEDIATE")
                fila = con.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
                if (fila["valor"] if fila else None) != anterior:
                    raise ConflictoActualizacion("La configuración cambió en otra sesión. Recárgala antes de guardar.")
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

    def renumerar(self, cot_id: int, nuevo_numero: str, version_esperada: str | None = None) -> str:
        """Cambia el número de una cotización; el modo compartido exige la versión abierta."""
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

        if PRIVACIDAD_COMPARTIDA or PERFILES:
            if not version_esperada:
                raise ConflictoActualizacion(
                    "No conozco la versión de la cotización que abriste. Recarga antes de cambiar el número."
                )
            # Las rutas de documentos existentes siguen apuntando a sus archivos.
            with self._con() as con:
                con.execute("BEGIN IMMEDIATE")
                actual = con.execute(
                    "SELECT numero, actualizado FROM cotizaciones WHERE id=?", (cot_id,)
                ).fetchone()
                if not actual:
                    raise ConflictoActualizacion("La cotización ya no existe.")
                if (actual["actualizado"] or "") != version_esperada:
                    raise ConflictoActualizacion(
                        "Otra sesión modificó esta cotización. Recarga la versión compartida antes de cambiar el número."
                    )
                duplicado = con.execute(
                    "SELECT 1 FROM cotizaciones WHERE numero=? AND id<>?", (nuevo_numero, cot_id)
                ).fetchone()
                if duplicado:
                    raise ValueError(f"Ya existe una cotización con el número «{nuevo_numero}».")
                cur = con.execute(
                    "UPDATE cotizaciones SET numero=?, actualizado=? WHERE id=? AND actualizado=?",
                    (nuevo_numero, _ahora(), cot_id, actual["actualizado"]),
                )
                if cur.rowcount != 1:
                    raise ConflictoActualizacion(
                        "Otra sesión modificó esta cotización. Recarga antes de cambiar el número."
                    )
            return nuevo_numero
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
                if not reg.get(campo):
                    continue
                ruta = Path(reg[campo])
                movido = carpeta_nueva / ruta.name
                if ruta.parent != carpeta_anterior or not movido.is_file():
                    continue
                if ruta.name == f"{nombre_anterior}{extension}":
                    nuevo = carpeta_nueva / f"{nombre_nuevo}{extension}"
                    movido.rename(nuevo)
                    movido = nuevo
                campos[campo] = str(movido)
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
            anterior = con.execute("SELECT * FROM cotizaciones WHERE id=?", (cot_id,)).fetchone()
            if version_esperada is not None:
                fila = anterior
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
            for campo in ("neto", "iva", "total", "estado", "fecha"):
                if campo in campos:
                    valor = campos[campo]
                    nuevo = valor.isoformat() if isinstance(valor, date) else valor
                    if anterior[campo] != nuevo:
                        self._auditar(con, "Cotización", anterior["numero"], campo, anterior[campo], nuevo)
            if datos is not None:
                previo = json.loads(anterior["datos"] or "{}")
                for campo in ("lineas", "anexos"):
                    if previo.get(campo) != datos.get(campo):
                        def resumen(valor):
                            return {"cantidad": len(valor or [])}
                        self._auditar(con, "Cotización", anterior["numero"], campo,
                                      resumen(previo.get(campo)), resumen(datos.get(campo)))

    def _auditar(self, con, entidad, objeto, campo, anterior, nuevo):
        con.execute(
            "INSERT INTO cambios_comerciales(fecha,actor,entidad,objeto,campo,anterior,nuevo) VALUES (?,?,?,?,?,?,?)",
            (_ahora(), self.actor, entidad, str(objeto), campo, _json(anterior), _json(nuevo)),
        )

    def registrar_cambio(self, entidad, objeto, campo, anterior, nuevo):
        with self._con() as con:
            self._auditar(con, entidad, objeto, campo, anterior, nuevo)

    def cambios_comerciales(self, limite=150):
        with self._con() as con:
            return [dict(fila) for fila in con.execute(
                "SELECT fecha, actor, entidad, objeto, campo, anterior, nuevo "
                "FROM cambios_comerciales ORDER BY id DESC LIMIT ?", (limite,),
            )]

    def lista_proveedor(self, proveedor):
        with self._con() as con:
            fila = con.execute("SELECT costos FROM costos_proveedor WHERE proveedor=?", (proveedor,)).fetchone()
        return json.loads(fila["costos"]) if fila else {}

    def guardar_lista_proveedor(self, proveedor, precios, previa):
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            fila = con.execute("SELECT costos FROM costos_proveedor WHERE proveedor=?", (proveedor,)).fetchone()
            if (json.loads(fila["costos"]) if fila else {}) != previa:
                raise ConflictoActualizacion("La lista de este proveedor cambió. Revisa la comparación antes de guardar.")
            con.execute("INSERT INTO costos_proveedor VALUES (?,?,?) ON CONFLICT(proveedor) "
                        "DO UPDATE SET actualizado=excluded.actualizado,costos=excluded.costos",
                        (proveedor, _ahora(), _json(precios)))
            self._auditar(con, "Proveedor", proveedor, "Lista de costos", len(previa), len(precios))

    def seguimientos(self):
        with self._con() as con:
            return {fila["cot_id"]: dict(fila) for fila in con.execute("SELECT * FROM seguimiento_cotizacion")}

    def guardar_seguimiento(self, cot_id, vencimiento, ultimo_contacto):
        vence = date.fromisoformat(vencimiento).isoformat() if vencimiento else None
        contacto = date.fromisoformat(ultimo_contacto).isoformat() if ultimo_contacto else None
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            actual = con.execute("SELECT numero FROM cotizaciones WHERE id=?", (cot_id,)).fetchone()
            if not actual:
                raise ConflictoActualizacion("La cotización ya no existe.")
            previo = con.execute("SELECT vence, ultimo_contacto FROM seguimiento_cotizacion WHERE cot_id=?",
                                 (cot_id,)).fetchone()
            con.execute("INSERT INTO seguimiento_cotizacion VALUES (?,?,?) ON CONFLICT(cot_id) "
                        "DO UPDATE SET vence=excluded.vence,ultimo_contacto=excluded.ultimo_contacto",
                        (cot_id, vence, contacto))
            self._auditar(con, "Cotización", actual["numero"], "Seguimiento",
                          dict(previo) if previo else None, {"vence": vence, "ultimo_contacto": contacto})

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

    def listar(self, texto: str = "", estado: str | None = None, *, incluir_datos: bool = True) -> list[dict]:
        """Lista cotizaciones. Las vistas de resumen pueden omitir el JSON de las líneas."""
        columnas = "*" if incluir_datos else "id, numero, fecha, cliente, rut, total, estado, notas, pdf, actualizado, creado"
        consulta, args = f"SELECT {columnas} FROM cotizaciones", []
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
                reg["pdf"] = None
                if incluir_datos:
                    reg["xlsx"] = None
                    try:
                        reg["datos"] = _json(_datos_compartidos(json.loads(reg.get("datos") or "{}")))
                    except (TypeError, json.JSONDecodeError):
                        reg["datos"] = "{}"
        return salida

    def eliminar(self, cot_id: int):
        reg = self.obtener(cot_id)
        with self._con() as con:
            con.execute("DELETE FROM versiones_cotizacion WHERE cot_id=?", (cot_id,))
            con.execute("DELETE FROM seguimiento_cotizacion WHERE cot_id=?", (cot_id,))
            con.execute("DELETE FROM cotizaciones WHERE id=?", (cot_id,))
        if reg:
            shutil.rmtree(self._carpeta(reg["numero"]), ignore_errors=True)

    # ------------------------------------------------------------ archivos
    def _carpeta(self, numero: str) -> Path:
        return self.archivos / re.sub(r"[^A-Za-z0-9._-]", "_", numero)

    @staticmethod
    def _registrar_version(con, fila, etiqueta):
        contenido = {k: fila[k] for k in ("fecha", "cliente", "rut", "neto", "iva", "total",
                                        "con_iva", "origen", "datos")}
        contenido["datos"] = json.loads(contenido["datos"] or "{}")
        if PRIVACIDAD_COMPARTIDA:
            contenido.update(cliente="", rut="", datos=_datos_compartidos(contenido["datos"]))
        con.execute(
            "INSERT INTO versiones_cotizacion(cot_id, fecha, etiqueta, contenido) VALUES (?, ?, ?, ?)",
            (fila["id"], _ahora(), etiqueta, _json(contenido)),
        )

    def guardar_version(self, cot_id, version_esperada, etiqueta):
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            fila = con.execute("SELECT * FROM cotizaciones WHERE id=?", (cot_id,)).fetchone()
            if not fila or not version_esperada or fila["actualizado"] != version_esperada:
                raise ConflictoActualizacion("Recarga la cotización antes de guardar su versión.")
            self._registrar_version(con, fila, etiqueta)

    def versiones(self, cot_id):
        with self._con() as con:
            filas = con.execute(
                "SELECT id, fecha, etiqueta, contenido FROM versiones_cotizacion WHERE cot_id=? ORDER BY id DESC",
                (cot_id,),
            ).fetchall()
        versiones = []
        for fila in filas:
            contenido = json.loads(fila["contenido"])
            if PRIVACIDAD_COMPARTIDA:
                contenido.update(cliente="", rut="", datos=_datos_compartidos(contenido.get("datos")))
            versiones.append({**dict(fila), "contenido": contenido})
        return versiones

    def restaurar_version(self, cot_id, version_id, version_esperada):
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            actual = con.execute("SELECT * FROM cotizaciones WHERE id=?", (cot_id,)).fetchone()
            if not actual or not version_esperada or actual["actualizado"] != version_esperada:
                raise ConflictoActualizacion("La cotización cambió. Recárgala antes de restaurar.")
            fila = con.execute(
                "SELECT contenido FROM versiones_cotizacion WHERE cot_id=? AND id=?", (cot_id, version_id),
            ).fetchone()
            if not fila:
                raise ValueError("No encontré la versión de esta cotización.")
            guardada = json.loads(fila["contenido"])
            datos = guardada["datos"]
            if PRIVACIDAD_COMPARTIDA:
                guardada.update(cliente="", rut="")
                datos = _datos_compartidos(datos)
            if datos.get("lineas"):
                datos.update(paso=2, editado_manual=True)
            self._registrar_version(con, actual, "Antes de restaurar")
            con.execute(
                """UPDATE cotizaciones SET fecha=?, cliente=?, rut=?, neto=?, iva=?, total=?,
                   con_iva=?, origen=?, datos=?, actualizado=?, estado='Borrador', xlsx=NULL, pdf=NULL
                   WHERE id=?""",
                [*(guardada[k] for k in ("fecha", "cliente", "rut", "neto", "iva", "total", "con_iva", "origen")),
                 _json(datos), _ahora(), cot_id],
            )

    def guardar_archivos(self, cot_id: int, xlsx=None, pdf=None, version_esperada=None, etiqueta_version=None):
        if PRIVACIDAD_COMPARTIDA:
            if etiqueta_version:
                self.guardar_version(cot_id, version_esperada, etiqueta_version)
            return
        if not xlsx and pdf is None:
            return
        # El mismo bloqueo de escritura protege la versión y la publicación de archivos.
        with self._con() as con:
            con.execute("BEGIN IMMEDIATE")
            reg = con.execute("SELECT * FROM cotizaciones WHERE id=?", (cot_id,)).fetchone()
            if not reg and not PERFILES:
                return
            if not reg:
                raise ConflictoActualizacion("La cotización ya no existe.")
            if (PERFILES or etiqueta_version) and (not version_esperada or reg["actualizado"] != version_esperada):
                raise ConflictoActualizacion("Otra sesión modificó la cotización. Recarga antes de generar documentos.")
            carpeta = self._carpeta(reg["numero"])
            carpeta.mkdir(parents=True, exist_ok=True)
            base = re.sub(r"[^A-Za-z0-9._-]", "_", reg["numero"])
            campos = {}
            for campo, extension, contenido in (
                ("xlsx", ".xlsx", Path(xlsx).read_bytes() if xlsx else None),
                ("pdf", ".pdf", pdf),
            ):
                if contenido is None:
                    continue
                # Versionar evita sustituir un archivo aún referenciado por otra sesión.
                from uuid import uuid4
                sufijo = f"-{uuid4().hex[:12]}" if PERFILES or etiqueta_version else ""
                destino = carpeta / f"{base}{sufijo}{extension}"
                temporal = destino.with_suffix(extension + ".tmp")
                temporal.write_bytes(contenido)
                os.replace(temporal, destino)
                campos[campo] = str(destino)
            columnas = [f"{campo}=?" for campo in campos] + ["actualizado=?"]
            con.execute(f"UPDATE cotizaciones SET {', '.join(columnas)} WHERE id=?",
                        [*campos.values(), _ahora(), cot_id])
            if etiqueta_version:
                self._registrar_version(con, reg, etiqueta_version)

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
