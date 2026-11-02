
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
        print("[LOG] 1. Obteniendo coordenadas...", flush=True)
        latitud, longitud = obtener_coordenadas(localidad)
        print("[LOG] 1. Coordenadas obtenidas correctamente", flush=True)

        print("[LOG] 2. Creando límites...", flush=True)
        limites = crear_limites(
            latitud,
            longitud,
            RADIO_KM
        )
        print("[LOG] 2. Límites creados correctamente", flush=True)

        print("[LOG] 3. Creando sesión con Iberdrola...", flush=True)
        sesion = crear_sesion()
        print("[LOG] 3. Sesión creada correctamente", flush=True)

        print("[LOG] 4. Buscando cargadores...", flush=True)
        cargadores = buscar_cargadores(
            sesion,
            limites
        )
        print(f"[LOG] 4. Cargadores encontrados: {len(cargadores)}", flush=True)

        print("[LOG] 5. Consultando disponibilidad...", flush=True)
        cargadores = consultar_disponibilidad(
            sesion,
            cargadores
        )
        print("[LOG] 5. Disponibilidad consultada", flush=True)

        print("[LOG] 6. Normalizando resultados...", flush=True)
        resultados = [
            extraer_datos(cargador)
            for cargador in cargadores
        ]

        print("[LOG] 7. Ordenando resultados...", flush=True)
        resultados.sort(
            key=lambda c: (
                c["disponible"] is not True,
                -c["tomas_disponibles"]
            )
        )

        print("[LOG] Búsqueda finalizada correctamente", flush=True)

        return {
            "localidad": localidad,
            "radio_km": RADIO_KM,
            "total": len(resultados),
            "cargadores": resultados
        }

    except requests.RequestException as error:
        print(f"[ERROR] Error de conexión: {repr(error)}", flush=True)
        raise HTTPException(
            status_code=502,
            detail="No ha sido posible conectar con los servicios externos."
        )

    except (ValueError, RuntimeError) as error:
        print(f"[ERROR] Error de datos o ejecución: {repr(error)}", flush=True)
        raise HTTPException(
            status_code=502,
            detail=str(error)
        )

    except Exception as error:
        print(f"[ERROR] Error inesperado: {repr(error)}", flush=True)
        raise HTTPException(
            status_code=500,
            detail="Error interno al buscar cargadores."
        )
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
