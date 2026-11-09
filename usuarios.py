"""Identidades locales: contraseñas derivadas, sesiones revocables y bloqueo de intentos.

La base de acceso vive fuera de biblioteca y nunca se incluye en sus descargas.
No hay cuentas ni contraseñas por defecto. El aprovisionamiento es una acción local.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ITERACIONES = 600_000


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
                    cambiar INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS sesiones (
                    token TEXT PRIMARY KEY, usuario TEXT NOT NULL,
                    vence REAL NOT NULL, ultimo REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS intentos (
                    usuario TEXT PRIMARY KEY, fallos INTEGER NOT NULL, desde REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS auditoria (
                    id INTEGER PRIMARY KEY, fecha REAL NOT NULL, usuario TEXT NOT NULL,
                    evento TEXT NOT NULL, objetivo TEXT NOT NULL DEFAULT '');
            """)

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
                "SELECT usuario,nombre,rol,activo,cambiar FROM usuarios ORDER BY usuario")]

    def entrar(self, usuario: str, clave: str) -> str | None:
        usuario = usuario.strip().lower()[:100]
        ahora = time.time()
        with self.con() as c:
            # Serializa el contador de fallos entre sesiones y procesos.
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM intentos WHERE desde < ?", (ahora - 900,))
            intentos = c.execute("SELECT * FROM intentos WHERE usuario=?", (usuario,)).fetchone()
            if intentos and intentos["fallos"] >= 5:
                return None
            fila = c.execute("SELECT * FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
            if not fila or not verificar(clave, fila["clave"]) or not fila["activo"]:
                c.execute("INSERT INTO intentos VALUES(?,1,?) ON CONFLICT(usuario) "
                          "DO UPDATE SET fallos=fallos+1", (usuario, ahora))
                self._auditar(c, usuario, "acceso_fallido")
                return None
            c.execute("DELETE FROM intentos WHERE usuario=?", (usuario,))
            c.execute("DELETE FROM sesiones WHERE vence<? OR ultimo<?", (ahora, ahora - 3600))
            token = secrets.token_urlsafe(32)
            c.execute("INSERT INTO sesiones VALUES(?,?,?,?)",
                      (hashlib.sha256(token.encode()).hexdigest(), usuario, ahora + 8 * 3600, ahora))
            self._auditar(c, usuario, "entrar")
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

    def administrar(self, token: str, objetivo: str, *, clave: str | None = None, activo=None):
        identidad = self.identidad(token)
        if not identidad or identidad["rol"] != "admin" or identidad["cambiar"]:
            raise PermissionError("Solo Harim puede administrar las cuentas.")
        if objetivo == identidad["usuario"]:
            raise ValueError("Usa Cambiar mi contraseña para tu propia cuenta.")
        if clave and (not re.fullmatch(r"[A-Za-z0-9]{6,512}", clave) or not re.search(r"[A-Za-z]", clave) or not re.search(r"[0-9]", clave)):
            raise ValueError("La contraseña temporal debe tener al menos 6 caracteres, letras y números.")
        derivada = derivar(clave) if clave else None
        with self.con() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM usuarios WHERE usuario=?", (objetivo,)).fetchone():
                raise ValueError("El perfil no existe.")
            if derivada:
                c.execute("UPDATE usuarios SET clave=?,cambiar=1 WHERE usuario=?", (derivada, objetivo))
                c.execute("DELETE FROM intentos WHERE usuario=?", (objetivo,))
            if activo is not None:
                c.execute("UPDATE usuarios SET activo=? WHERE usuario=?", (int(bool(activo)), objetivo))
            c.execute("DELETE FROM sesiones WHERE usuario=?", (objetivo,))
            self._auditar(c, identidad["usuario"], "administrar", objetivo)
