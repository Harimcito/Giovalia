"""Prueba de navegación real con Chromium y datos temporales."""
import os
import subprocess
import sys
import time
from pathlib import Path
from io import BytesIO

from PIL import Image
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from cotizador import Biblioteca
from historial import Historial

from playwright.sync_api import sync_playwright, expect
def comprobar_interfaz(numero, numero_otro):
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('GIOVAL_TEST_CHROMIUM'))
        page=browser.new_page(viewport={'width':1440,'height':930})
        page.set_default_timeout(12000)
        errores=[]
        page.on('pageerror', lambda error: errores.append(str(error)))
        page.goto('http://127.0.0.1:8610/')
        expect(page.get_by_role('table',name='Cotizaciones recientes')).to_be_visible()
        expect(page.locator(".st-key-gv_nav").get_by_role("button", name="Precios")).to_be_visible()
        expect(page.locator(".st-key-gv_nav_movil")).to_be_hidden()
        page.wait_for_function("getComputedStyle(document.querySelector('[data-testid=stAppViewContainer]')).backgroundImage.includes('radial-gradient')")
        cabecera = page.locator(".st-key-cabecera_giovalia")
        acciones = cabecera.locator(".st-key-gv_cabecera_acciones")
        botones = ("Buscar", "Vista y lectura", "Ajustes", "Actualizar")
        posiciones = [acciones.get_by_role("button", name=etiqueta).bounding_box() for etiqueta in botones]
        assert max(p["y"] for p in posiciones) - min(p["y"] for p in posiciones) <= 2
        assert posiciones[0]["x"] < posiciones[-1]["x"]
        acciones.get_by_role("button", name="Vista y lectura").click()
        page.get_by_role("combobox", name="Tamaño del texto").click()
        page.get_by_role("option", name="Muy grande").click()
        page.wait_for_function("getComputedStyle(document.documentElement).fontSize === '20px'")
        page.locator(".st-key-ux_contraste").get_by_text("Alto contraste").click()
        page.wait_for_function("document.querySelector('#gv-preferencias').dataset.contraste === '1'")
        page.locator(".st-key-ux_mostrador").get_by_text("Modo mostrador").click()
        expect(page.locator(".st-key-gv_nav").get_by_text("Gestión comercial")).to_have_count(0)
        page.keyboard.press("Escape")
        page.keyboard.press('Control+k')
        consulta=page.get_by_role('textbox',name='Buscar en Giovalia')
        consulta.fill('TN1060')
        consulta.press('Enter')
        page.get_by_role('button',name=f"Abrir {numero}").wait_for()
        page.wait_for_function("CSS.highlights.has('gv-coincidencia')")
        consulta.press('ArrowDown')
        page.keyboard.press('ArrowDown')
        expect(page.get_by_role('button',name=f"Abrir {numero}")).to_be_focused()
        page.keyboard.press('Enter')
        tabla=page.get_by_role('table',name='Productos de la cotización')
        expect(tabla).to_be_visible()
        expect(page.locator(".gv-stepper [aria-current=step]")).to_contain_text("Editar productos")
        page.keyboard.press('Escape')
        tabla.get_by_role('button',name='Resma oficio').click()
        expect(tabla.get_by_role('button',name='Resma oficio')).to_have_attribute('aria-current','true')
        page.get_by_role('textbox',name='Precio de la línea seleccionada').fill('2380')
        tabla.get_by_role('button',name='Toner Brother TN1060').click()
        expect(page.get_by_role('dialog')).to_be_visible()
        page.get_by_role('button',name='Guardar y continuar').click()
        expect(tabla.get_by_role('button',name='Toner Brother TN1060')).to_have_attribute('aria-current','true')
        barra = page.locator(".st-key-barra_cotizacion")
        expect(barra.get_by_role("button", name="Guardar borrador")).to_be_visible()
        assert barra.evaluate("e => getComputedStyle(e).position") == "sticky"
        page.get_by_role("button", name="Ampliar foto").click()
        expect(page.get_by_role("dialog", name="Vista ampliada del anexo")).to_be_visible()
        page.keyboard.press("Escape")
        page.get_by_role('button',name='Ir al campo').first.click()
        expect(page.get_by_role('textbox',name='Nombre o razón social')).to_be_focused()
        page.set_viewport_size({'width':390,'height':850})
        page.wait_for_function("getComputedStyle(document.querySelector('.gv-lista tbody tr')).display === 'grid'")
        assert tabla.bounding_box()['width']<=390
        expect(page.locator(".st-key-gv_nav_movil")).to_be_visible()
        ancho_cabecera = cabecera.bounding_box()
        for etiqueta in botones:
            posicion = acciones.get_by_role("button", name=etiqueta).bounding_box()
            assert ancho_cabecera["x"] <= posicion["x"]
            assert posicion["x"] + posicion["width"] <= ancho_cabecera["x"] + ancho_cabecera["width"] + 1
        page.set_viewport_size({'width':1440,'height':930})
        page.locator('.st-key-gv_nav').get_by_role('button', name='Historial', exact=True).click()
        historial=page.get_by_role('table',name='Cotizaciones del historial')
        expect(historial).to_be_visible()
        historial.get_by_role('button',name=numero).click()
        expect(historial.get_by_role('button',name=numero).and_(page.locator('[aria-current="true"]'))).to_be_visible()
        page.get_by_role('textbox',name='Buscar',exact=True).fill(numero)
        page.get_by_role('textbox',name='Buscar',exact=True).press('Enter')
        expect(historial.get_by_role('button',name=numero_otro)).to_have_count(0)
        page.get_by_role('button',name='Seguir editando esta cotización').click()
        page.get_by_role('button',name='Volver al historial').click()
        expect(page.get_by_role('textbox',name='Buscar',exact=True)).to_have_value(numero)
        page.locator('.st-key-gv_nav').get_by_role('button', name='Biblioteca', exact=True).click()
        page.get_by_role('button',name='Sin foto · 1').click()
        expect(page.get_by_role('combobox', name='Filtrar por estado')).to_have_value('Sin foto')
        page.get_by_text('Galería',exact=True).click()
        page.locator('.st-key-biblioteca_galeria_RESMA').get_by_role('button', name='Ver producto').click()
        expect(page.get_by_text('Detalle del producto',exact=True)).to_be_visible()
        page.get_by_role('button',name='Limpiar búsqueda y filtros').click()
        assert not errores, errores
        print('Chrome: actividad, teclado, resaltado, filas, guardado, foco de alertas, móvil, historial y galería OK')


def ejecutar():
    with TemporaryDirectory() as temp:
        carpeta = Path(temp) / "biblioteca"
        bib = Biblioteca(carpeta)
        bib.registrar("TN1060", "Toner Brother")
        bib.registrar("RESMA", "Resma oficio")
        foto = BytesIO()
        Image.new("RGB", (24, 24), "#20467d").save(foto, "PNG")
        bib.guardar_archivo("TN1060", "foto", "toner.png", foto.getvalue())
        hist = Historial(carpeta)
        primero = [
            dict(cantidad=1, descripcion="Toner Brother TN1060", clave_auto="TN1060", unitario=1000),
            dict(cantidad=2, descripcion="Resma oficio", clave_auto="RESMA", unitario=1000),
        ]
        segundo = [dict(cantidad=1, descripcion="Cartucho nuevo", clave_auto="NUEVO", unitario=2000)]
        numeros = []
        for lineas in (primero, segundo):
            identificador, numero = hist.crear({}, lineas, {})
            hist.actualizar(identificador, datos=dict(lineas=lineas, paso=2))
            numeros.append(numero)
        entorno = dict(os.environ, GIOVAL_DATA_DIR=temp, GIOVAL_REQUIRE_ACCESS="false", GIOVAL_PROFILES="false")
        comando = [
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.address", "127.0.0.1", "--server.port", "8610",
            "--server.headless", "true", "--global.developmentMode", "false",
            "--browser.gatherUsageStats", "false",
        ]
        with subprocess.Popen(comando, env=entorno) as servidor:
            try:
                for _ in range(60):
                    try:
                        with urlopen("http://127.0.0.1:8610/_stcore/health", timeout=1) as respuesta:
                            if respuesta.status == 200:
                                break
                    except (OSError, URLError):
                        if servidor.poll() is not None:
                            raise RuntimeError("Streamlit se cerró al iniciar.")
                        time.sleep(0.5)
                else:
                    raise RuntimeError("Streamlit no respondió a tiempo.")
                comprobar_interfaz(*numeros)
                assert hist.obtener(1)["datos"]["lineas"][1]["unitario"] == 2000
            finally:
                servidor.terminate()
                servidor.wait(timeout=15)


if __name__ == "__main__":
    ejecutar()
