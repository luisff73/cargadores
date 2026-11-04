#!/bin/sh

# Directorio del proyecto
DIR="/volume1/web/cargadores"

# Archivo de registro
LOG="$DIR/uvicorn.log"

# Fecha actual
FECHA=$(date +"%Y%m%d_%H%M%S")

# Comprobar que existe el registro y no está vacío
if [ -s "$LOG" ]; then

    # Crear copia comprimida
    cp "$LOG" "$DIR/uvicorn_$FECHA.log"
    gzip "$DIR/uvicorn_$FECHA.log"

    # Vaciar el registro actual sin detener Uvicorn
    : > "$LOG"

fi

# Eliminar registros con más de 30 días
find "$DIR" -name "uvicorn_*.log.gz" -type f -mtime +30 -delete