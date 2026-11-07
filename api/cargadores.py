
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse
import requests
import time
from threading import Lock, BoundedSemaphore
from pydantic import BaseModel
from api.fenie import router as fenie_router
from fastapi.staticfiles import StaticFiles
from buscador import (RADIO_KM,crear_sesion,obtener_coordenadas,crear_limites,buscar_cargadores,consultar_disponibilidad,extraer_datos)
from vigilancia import (iniciar_vigilancia,esta_vigilado)

# Ruta de la página web
FRONTEND = Path(__file__).resolve().parent.parent / "public"

app = FastAPI(
    title="Buscador de Cargadores Iberdrola y Feníe Energía",
    description="Consulta de puntos de recarga y disponibilidad.",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None
)
app.include_router(fenie_router)
app.mount("/iconos",StaticFiles(directory=str(FRONTEND / "iconos")),name="iconos")


class SolicitudVigilancia(BaseModel):
    cuprId: int
    nombre: str
    direccion: str = ""
    localidad: str = ""

# --------------------------------------------------
# CABECERAS DE SEGURIDAD HTTP
# --------------------------------------------------

@app.middleware("http")
async def cabeceras_seguridad(request: Request, call_next):
    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )

    return response




# --------------------------------------------------
# PROTECCIÓN DE LA API
# --------------------------------------------------

MAX_PETICIONES = 10
VENTANA_SEGUNDOS = 60

peticiones_por_ip = {}
limite_lock = Lock()

busquedas_semaforo = BoundedSemaphore(1)


def comprobar_limite(ip):
    ahora = time.monotonic()

    with limite_lock:
        historial = peticiones_por_ip.get(ip, [])

        # Conservar únicamente las peticiones del último minuto
        historial = [
            momento for momento in historial
            if ahora - momento < VENTANA_SEGUNDOS
        ]

        if len(historial) >= MAX_PETICIONES:
            peticiones_por_ip[ip] = historial
            return False

        historial.append(ahora)
        peticiones_por_ip[ip] = historial

        return True

def proteger_busqueda(request: Request):
    ip = request.client.host if request.client else "desconocida"

    if not comprobar_limite(ip):
        raise HTTPException(
            status_code=429,
            detail="Has superado el límite de búsquedas. Espera un minuto."
        )

    if not busquedas_semaforo.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Ya hay otra búsqueda en curso. Inténtalo de nuevo."
        )

    try:
        yield
    finally:
        busquedas_semaforo.release()

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
    ),
    _: None = Depends(proteger_busqueda)
):
    
    localidad = localidad.strip()

    if not localidad:
        raise HTTPException(
            status_code=400,
            detail="Debes indicar una localidad."
        )

    if len(localidad) > 25:
        raise HTTPException(
            status_code=400,
            detail="El nombre de la localidad es demasiado largo."
        )

    if any(ord(caracter) < 32 or ord(caracter) == 127 for caracter in localidad):
        raise HTTPException(
            status_code=400,
            detail="El nombre de la localidad contiene caracteres no permitidos."
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
            detail="No se ha podido procesar la búsqueda. Inténtalo de nuevo."
        )

    except Exception as error:
        print(f"[ERROR] Error inesperado: {repr(error)}", flush=True)
        raise HTTPException(
            status_code=500,
            detail="Error interno al buscar cargadores."
        )
    
@app.post("/api/vigilancia")
        

def activar_vigilancia(solicitud: SolicitudVigilancia):

    try:
        iniciada = iniciar_vigilancia(
            solicitud.cuprId,
            solicitud.nombre,
            solicitud.direccion,
            solicitud.localidad
        )

        if not iniciada:
            return {
                "ok": True,
                "mensaje": "Este cargador ya está siendo vigilado."
            }

        return {
            "ok": True,
            "mensaje": "Vigilancia iniciada correctamente."
        }

    except (ValueError, RuntimeError) as error:
        print(f"[ERROR] Vigilancia: {error}", flush=True)

        raise HTTPException(
            status_code=503,
            detail="No se ha podido iniciar la vigilancia."
        )

