
import requests
from tarifas_fenie import iniciar_sesion, BASE_URL

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

iniciar_sesion(session)

# Renovar token CSRF
r = session.get(BASE_URL + "/")
from tarifas_fenie import TokenParser

parser = TokenParser()
parser.feed(r.text)

session.headers.update({
    "X-CSRF-TOKEN": parser.token,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE_URL + "/",
    "Origin": BASE_URL
})

# Consultar ficha de Agullent
r = session.get(BASE_URL + "/punto-recarga/0344")

print("HTTP:", r.status_code)
print("URL final:", r.url)
print("Tamaño HTML:", len(r.text))
print("Botones recargar:", r.text.count("recargar"))
print("Atributos data-prices:", r.text.count("data-prices"))
print("Texto energy_price:", r.text.count("energy_price"))
print("Texto recharge_conditions:", r.text.count("recharge_conditions"))

with open("adzeneta.html", "w", encoding="utf-8") as f:
    f.write(r.text)

print("HTML guardado en adzeneta.html")

