from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import httpx
import datetime
import socket
import subprocess
import platform
from sqlalchemy import text
from typing import Optional

from app.config import settings
from app.database import get_db
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="", tags=["Dashboard"])
templates = Jinja2Templates(directory="app/templates")

# Cache simple en memoria para el clima
weather_cache = {
    "data": None,
    "last_fetched": None
}
CACHE_DURATION_MINUTES = 15

# Coordenadas de Ingeniero Budge, Buenos Aires
LATITUDE = -34.7176
LONGITUDE = -58.4593

def get_weather_desc(code: int) -> tuple[str, str]:
    """Retorna una tupla con (descripción_en_español, icono_fontawesome)"""
    mapping = {
        0: ("Cielo despejado", "fa-sun text-yellow-500"),
        1: ("Principalmente despejado", "fa-cloud-sun text-gray-400"),
        2: ("Parcialmente nublado", "fa-cloud text-gray-400"),
        3: ("Nublado", "fa-cloud text-gray-500"),
        45: ("Niebla", "fa-smog text-gray-400"),
        48: ("Niebla con escarcha", "fa-snowflake text-blue-200"),
        51: ("Llovizna ligera", "fa-cloud-rain text-blue-300"),
        53: ("Llovizna moderada", "fa-cloud-rain text-blue-400"),
        55: ("Llovizna densa", "fa-cloud-showers-heavy text-blue-500"),
        61: ("Lluvia débil", "fa-cloud-rain text-blue-400"),
        63: ("Lluvia moderada", "fa-cloud-showers-heavy text-blue-500"),
        65: ("Lluvia fuerte", "fa-cloud-showers-water text-blue-600"),
        71: ("Nieve ligera", "fa-snowflake text-blue-100"),
        73: ("Nieve moderada", "fa-snowflake text-blue-300"),
        75: ("Nieve fuerte", "fa-snowflake text-blue-500"),
        80: ("Lluvia torrencial débil", "fa-cloud-showers-heavy text-blue-400"),
        81: ("Lluvia torrencial moderada", "fa-cloud-showers-heavy text-blue-500"),
        82: ("Lluvia torrencial violenta", "fa-cloud-showers-water text-blue-600"),
        95: ("Tormenta eléctrica", "fa-cloud-bolt text-amber-500"),
        96: ("Tormenta con granizo leve", "fa-cloud-meatball text-blue-400"),
        99: ("Tormenta con granizo fuerte", "fa-cloud-meatball text-blue-600"),
    }
    return mapping.get(code, ("Desconocido", "fa-question-circle text-gray-400"))

def check_service_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_gateway_ip() -> str:
    try:
        with open("/proc/net/route") as fh:
            for line in fh:
                fields = line.strip().split()
                if len(fields) > 2 and fields[1] == '00000000':
                    gw_hex = fields[2]
                    parts = [int(gw_hex[i:i+2], 16) for i in range(0, 8, 2)]
                    parts.reverse()
                    return ".".join(map(str, parts))
    except Exception:
        pass
    return "192.168.2.1"

def ping_ip(ip: str) -> bool:
    try:
        cmd = ["ping", "-n", "1", "-w", "1000", ip] if platform.system() == "Windows" else ["ping", "-c", "1", "-W", "1", ip]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False

def check_internet() -> bool:
    return check_service_port("8.8.8.8", 53, timeout=1.0)

def check_db(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

@router.get("/", response_class=HTMLResponse)
async def get_root(
    request: Request, 
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Redirige al dashboard o renderiza la vista principal directamente"""
    # Obtenemos la fecha y hora formateada en español
    now = datetime.datetime.now()
    
    # Días y meses en español
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    meses = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", 
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
    ]
    
    fecha_formateada = f"{dias[now.weekday()]}, {now.day} de {meses[now.month - 1]} de {now.year}"
    hora_formateada = now.strftime("%H:%M")

    # Diagnóstico de red e infraestructura
    gw_ip = get_gateway_ip()
    net_status = {
        "local_ip": get_local_ip(),
        "gateway_ip": gw_ip,
        "gateway_online": ping_ip(gw_ip),
        "internet": check_internet(),
        "db": check_db(db)
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": current_user,
            "fecha": fecha_formateada,
            "hora": hora_formateada,
            "phpipam_url": settings.PHPIPAM_URL,
            "safweb_url": settings.SAFWEB_URL,
            "net_status": net_status
        }
    )

@router.get("/dashboard/weather", response_class=HTMLResponse)
async def get_weather(request: Request):
    """Retorna el componente HTML parcial para el widget del clima usando HTMX"""
    now = datetime.datetime.now()
    use_cache = False
    
    if weather_cache["data"] and weather_cache["last_fetched"]:
        elapsed = (now - weather_cache["last_fetched"]).total_seconds() / 60
        if elapsed < CACHE_DURATION_MINUTES:
            use_cache = True
            
    if use_cache:
        weather_data = weather_cache["data"]
    else:
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    current = data.get("current", {})
                    
                    weather_desc, weather_icon = get_weather_desc(current.get("weather_code", 0))
                    
                    weather_data = {
                        "temp": current.get("temperature_2m"),
                        "feels_like": current.get("apparent_temperature"),
                        "humidity": current.get("relative_humidity_2m"),
                        "wind_speed": current.get("wind_speed_10m"),
                        "desc": weather_desc,
                        "icon": weather_icon,
                        "location": "Ingeniero Budge, BA",
                        "error": False
                    }
                    weather_cache["data"] = weather_data
                    weather_cache["last_fetched"] = now
                else:
                    raise Exception("Status code no 200")
        except Exception as e:
            # Fallback en caso de error de red o de la API
            weather_data = {
                "temp": "--",
                "feels_like": "--",
                "humidity": "--",
                "wind_speed": "--",
                "desc": "Información del clima no disponible",
                "icon": "fa-triangle-exclamation text-yellow-500",
                "location": "Ingeniero Budge, BA",
                "error": True
            }
            # No guardamos errores en la caché principal
            if weather_cache["data"]:
                weather_data = weather_cache["data"] # Si hay caché vieja, úsala

    return templates.TemplateResponse(
        "partials/weather_widget.html",
        {
            "request": request,
            "weather": weather_data
        }
    )
