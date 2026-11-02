
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
import requests

from buscador import (
    RADIO_KM,
    crear_sesion,
    obtener_coordenadas,
    crear_limites,
    buscar_cargadores,
    consultar_disponibilidad,
    extraer_datos
)

app = FastAPI(
    title="Buscador de Cargadores Iberdrola",
    description="Consulta de puntos de recarga y disponibilidad.",
    version="1.0.0"
)

# Ruta de la página web
FRONTEND = Path(__file__).resolve().parent.parent / "public"


# --------------------------------------------------
# MOSTRAR LA PÁGINA WEB
# --------------------------------------------------

@app.get("/")
def inicio():
    archivo = FRONTEND / "index.html"

    if not archivo.exists():
        raise HTTPException(
            status_code=404,
            detail="No se encuentra la página web."
        )

    return FileResponse(archivo)


# --------------------------------------------------
# BUSCAR CARGADORES
# --------------------------------------------------

@app.get("/api/cargadores")
def buscar(
    localidad: str = Query(
        ...,
        min_length=2,
        max_length=100
    )
):
    localidad = localidad.strip()

    if not localidad:
        raise HTTPException(
            status_code=400,
            detail="Debes indicar una localidad."
        )

    try:
        # 1. Obtener coordenadas
        latitud, longitud = obtener_coordenadas(localidad)

        # 2. Crear área de búsqueda
        limites = crear_limites(
            latitud,
            longitud,
            RADIO_KM
        )

        # 3. Conectar con Iberdrola
        sesion = crear_sesion()

        # 4. Buscar cargadores
        cargadores = buscar_cargadores(
            sesion,
            limites
        )

        # 5. Consultar disponibilidad
        cargadores = consultar_disponibilidad(
            sesion,
            cargadores
        )

        # 6. Normalizar resultados
        resultados = [
            extraer_datos(cargador)
            for cargador in cargadores
        ]

        # 7. Ordenar: disponibles primero
        resultados.sort(
            key=lambda c: (
                c["disponible"] is not True,
                -c["tomas_disponibles"]
            )
        )

        return {
            "localidad": localidad,
            "radio_km": RADIO_KM,
            "total": len(resultados),
            "cargadores": resultados
        }

    except requests.RequestException:
        raise HTTPException(
            status_code=502,
            detail="No ha sido posible conectar con los servicios externos."
        )

    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=502,
            detail=str(error)
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Error interno al buscar cargadores."
        )
