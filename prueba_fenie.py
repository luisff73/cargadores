
import os
import re
import json
import requests
from html.parser import HTMLParser
from dotenv import load_dotenv

# Cargar credenciales del archivo .env
load_dotenv()

USUARIO = os.getenv("FENIE_USER")
PASSWORD = os.getenv("FENIE_PASSWORD")

BASE_URL = "https://recarga.fenieenergia.es"


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


class PriceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.prices = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if "data-prices" in attrs:
            self.prices.append(attrs["data-prices"])


# Comprobar credenciales
if not USUARIO or not PASSWORD:
    raise SystemExit("ERROR: No se encuentran las credenciales FENIE en .env")

session = requests.Session()

headers = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest"
}

print("1. Accediendo a la página de login...")

respuesta = session.get(
    f"{BASE_URL}/",
    headers=headers,
    timeout=20
)

print("   HTTP:", respuesta.status_code)

# Extraer token CSRF
parser = TokenParser()
parser.feed(respuesta.text)

token = parser.token

if not token:
    raise SystemExit("ERROR: No se ha encontrado el token CSRF")

print("2. Enviando credenciales...")

headers.update({
    "X-CSRF-TOKEN": token,
    "Referer": f"{BASE_URL}/login",
    "Origin": BASE_URL,
    "Content-Type": "application/x-www-form-urlencoded"
})

respuesta = session.post(
    f"{BASE_URL}/login",
    data={
        "user": USUARIO,
        "password": PASSWORD,
        "_token": token
    },
    headers=headers,
    timeout=20
)

try:
    resultado = respuesta.json()
except ValueError:
    raise SystemExit("ERROR: La respuesta del login no es JSON")

print("   Respuesta:", resultado)

if resultado.get("success") is not True:
    raise SystemExit("ERROR: Feníe no ha confirmado el acceso")

print("3. Acceso correcto. Consultando El Palomar...")

respuesta = session.get(
    f"{BASE_URL}/punto-recarga/0487",
    headers={"User-Agent": "Mozilla/5.0"},
    timeout=20
)

print("   HTTP ficha:", respuesta.status_code)

if respuesta.status_code != 200:
    raise SystemExit("ERROR al consultar la ficha")

# Extraer precios de los conectores
parser = PriceParser()
parser.feed(respuesta.text)

print("4. Tarifas encontradas:")

for i, dato in enumerate(parser.prices, 1):
    try:
        precio = json.loads(dato)
        print(f"   Conector {i}: {precio}")
    except ValueError:
        print(f"   Conector {i}: No se pudo interpretar el precio")

print("Prueba finalizada.")
