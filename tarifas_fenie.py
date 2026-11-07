
import os
import re
import json
import time
import requests
from html.parser import HTMLParser
from dotenv import load_dotenv

load_dotenv()

USUARIO = os.getenv("FENIE_USER")
PASSWORD = os.getenv("FENIE_PASSWORD")

BASE_URL = "https://recarga.fenieenergia.es"
ARCHIVO_SALIDA = "tarifas_fenie.json"


class TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "meta" and attrs.get("name") == "csrf-token":
            self.token = attrs.get("content")

        if tag == "input" and attrs.get("name") == "_token":
            self.token = attrs.get("value")


class PrecioParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.precios = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        clases = attrs.get("class", "").split()

        # Solo nos interesan los botones de RECARGA
        if "recargar" in clases and "data-prices" in attrs:
            try:
                precio = json.loads(attrs["data-prices"])
                self.precios.append(precio)
            except (ValueError, TypeError):
                pass


def iniciar_sesion(session):
    print("Iniciando sesión en Feníe...")

    respuesta = session.get(BASE_URL + "/", timeout=20)
    respuesta.raise_for_status()

    parser = TokenParser()
    parser.feed(respuesta.text)

    if not parser.token:
        raise Exception("No se encuentra el token CSRF")

    headers = {
        "X-CSRF-TOKEN": parser.token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL + "/",
        "Origin": BASE_URL
    }

    # Mantener estas cabeceras en las siguientes peticiones
    session.headers.update(headers)

    respuesta = session.post(
        BASE_URL + "/login",
        data={
            "user": USUARIO,
            "password": PASSWORD,
            "_token": parser.token
        },
        headers=headers,
        timeout=20
    )

    respuesta.raise_for_status()

    resultado = respuesta.json()

    if resultado.get("success") is not True:
        raise Exception("Feníe no ha confirmado el acceso")

    print("Sesión iniciada correctamente.")




def consultar_catalogo(session):
    print("Actualizando token CSRF...")

    # Obtener un token nuevo después del login
    respuesta = session.get(BASE_URL + "/", timeout=20)
    respuesta.raise_for_status()

    parser = TokenParser()
    parser.feed(respuesta.text)

    if not parser.token:
        raise Exception("No se encuentra el nuevo token CSRF")

    session.headers.update({
        "X-CSRF-TOKEN": parser.token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": BASE_URL + "/",
        "Origin": BASE_URL
    })

    print("Descargando catálogo de cargadores...")

    respuesta = session.post(
        BASE_URL + "/punto-recarga/todos",
        timeout=30
    )

    print("HTTP catálogo:", respuesta.status_code)

    respuesta.raise_for_status()

    puntos = respuesta.json()

    print(f"Se han encontrado {len(puntos)} cargadores.")

    return puntos




def consultar_tarifa(session, punto):
    codigo = punto.get("code")

    if not codigo:
        return None

    respuesta = session.get(
        f"{BASE_URL}/punto-recarga/{codigo}",
        timeout=20
    )

    respuesta.raise_for_status()

    parser = PrecioParser()
    parser.feed(respuesta.text)

    conectores = punto.get("conectores") or []

    # Expandir los conectores según las unidades disponibles
    detalles_conectores = []

    for conector in conectores:
        cantidad = int(conector.get("count") or 0)

        for _ in range(cantidad):
            detalles_conectores.append({
                "tipo": conector.get("type"),
                "potencia_kw": conector.get("power")
            })

    resultado = []

    for i, precio in enumerate(parser.precios):
        detalle = (
            detalles_conectores[i]
            if i < len(detalles_conectores)
            else {}
        )

        resultado.append({
            **detalle,
            "gratuito": precio.get("free"),
            "precio_kwh": (
                float(precio["energy_price"])
                if precio.get("energy_price") is not None
                else None
            )
        })

    return resultado


def main():
    if not USUARIO or not PASSWORD:
        raise Exception("Faltan las credenciales FENIE en el archivo .env")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0"
    })

    iniciar_sesion(session)

    puntos = consultar_catalogo(session)
    print("\nPrimeros 5 cargadores:")
    for punto in puntos[:5]:
        print(punto.get("nombre_punto"), "-", punto.get("code"))

    resultados = []

    for numero, punto in enumerate(puntos, 1):
        nombre = punto.get("nombre_punto", "Sin nombre")
        codigo = punto.get("code")

        print(
            f"[{numero}/{len(puntos)}] "
            f"{nombre} ({codigo})"
        )

        registro = {
            "id": punto.get("id"),
            "codigo": codigo,
            "nombre": nombre,
            "direccion": punto.get("direccion"),
            "conectores": []
        }

        try:
            tarifas = consultar_tarifa(session, punto)

            registro["conectores"] = tarifas or []

            print(f"   Tarifas encontradas: {len(registro['conectores'])}")

        except requests.RequestException as error:
            print(f"   Error HTTP: {error}")

        except Exception as error:
            print(f"   Error: {error}")

        resultados.append(registro)

        # Pequeña pausa para no saturar el servidor
        time.sleep(0.3)

    with open(ARCHIVO_SALIDA, "w", encoding="utf-8") as archivo:
        json.dump(
            resultados,
            archivo,
            ensure_ascii=False,
            indent=4
        )

    print("\nProceso terminado.")
    print(f"Cargadores procesados: {len(resultados)}")
    print(f"Archivo generado: {ARCHIVO_SALIDA}")


if __name__ == "__main__":
    main()
