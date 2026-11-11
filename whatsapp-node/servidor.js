// Servicio mínimo de WhatsApp para los avisos de vigilancia.
//
// Imita las dos llamadas de WAHA que usa vigilancia.py (comprobar si un
// número tiene WhatsApp y enviar un texto), así que la app Python solo
// necesita WAHA_URL y WAHA_API_KEY apuntando a este servicio.
//
// Arranque: npm start  (lee el .env general del proyecto, el de la carpeta
// superior: WAHA_URL, WAHA_API_KEY y, opcional, WHATSAPP_HOST).
// Vincular WhatsApp: abrir http://IP:PUERTO/qr?key=WAHA_API_KEY y escanear el QR.

import http from 'node:http'
import { rm } from 'node:fs/promises'
import { timingSafeEqual } from 'node:crypto'

import makeWASocket, {
    DisconnectReason,
    useMultiFileAuthState
} from '@whiskeysockets/baileys'
import pino from 'pino'
import QRCode from 'qrcode'

// Las mismas variables que usa la app Python, para tenerlo todo en un .env.
const API_KEY = process.env.WAHA_API_KEY
const PUERTO = Number(new URL(process.env.WAHA_URL || 'http://x:3200').port || 3200)
const HOST = process.env.WHATSAPP_HOST || '0.0.0.0'
const CARPETA_SESION = process.env.CARPETA_SESION || './sesion'

// Pausa antes de reconectar, para no saturar el Synology si algo falla.
const ESPERA_RECONEXION_MS = 10_000
const MAX_CUERPO_BYTES = 64 * 1024

if (!API_KEY || API_KEY.length < 16) {
    console.error('Falta WAHA_API_KEY en el .env del proyecto (mínimo 16 caracteres).')
    process.exit(1)
}

// --------------------------------------------------
// CONEXIÓN CON WHATSAPP
// --------------------------------------------------

let sock = null
let conectado = false
let qrActual = null
let reconexionPendiente = null

async function conectar() {
    reconexionPendiente = null

    const { state, saveCreds } = await useMultiFileAuthState(CARPETA_SESION)

    const socket = makeWASocket({
        auth: state,
        logger: pino({ level: 'silent' }),
        markOnlineOnConnect: false,
        syncFullHistory: false
    })

    sock = socket

    socket.ev.on('creds.update', saveCreds)

    socket.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
        if (qr) {
            qrActual = qr
            console.log('Nuevo código QR disponible en /qr')
        }

        if (connection === 'open') {
            conectado = true
            qrActual = null
            console.log('WhatsApp conectado.')
        }

        if (connection === 'close') {
            conectado = false

            // Ignora cierres de sockets antiguos.
            if (sock !== socket) {
                return
            }

            const codigo = lastDisconnect?.error?.output?.statusCode

            if (codigo === DisconnectReason.loggedOut) {
                console.log('Sesión cerrada desde el móvil. Se pedirá un QR nuevo.')
                await rm(CARPETA_SESION, { recursive: true, force: true })
            } else {
                console.log(`Conexión cerrada (código ${codigo}). Reintentando...`)
            }

            // Tras escanear el QR, WhatsApp pide reiniciar la conexión al momento.
            programarReconexion(
                codigo === DisconnectReason.restartRequired ? 1000 : ESPERA_RECONEXION_MS
            )
        }
    })
}

function programarReconexion(espera = ESPERA_RECONEXION_MS) {
    if (reconexionPendiente) {
        return
    }

    reconexionPendiente = setTimeout(() => {
        conectar().catch(error => {
            console.error('Error al conectar:', error.message)
            programarReconexion()
        })
    }, espera)
}

// --------------------------------------------------
// UTILIDADES HTTP
// --------------------------------------------------

function claveValida(recibida) {
    const a = Buffer.from(String(recibida || ''))
    const b = Buffer.from(API_KEY)
    return a.length === b.length && timingSafeEqual(a, b)
}

function responder(res, estado, datos) {
    res.writeHead(estado, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store'
    })
    res.end(JSON.stringify(datos))
}

function responderHtml(res, cuerpo, recargarSegundos) {
    res.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store'
    })
    res.end(`<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="${recargarSegundos}">
<title>WhatsApp cargadores</title>
<style>body{font-family:sans-serif;text-align:center;padding:24px}</style>
</head><body>${cuerpo}</body></html>`)
}

function leerJson(req) {
    return new Promise((resolver, rechazar) => {
        let datos = ''

        req.on('data', trozo => {
            datos += trozo
            if (datos.length > MAX_CUERPO_BYTES) {
                rechazar(new Error('Cuerpo demasiado grande'))
                req.destroy()
            }
        })

        req.on('end', () => {
            try {
                resolver(JSON.parse(datos || '{}'))
            } catch {
                rechazar(new Error('JSON no válido'))
            }
        })

        req.on('error', rechazar)
    })
}

// Acepta "34612345678" o "34612345678@c.us" (formato de WAHA).
function telefonoAJid(valor) {
    const digitos = String(valor || '').replace(/@.*$/, '')

    if (!/^[1-9]\d{7,14}$/.test(digitos)) {
        return null
    }

    return `${digitos}@s.whatsapp.net`
}

// --------------------------------------------------
// RUTAS
// --------------------------------------------------

async function atender(req, res) {
    const url = new URL(req.url, 'http://localhost')

    // Página para escanear el QR desde el navegador.
    if (req.method === 'GET' && url.pathname === '/qr') {
        if (!claveValida(url.searchParams.get('key'))) {
            return responder(res, 401, { message: 'Unauthorized' })
        }

        if (conectado) {
            return responderHtml(res, '<h1>✅ WhatsApp conectado</h1>', 30)
        }

        if (qrActual) {
            const imagen = await QRCode.toDataURL(qrActual, { width: 320 })
            return responderHtml(
                res,
                '<h1>Escanea el código con WhatsApp</h1>' +
                '<p>Ajustes &gt; Dispositivos vinculados &gt; Vincular un dispositivo</p>' +
                `<img src="${imagen}" alt="Código QR">` +
                '<p>La página se actualiza sola.</p>',
                10
            )
        }

        return responderHtml(res, '<h1>Conectando con WhatsApp...</h1>', 3)
    }

    if (!url.pathname.startsWith('/api/')) {
        return responder(res, 404, { message: 'Not Found' })
    }

    if (!claveValida(req.headers['x-api-key'])) {
        return responder(res, 401, { message: 'Unauthorized' })
    }

    if (req.method === 'GET' && url.pathname === '/api/estado') {
        return responder(res, 200, { conectado })
    }

    if (!conectado) {
        return responder(res, 503, { message: 'WhatsApp no está conectado' })
    }

    if (req.method === 'GET' && url.pathname === '/api/contacts/check-exists') {
        const jid = telefonoAJid(url.searchParams.get('phone'))

        if (!jid) {
            return responder(res, 400, { message: 'Número no válido' })
        }

        const [resultado] = (await sock.onWhatsApp(jid)) || []

        return responder(res, 200, {
            numberExists: Boolean(resultado?.exists),
            chatId: resultado?.jid ?? null
        })
    }

    if (req.method === 'POST' && url.pathname === '/api/sendText') {
        const cuerpo = await leerJson(req)
        const jid = telefonoAJid(cuerpo.chatId)
        const texto = typeof cuerpo.text === 'string' ? cuerpo.text : ''

        if (!jid || !texto || texto.length > 4096) {
            return responder(res, 400, { message: 'chatId o text no válidos' })
        }

        await sock.sendMessage(jid, { text: texto })

        return responder(res, 201, { ok: true })
    }

    return responder(res, 404, { message: 'Not Found' })
}

const servidor = http.createServer((req, res) => {
    atender(req, res).catch(error => {
        console.error('Error atendiendo la petición:', error.message)
        if (!res.headersSent) {
            responder(res, 500, { message: 'Error interno' })
        }
    })
})

servidor.listen(PUERTO, HOST, () => {
    console.log(`Servicio de WhatsApp escuchando en ${HOST}:${PUERTO}`)
})

conectar().catch(error => {
    console.error('Error al conectar:', error.message)
    programarReconexion()
})
