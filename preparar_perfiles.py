"""Aprovisionamiento local; nunca imprime contraseñas en registros o consola."""
import argparse
import secrets
from pathlib import Path
from usuarios import Usuarios


def preparar(base):
    base = Path(base)
    db = Usuarios(base / "Acceso" / "usuarios.db")
    existentes = {r["usuario"] for r in db.listar()}
    nuevos = []
    for usuario, nombre, rol in [("harim", "Harim", "admin"), ("giovanna", "Giovanna", "trabajador")]:
        if usuario not in existentes:
            clave = secrets.token_urlsafe(18)
            db.crear(usuario, nombre, rol, clave)
            nuevos.append(f"Usuario: {usuario}\nContraseña temporal: {clave}\n")
    if nuevos:
        archivo = base / "Acceso" / "Accesos-iniciales.txt"
        # Evitar sobrescribir contraseñas temporales de una instalación parcial.
        with archivo.open("a", encoding="utf-8") as salida:
            salida.write("\nGiovalIA · Acceso privado del equipo\n"
                         "Al ingresar, cambia la contraseña temporal. No subas este archivo a GitHub.\n\n")
            salida.write("\n".join(nuevos))
        print(f"Perfiles preparados. Accesos iniciales disponibles solo en: {archivo}")
    else:
        print("Los perfiles ya existen. No se modificaron sus contraseñas.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    preparar(parser.parse_args().base)
