
import json
import time
import requests
from pathlib import Path

BASE_URL = "https://recarga.fenieenergia.es"
ARCHIVO_SALIDA = Path("fenie_barrido.json")

INICIO = 1
FIN = 999
PAUSA = 1.0

resultados = []
codigos_vistos = set()

# Si ya existe un barrido anterior, lo recuperamos.
if ARCHIVO_SALIDA.exists():
    try:
        resultados = json.loads(
            ARCHIVO_SALIDA.read_text(encoding="utf-8")
        )
        codigos_vistos = {
            punto["codigo"] for punto in resultados
        }
        print(f"Recuperados {len(resultados)} resultados anteriores.")
    except (json.JSONDecodeError, KeyError, TypeError):
        print("El JSON anterior no es válido. Se empezará de nuevo.")
        resultados = []
        codigos_vistos = set()

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml"
})

print("Iniciando barrido de códigos Feníe...")

for numero in range(INICIO, FIN + 1):
    codigo = f"{numero:04d}"

    if codigo in codigos_vistos:
        continue

    url = f"{BASE_URL}/punto-recarga/{codigo}"

    try:
        respuesta = session.get(
            url,
            allow_redirects=False,
            timeout=20
        )

        if respuesta.status_code == 200 and codigo in respuesta.text:
            resultados.append({
                "codigo": codigo,
                "url": url,
                "http": 200,
                "html": respuesta.text
            })

            codigos_vistos.add(codigo)

            print(f"[ENCONTRADO] {codigo} - {len(resultados)} fichas")

            # Guardado inmediato para no perder resultados.
            ARCHIVO_SALIDA.write_text(
                json.dumps(resultados, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        elif respuesta.status_code == 302:
            print(f"[NO EXISTE] {codigo}")

        elif respuesta.status_code == 200:
            print(f"[REVISAR] {codigo} - HTTP 200 sin código en HTML")

        else:
            print(f"[HTTP {respuesta.status_code}] {codigo}")

    except requests.RequestException as error:
        print(f"[ERROR] {codigo}: {error}")

    time.sleep(PAUSA)

print("\nBarrido terminado.")
print(f"Fichas encontradas: {len(resultados)}")
print(f"Archivo: {ARCHIVO_SALIDA.resolve()}")
