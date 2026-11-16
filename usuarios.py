"""Identidades locales: contraseñas derivadas, sesiones revocables y bloqueo de intentos.

La base de acceso vive fuera de biblioteca y nunca se incluye en sus descargas.
No hay cuentas ni contraseñas por defecto. El aprovisionamiento es una acción local.
"""
from __future__ import annotations

import hashlib
import json
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ITERACIONES = 600_000
DURACION_SESION = 8 * 3600


def derivar(clave: str) -> str:
    if not 6 <= len(clave) <= 512:
        raise ValueError("Usa entre 6 y 512 caracteres para la contraseña.")
    sal = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), ITERACIONES)
    return f"pbkdf2_sha256${ITERACIONES}${sal}${digest.hex()}"


def verificar(clave: str, derivada: str) -> bool:
    if len(clave) > 512:
        return False
    algoritmo, vueltas, sal, digest = derivada.split("$")
    if algoritmo != "pbkdf2_sha256":
        return False
    candidato = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), int(vueltas))
    return hmac.compare_digest(candidato.hex(), digest)


class Usuarios:
    def __init__(self, archivo: str | Path):
        self.archivo = Path(archivo)
        self.archivo.parent.mkdir(parents=True, exist_ok=True)
        with self.con() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    usuario TEXT PRIMARY KEY, nombre TEXT NOT NULL, rol TEXT NOT NULL,
                    clave TEXT NOT NULL, activo INTEGER NOT NULL DEFAULT 1,
                    cambiar INTEGER NOT NULL DEFAULT 1, correo TEXT NOT NULL DEFAULT '',
                    correo_verificado INTEGER NOT NULL DEFAULT 0,
                    registro_pendiente INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS preferencias_ux (
                    usuario TEXT PRIMARY KEY, datos TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sesiones (
                    token TEXT PRIMARY KEY, usuario TEXT NOT NULL,
                    vence REAL NOT NULL, ultimo REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS intentos (
                    usuario TEXT PRIMARY KEY, fallos INTEGER NOT NULL, desde REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS auditoria (
                    id INTEGER PRIMARY KEY, fecha REAL NOT NULL, usuario TEXT NOT NULL,
                    evento TEXT NOT NULL, objetivo TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS recuperaciones (
                    token_hash TEXT PRIMARY KEY, usuario TEXT NOT NULL,
                    tipo TEXT NOT NULL DEFAULT 'restablecer', vence REAL NOT NULL, creado REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS limites_correo (
                    tipo TEXT NOT NULL, usuario TEXT NOT NULL, creado REAL NOT NULL,
                    PRIMARY KEY(tipo, usuario));
            """)
            columnas = {r[1] for r in c.execute("PRAGMA table_info(usuarios)")}
            if "correo" not in columnas:
                c.execute("ALTER TABLE usuarios ADD COLUMN correo TEXT NOT NULL DEFAULT ''")
            if "correo_verificado" not in columnas:
                c.execute("ALTER TABLE usuarios ADD COLUMN correo_verificado INTEGER NOT NULL DEFAULT 0")
            if "registro_pendiente" not in columnas:
                c.execute("ALTER TABLE usuarios ADD COLUMN registro_pendiente INTEGER NOT NULL DEFAULT 0")
            columnas_codigos = {r[1] for r in c.execute("PRAGMA table_info(recuperaciones)")}
            if "tipo" not in columnas_codigos:
                c.execute("ALTER TABLE recuperaciones ADD COLUMN tipo TEXT NOT NULL DEFAULT 'restablecer'")

    @contextmanager
    def con(self):
        c = sqlite3.connect(self.archivo, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def leer_preferencias_ux(self, usuario):
        with self.con() as c:
            fila = c.execute("SELECT datos FROM preferencias_ux WHERE usuario=?", (usuario,)).fetchone()
        return json.loads(fila["datos"]) if fila else None

    def guardar_preferencias_ux(self, usuario, datos):
        with self.con() as c:
            c.execute("INSERT INTO preferencias_ux(usuario,datos) VALUES (?,?) "
                      "ON CONFLICT(usuario) DO UPDATE SET datos=excluded.datos", (usuario, json.dumps(datos)))

    @staticmethod
    def _auditar(c, usuario, evento, objetivo=""):
        c.execute("INSERT INTO auditoria(fecha,usuario,evento,objetivo) VALUES(?,?,?,?)",
                  (time.time(), usuario, evento, objetivo))

    def crear(self, usuario: str, nombre: str, rol: str, clave: str):
        usuario = usuario.strip().lower()
        if usuario not in {"harim", "giovanna"} or rol not in {"admin", "trabajador"}:
            raise ValueError("Perfil no permitido.")
        derivada = derivar(clave)
        with self.con() as c:
            c.execute("INSERT INTO usuarios(usuario,nombre,rol,clave) VALUES(?,?,?,?)",
                      (usuario, nombre, rol, derivada))
            self._auditar(c, "instalador-local", "crear", usuario)

    def listar(self):
        with self.con() as c:
            return [dict(r) for r in c.execute(
                "SELECT usuario,nombre,rol,activo,cambiar,correo,correo_verificado,registro_pendiente "
                "FROM usuarios ORDER BY usuario")]

    @staticmethod
    def _correo_normalizado(correo: str) -> str:
        correo = (correo or "").strip().lower()
        if correo and (len(correo) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", correo)):
            raise ValueError("Escribe una dirección de correo válida.")
        return correo

    def registrar_cuenta(self, nombre: str, correo: str, clave: str):
        """Crea una cuenta de trabajador inactiva hasta confirmar el correo."""
        nombre = " ".join(str(nombre or "").split())
        correo = self._correo_normalizado(correo)
        if not 2 <= len(nombre) <= 100:
            raise ValueError("Escribe tu nombre (entre 2 y 100 caracteres).")
        if not correo:
            raise ValueError("Escribe un correo válido.")
        if (not re.fullmatch(r"[A-Za-z0-9]{12,512}", clave or "")
                or not re.search("[A-Za-z]", clave) or not re.search("[0-9]", clave)):
            raise ValueError("La contraseña debe tener al menos 12 caracteres, con letras y números.")
        ahora = time.time()
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            existe = c.execute("SELECT 1 FROM usuarios WHERE lower(usuario)=? OR lower(correo)=?",
                               (correo, correo)).fetchone()
            if existe:
                return None
            ultimo = c.execute("SELECT creado FROM limites_correo WHERE tipo='alta' AND usuario=?", (correo,)).fetchone()
            if ultimo and ultimo["creado"] > ahora - 60:
                return None
            derivada = derivar(clave)
            c.execute("INSERT INTO usuarios(usuario,nombre,rol,clave,activo,cambiar,correo,correo_verificado,registro_pendiente) "
                      "VALUES(?,?, 'trabajador', ?,1,0,?,0,1)", (correo, nombre, derivada, correo))
            codigo = secrets.token_urlsafe(32)
            huella = hashlib.sha256(codigo.encode()).hexdigest()
            c.execute("INSERT INTO recuperaciones(token_hash,usuario,tipo,vence,creado) VALUES(?,?,?,?,?)",
                      (huella, correo, "alta", ahora + 1800, ahora))
            c.execute("INSERT INTO limites_correo VALUES('alta',?,?) ON CONFLICT(tipo,usuario) DO UPDATE SET creado=excluded.creado",
                      (correo, ahora))
            self._auditar(c, correo, "registro_pendiente", correo)
            return correo, codigo

    def cancelar_registro(self, correo: str):
        correo = self._correo_normalizado(correo)
        with self.con() as c:
            fila = c.execute("SELECT usuario FROM usuarios WHERE lower(correo)=? AND registro_pendiente=1",
                             (correo,)).fetchone()
            if not fila:
                return
            usuario = fila["usuario"]
            c.execute("DELETE FROM recuperaciones WHERE usuario=? AND tipo='alta'", (usuario,))
            c.execute("DELETE FROM usuarios WHERE usuario=? AND registro_pendiente=1", (usuario,))
            self._auditar(c, "registro", "alta_cancelada", usuario)

    def reenviar_codigo_alta(self, correo: str):
        correo = self._correo_normalizado(correo)
        ahora = time.time()
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = c.execute("SELECT usuario,correo FROM usuarios WHERE lower(correo)=? "
                             "AND registro_pendiente=1 AND activo=1", (correo,)).fetchone()
            if not fila:
                return None
            ultimo = c.execute("SELECT creado FROM limites_correo WHERE tipo='alta' AND usuario=?",
                               (fila["usuario"],)).fetchone()
            if ultimo and ultimo["creado"] > ahora - 60:
                return None
            codigo = secrets.token_urlsafe(32)
            huella = hashlib.sha256(codigo.encode()).hexdigest()
            c.execute("DELETE FROM recuperaciones WHERE usuario=?", (fila["usuario"],))
            c.execute("INSERT INTO recuperaciones(token_hash,usuario,tipo,vence,creado) VALUES(?,?,?,?,?)",
                      (huella, fila["usuario"], "alta", ahora + 1800, ahora))
            c.execute("INSERT INTO limites_correo VALUES('alta',?,?) ON CONFLICT(tipo,usuario) DO UPDATE SET creado=excluded.creado",
                      (fila["usuario"], ahora))
            self._auditar(c, "registro", "reenviar_codigo_alta", fila["usuario"])
            return fila["correo"], codigo

    def asociar_correo(self, token: str, objetivo: str, correo: str):
        identidad = self.identidad(token)
        if not identidad or identidad["rol"] != "admin" or identidad["cambiar"]:
            raise PermissionError("Solo Harim puede administrar los correos de las cuentas.")
        correo = self._correo_normalizado(correo)
        if not correo:
            raise ValueError("Cada cuenta necesita un correo asociado.")
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM usuarios WHERE usuario=?", (objetivo,)).fetchone():
                raise ValueError("El perfil no existe.")
            duplicado = c.execute("SELECT usuario FROM usuarios WHERE lower(correo)=? AND usuario<>?", (correo, objetivo)).fetchone()
            if duplicado:
                raise ValueError("Ese correo ya está asociado a otra cuenta.")
            actual = c.execute("SELECT correo,correo_verificado FROM usuarios WHERE usuario=?", (objetivo,)).fetchone()
            verificado = actual["correo_verificado"] if actual["correo"] == correo else 0
            c.execute("UPDATE usuarios SET correo=?,correo_verificado=? WHERE usuario=?", (correo, verificado, objetivo))
            c.execute("DELETE FROM recuperaciones WHERE usuario=?", (objetivo,))
            self._auditar(c, identidad["usuario"], "asociar_correo", objetivo)

    def emitir_verificacion_correo(self, token: str, objetivo: str):
        identidad = self.identidad(token)
        if not identidad or identidad["rol"] != "admin" or identidad["cambiar"]:
            raise PermissionError("Solo Harim puede verificar correos de las cuentas.")
        ahora = time.time()
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = c.execute("SELECT correo,correo_verificado FROM usuarios WHERE usuario=? AND activo=1", (objetivo,)).fetchone()
            if not fila or not fila["correo"]:
                raise ValueError("Primero asocia un correo a la cuenta.")
            if fila["correo_verificado"]:
                raise ValueError("Ese correo ya está verificado.")
            ultimo = c.execute("SELECT creado FROM limites_correo WHERE tipo='verificar' AND usuario=?", (objetivo,)).fetchone()
            if ultimo and ultimo["creado"] > ahora - 60:
                raise ValueError("Espera un minuto antes de solicitar otro código.")
            codigo = secrets.token_urlsafe(32)
            huella = hashlib.sha256(codigo.encode()).hexdigest()
            c.execute("DELETE FROM recuperaciones WHERE usuario=?", (objetivo,))
            c.execute("INSERT INTO recuperaciones(token_hash,usuario,tipo,vence,creado) VALUES(?,?,?,?,?)",
                      (huella, objetivo, "verificar", ahora + 1800, ahora))
            c.execute("INSERT INTO limites_correo VALUES('verificar',?,?) ON CONFLICT(tipo,usuario) DO UPDATE SET creado=excluded.creado",
                      (objetivo, ahora))
            self._auditar(c, identidad["usuario"], "enviar_verificacion_correo", objetivo)
            return fila["correo"], codigo

    def verificar_correo(self, codigo: str):
        huella = hashlib.sha256((codigo or "").strip().encode()).hexdigest()
        ahora = time.time()
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = c.execute("SELECT usuario,tipo FROM recuperaciones WHERE token_hash=? AND tipo IN ('verificar','alta') AND vence>?",
                             (huella, ahora)).fetchone()
            if not fila:
                raise ValueError("El código no es válido o venció. Pide uno nuevo al administrador.")
            usuario = fila["usuario"]
            if fila["tipo"] == "alta":
                actualizado = c.execute("UPDATE usuarios SET correo_verificado=1,registro_pendiente=0 "
                                         "WHERE usuario=? AND correo<>'' AND registro_pendiente=1", (usuario,))
            else:
                actualizado = c.execute("UPDATE usuarios SET correo_verificado=1,registro_pendiente=0 "
                                         "WHERE usuario=? AND correo<>'' AND activo=1", (usuario,))
            if actualizado.rowcount == 0:
                raise ValueError("No se pudo verificar el correo. Contacta al administrador.")
            c.execute("DELETE FROM recuperaciones WHERE usuario=?", (usuario,))
            self._auditar(c, "verificacion-correo", "verificar_correo", usuario)

    def solicitar_restauracion(self, correo: str):
        correo = self._correo_normalizado(correo)
        ahora = time.time()
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = c.execute("SELECT usuario,correo FROM usuarios WHERE lower(correo)=? AND activo=1 AND correo_verificado=1", (correo,)).fetchone()
            if not fila:
                return None
            ultimo = c.execute("SELECT creado FROM limites_correo WHERE tipo='restablecer' AND usuario=?", (fila["usuario"],)).fetchone()
            if ultimo and ultimo["creado"] > ahora - 60:
                return None
            codigo = secrets.token_urlsafe(32)
            huella = hashlib.sha256(codigo.encode()).hexdigest()
            c.execute("DELETE FROM recuperaciones WHERE usuario=?", (fila["usuario"],))
            c.execute("INSERT INTO recuperaciones(token_hash,usuario,tipo,vence,creado) VALUES(?,?,?,?,?)",
                      (huella, fila["usuario"], "restablecer", ahora + 1800, ahora))
            c.execute("INSERT INTO limites_correo VALUES('restablecer',?,?) ON CONFLICT(tipo,usuario) DO UPDATE SET creado=excluded.creado",
                      (fila["usuario"], ahora))
            self._auditar(c, "recuperacion-correo", "solicitar_restauracion", fila["usuario"])
            return fila["correo"], codigo

    def cancelar_codigo(self, codigo: str):
        huella = hashlib.sha256((codigo or "").encode()).hexdigest()
        with self.con() as c:
            c.execute("DELETE FROM recuperaciones WHERE token_hash=?", (huella,))

    def restaurar_con_codigo(self, codigo: str, nueva: str):
        if not re.fullmatch(r"[A-Za-z0-9]{12,512}", nueva or "") or not re.search("[A-Za-z]", nueva) or not re.search("[0-9]", nueva):
            raise ValueError("Usa al menos 12 caracteres, con letras y números, sin espacios ni símbolos.")
        huella = hashlib.sha256((codigo or "").strip().encode()).hexdigest()
        ahora = time.time()
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            fila = c.execute("SELECT usuario FROM recuperaciones WHERE token_hash=? AND tipo='restablecer' AND vence>?",
                             (huella, ahora)).fetchone()
            if not fila:
                raise ValueError("El código no es válido o venció. Solicita uno nuevo.")
            usuario = fila["usuario"]
            derivada = derivar(nueva)
            c.execute("UPDATE usuarios SET clave=?,cambiar=0 WHERE usuario=? AND activo=1", (derivada, usuario))
            if c.total_changes == 0:
                raise ValueError("No se pudo completar la recuperación. Solicita un código nuevo.")
            c.execute("DELETE FROM recuperaciones WHERE usuario=?", (usuario,))
            c.execute("DELETE FROM sesiones WHERE usuario=?", (usuario,))
            c.execute("DELETE FROM intentos WHERE usuario=?", (usuario,))
            self._auditar(c, "recuperacion-correo", "restaurar_clave", usuario)

    def entrar(self, usuario: str, clave: str) -> str | None:
        usuario = usuario.strip().lower()[:254]
        ahora = time.time()
        with self.con() as c:
            # Serializa el contador de fallos entre sesiones y procesos.
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM intentos WHERE desde < ?", (ahora - 900,))
            fila = c.execute("SELECT * FROM usuarios WHERE lower(usuario)=? OR lower(correo)=? LIMIT 1",
                             (usuario, usuario)).fetchone()
            clave_intentos = fila["usuario"] if fila else usuario
            intentos = c.execute("SELECT * FROM intentos WHERE usuario=?", (clave_intentos,)).fetchone()
            if intentos and intentos["fallos"] >= 5:
                return None
            if (not fila or not verificar(clave, fila["clave"]) or not fila["activo"]
                    or fila["registro_pendiente"]):
                c.execute("INSERT INTO intentos VALUES(?,1,?) ON CONFLICT(usuario) "
                          "DO UPDATE SET fallos=fallos+1", (clave_intentos, ahora))
                self._auditar(c, clave_intentos, "acceso_fallido")
                return None
            c.execute("DELETE FROM intentos WHERE usuario=?", (clave_intentos,))
            c.execute("DELETE FROM sesiones WHERE vence<? OR ultimo<?", (ahora, ahora - 3600))
            token = secrets.token_urlsafe(32)
            c.execute("INSERT INTO sesiones VALUES(?,?,?,?)",
                      (hashlib.sha256(token.encode()).hexdigest(), clave_intentos, ahora + DURACION_SESION, ahora))
            self._auditar(c, clave_intentos, "entrar")
            return token

    def identidad(self, token: str | None):
        if not token:
            return None
        ahora = time.time()
        with self.con() as c:
            huella = hashlib.sha256(token.encode()).hexdigest()
            fila = c.execute("SELECT u.usuario,u.nombre,u.rol,u.cambiar FROM sesiones s "
                             "JOIN usuarios u ON u.usuario=s.usuario WHERE s.token=? "
                             "AND s.vence>? AND s.ultimo>? AND u.activo=1",
                             (huella, ahora, ahora - 3600)).fetchone()
            if fila:
                c.execute("UPDATE sesiones SET ultimo=? WHERE token=?", (ahora, huella))
                return dict(fila)
        return None

    def salir(self, token: str | None):
        if token:
            with self.con() as c:
                c.execute("DELETE FROM sesiones WHERE token=?",
                          (hashlib.sha256(token.encode()).hexdigest(),))

    def cambiar_clave(self, token: str, actual: str, nueva: str):
        identidad = self.identidad(token)
        if not identidad:
            raise PermissionError("Inicia sesión de nuevo.")
        if not re.fullmatch(r"[A-Za-z0-9]{6,512}", nueva) or not re.search(r"[A-Za-z]", nueva) or not re.search(r"[0-9]", nueva):
            raise ValueError("Usa al menos 6 caracteres, con letras y números, sin espacios ni símbolos.")
        derivada = derivar(nueva)
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            usuario = identidad["usuario"]
            fila = c.execute("SELECT clave FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
            if not verificar(actual, fila["clave"]):
                raise ValueError("La contraseña actual no coincide.")
            if verificar(nueva, fila["clave"]):
                raise ValueError("Elige una contraseña diferente de la actual.")
            c.execute("UPDATE usuarios SET clave=?,cambiar=0 WHERE usuario=?", (derivada, usuario))
            c.execute("DELETE FROM sesiones WHERE usuario=?", (usuario,))
            self._auditar(c, usuario, "cambiar_clave")

    def administrar(self, token: str, objetivo: str, *, clave: str | None = None, activo=None, correo: str | None = None):
        identidad = self.identidad(token)
        if not identidad or identidad["rol"] != "admin" or identidad["cambiar"]:
            raise PermissionError("Solo Harim puede administrar las cuentas.")
        if objetivo == identidad["usuario"] and clave:
            raise ValueError("Usa Cambiar mi contraseña para tu propia cuenta.")
        if clave and (not re.fullmatch(r"[A-Za-z0-9]{6,512}", clave) or not re.search(r"[A-Za-z]", clave) or not re.search(r"[0-9]", clave)):
            raise ValueError("La contraseña temporal debe tener al menos 6 caracteres, letras y números.")
        derivada = derivar(clave) if clave else None
        correo_normalizado = self._correo_normalizado(correo) if correo is not None else None
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM usuarios WHERE usuario=?", (objetivo,)).fetchone():
                raise ValueError("El perfil no existe.")
            if derivada:
                c.execute("UPDATE usuarios SET clave=?,cambiar=1 WHERE usuario=?", (derivada, objetivo))
                c.execute("DELETE FROM intentos WHERE usuario=?", (objetivo,))
            if activo is not None:
                c.execute("UPDATE usuarios SET activo=? WHERE usuario=?", (int(bool(activo)), objetivo))
            if correo_normalizado is not None:
                if correo_normalizado:
                    duplicado = c.execute("SELECT usuario FROM usuarios WHERE lower(correo)=? AND usuario<>?", (correo_normalizado, objetivo)).fetchone()
                    if duplicado:
                        raise ValueError("Ese correo ya está asociado a otra cuenta.")
                actual = c.execute("SELECT correo FROM usuarios WHERE usuario=?", (objetivo,)).fetchone()
                if not actual:
                    raise ValueError("El perfil no existe.")
                verificado = c.execute("SELECT correo_verificado FROM usuarios WHERE usuario=?", (objetivo,)).fetchone()[0]
                if correo_normalizado != actual["correo"]:
                    verificado = 0
                if not correo_normalizado:
                    raise ValueError("Cada cuenta necesita un correo asociado.")
                c.execute("UPDATE usuarios SET correo=?,correo_verificado=? WHERE usuario=?",
                          (correo_normalizado, verificado, objetivo))
                c.execute("DELETE FROM recuperaciones WHERE usuario=?", (objetivo,))
            c.execute("DELETE FROM sesiones WHERE usuario=?", (objetivo,))
            self._auditar(c, identidad["usuario"], "administrar", objetivo)
