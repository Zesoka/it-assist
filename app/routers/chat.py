from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import google.generativeai as genai
from typing import List, Dict, Any

from app.config import settings
from app.database import get_db
from app.auth import get_current_user
from app.models import User

import google.generativeai as genai
from typing import List, Dict, Any
import re
import html

from app.config import settings
from app.database import get_db
from app.auth import get_current_user
from app.models import User

router = APIRouter(prefix="/chat", tags=["IT AI Agent"])
templates = Jinja2Templates(directory="app/templates")

# Configurar la API Key de Gemini
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "Eres un ingeniero senior en Infraestructura IT operando en el sector salud. "
    "Tu especialidad abarca virtualización con clústeres Proxmox, administración de firewalls FortiGate, "
    "gestión de switches Aruba, despliegue de redes UniFi y administración de servidores Linux. "
    "Tu objetivo es asistir a los técnicos de soporte brindando diagnósticos precisos, "
    "comandos exactos y pasos de troubleshooting aplicables a estas tecnologías."
)

def format_markdown_to_html(text: str) -> str:
    """Parsea Markdown básico a HTML estructurado y seguro con clases Tailwind"""
    # 1. Escapar HTML original para evitar inyecciones XSS
    text = html.escape(text)
    
    # 2. Bloques de código: ```lenguaje\nCódigo\n```
    def replace_code_block(match):
        lang = match.group(1) or ""
        code = match.group(2)
        # Des-escapar el código para que se muestre en crudo dentro del bloque pre
        code_unescaped = html.unescape(code)
        return f'<pre class="bg-slate-900 text-slate-100 p-4 rounded-xl font-mono text-xs overflow-x-auto my-3 border border-slate-950"><code>{html.escape(code_unescaped)}</code></pre>'
    
    text = re.sub(r'```(\w*)\n(.*?)```', replace_code_block, text, flags=re.DOTALL)
    
    # 3. Código en línea: `código`
    text = re.sub(r'`(.*?)`', r'<code class="bg-slate-200 text-brand-700 px-1.5 py-0.5 rounded font-mono text-xs font-semibold">\1</code>', text)
    
    # 4. Negrita: **texto**
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong class="font-bold text-slate-900">\1</strong>', text)
    
    # 5. Listas con viñetas: líneas que empiezan con - o *
    lines = text.split("\n")
    in_list = False
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:]
            if not in_list:
                new_lines.append('<ul class="list-disc pl-5 space-y-1 my-2 text-slate-700">')
                in_list = True
            new_lines.append(f'<li>{content}</li>')
        else:
            if in_list:
                new_lines.append('</ul>')
                in_list = False
            new_lines.append(line)
            
    if in_list:
        new_lines.append('</ul>')
        
    text = "\n".join(new_lines)
    
    # 6. Párrafos
    paragraphs = text.split("\n\n")
    p_formatted = []
    for p in paragraphs:
        if p.strip():
            if "<pre" in p or "<ul" in p or "<li" in p:
                p_formatted.append(p)
            else:
                p_formatted.append(f'<p class="mb-3 leading-relaxed text-slate-700">{p.replace("\n", "<br>")}</p>')
                
    return "\n".join(p_formatted)

@router.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request, current_user: User = Depends(get_current_user)):
    """Renderiza la vista principal del Chat con la IA"""
    history = request.session.get("chat_history", [])
    
    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "user": current_user,
            "history": history,
            "gemini_configured": bool(settings.GEMINI_API_KEY)
        }
    )

@router.post("/message", response_class=HTMLResponse)
async def send_message(
    request: Request,
    message: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """Envía un mensaje al Agente de IA y retorna los fragmentos de chat actualizados mediante HTMX"""
    message = message.strip()
    if not message:
        return HTMLResponse(status_code=400, content="Mensaje vacío.")
        
    # Verificar configuración de Gemini
    if not settings.GEMINI_API_KEY:
        return templates.TemplateResponse(
            "partials/chat_message_error.html",
            {
                "request": request,
                "error_message": "La API Key de Gemini no está configurada en el servidor. Por favor, agregue 'GEMINI_API_KEY' en el archivo .env en la raíz del proyecto y reinicie el servicio."
            }
        )

    # 1. Obtener historial de la sesión (contiene los mensajes planos o formateados de antes)
    # Para enviarle el historial plano a Gemini, guardamos una versión plana en sesión
    chat_history_raw = request.session.get("chat_history_raw", [])
    
    # 2. Formatear historial plano para el SDK de Gemini
    gemini_history = []
    for msg in chat_history_raw:
        gemini_history.append({
            "role": "user" if msg["role"] == "user" else "model",
            "parts": [msg["text"]]
        })

    try:
        # 3. Inicializar el modelo con el prompt del sistema
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=SYSTEM_PROMPT
        )
        
        # 4. Iniciar chat con historial e interactuar
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(message)
        response_raw_text = response.text
        
        # 5. Formatear la respuesta raw a HTML para renderizar
        response_html = format_markdown_to_html(response_raw_text)

        # Guardar en el historial de sesión formateado para mostrar en refresco de página
        chat_history = request.session.get("chat_history", [])
        chat_history.append({"role": "user", "text": message})
        chat_history.append({"role": "assistant", "text": response_html})
        request.session["chat_history"] = chat_history

        # Guardar en el historial crudo para enviarlo a la API en subsecuentes llamadas
        chat_history_raw.append({"role": "user", "text": message})
        chat_history_raw.append({"role": "assistant", "text": response_raw_text})
        request.session["chat_history_raw"] = chat_history_raw

        # 6. Renderizar y retornar únicamente la burbuja del usuario y la respuesta de la IA
        return templates.TemplateResponse(
            "partials/chat_messages.html",
            {
                "request": request,
                "user_message": message,
                "assistant_message": response_html
            }
        )
        
    except Exception as e:
        return templates.TemplateResponse(
            "partials/chat_message_error.html",
            {
                "request": request,
                "error_message": f"Error al conectarse con Gemini API: {str(e)}"
            }
        )

@router.delete("/clear", response_class=HTMLResponse)
async def clear_chat(request: Request, current_user: User = Depends(get_current_user)):
    """Limpia el historial de chat de la sesión y retorna la caja de mensajes vacía"""
    request.session["chat_history"] = []
    request.session["chat_history_raw"] = []
    return HTMLResponse(
        content='<div id="chat-messages" class="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-50 border border-slate-100 rounded-xl min-h-[400px] max-h-[500px]"></div>',
        status_code=200
    )

