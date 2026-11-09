"""Recuperación presencial por el propietario de Windows; no es una ruta web."""
import argparse
import getpass
import os
import re
from pathlib import Path
from usuarios import Usuarios, derivar

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("usuario", choices=["harim", "giovanna"])
    usuario = parser.parse_args().usuario
    archivo = Path(os.environ["LOCALAPPDATA"]) / "Gioval" / "Acceso" / "usuarios.db"
    if not archivo.is_file():
        raise SystemExit("No existe la base de perfiles de este PC.")
    clave = getpass.getpass("Nueva contraseña temporal (mínimo 6, letras y números): ")
    repetir = getpass.getpass("Repetir: ")
    if clave != repetir or not re.fullmatch(r"[A-Za-z0-9]{6,512}", clave) or not re.search("[A-Za-z]", clave) or not re.search("[0-9]", clave):
        raise SystemExit("Las contraseñas deben coincidir y contener letras y números.")
    db = Usuarios(archivo)
    with db.con() as c:
        c.execute("UPDATE usuarios SET clave=?,cambiar=1,activo=1 WHERE usuario=?", (derivar(clave), usuario))
        c.execute("DELETE FROM sesiones WHERE usuario=?", (usuario,))
        c.execute("DELETE FROM intentos WHERE usuario=?", (usuario,))
        db._auditar(c, "propietario-windows", "recuperacion_local", usuario)
    print("Contraseña temporal actualizada. Al ingresar debes elegir otra definitiva.")
