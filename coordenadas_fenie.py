"""Añade latitud y longitud al catálogo de Feníe.

Las coordenadas salen del mapa de cada ficha guardada en fenie_barrido.json.
Se puede ejecutar de nuevo tras actualizar el barrido o el catálogo.
"""

import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
BARRIDO = BASE / "fenie_barrido.json"
CATALOGO = BASE / "fenie_catalogo_completo.json"

PATRON_COORDENADAS = re.compile(
    r"latLng:\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]"
)


def en_espana(latitud, longitud):
    """Descarta coordenadas erróneas publicadas en algunas fichas."""
    return 27 <= latitud <= 44.5 and -19 <= longitud <= 5


def extraer_coordenadas(html):
    coincidencia = PATRON_COORDENADAS.search(html)

    if not coincidencia:
        return None

    latitud = float(coincidencia.group(1))
    longitud = float(coincidencia.group(2))

    if not en_espana(latitud, longitud):
        return None

    return latitud, longitud


def main():
    barrido = json.loads(BARRIDO.read_text(encoding="utf-8"))
    catalogo = json.loads(CATALOGO.read_text(encoding="utf-8"))

    html_por_codigo = {
        ficha["codigo"]: ficha["html"]
        for ficha in barrido
    }

    sin_coordenadas = []

    for punto in catalogo:
        coordenadas = extraer_coordenadas(
            html_por_codigo.get(punto["codigo"], "")
        )

        if coordenadas is None:
            punto.pop("latitud", None)
            punto.pop("longitud", None)
            sin_coordenadas.append(punto["codigo"])
            continue

        punto["latitud"], punto["longitud"] = coordenadas

    # newline mantiene los saltos de línea CRLF que ya tiene el archivo.
    with CATALOGO.open("w", encoding="utf-8", newline="\r\n") as archivo:
        json.dump(catalogo, archivo, ensure_ascii=False, indent=2)

    print(f"Puntos en el catálogo: {len(catalogo)}")
    print(f"Con coordenadas: {len(catalogo) - len(sin_coordenadas)}")
    print(f"Sin coordenadas: {sin_coordenadas}")


if __name__ == "__main__":
    main()
