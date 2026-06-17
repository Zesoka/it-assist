from fastapi import APIRouter, Request, Depends, Form, HTTPException, status, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import os
import re
import tempfile
import time
import uuid
from typing import List, Dict, Any

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from docx import Document
from docx.shared import Inches
from fpdf import FPDF
import google.generativeai as genai
import httpx

from app.database import get_db
from app.auth import get_current_user
from app.models import User
from app.config import settings

# Configurar la API Key de Gemini
if settings.GEMINI_API_KEY:
    genai.configure(api_key=settings.GEMINI_API_KEY)

router = APIRouter(prefix="/scraper", tags=["YouTube Scraper"])
templates = Jinja2Templates(directory="app/templates")

# Directorio temporal para almacenar descargas creadas
TEMP_DOWNLOADS_DIR = os.path.join(tempfile.gettempdir(), "herramientas_it_downloads")
os.makedirs(TEMP_DOWNLOADS_DIR, exist_ok=True)

def extract_video_id(url: str) -> str:
    """Extrae el Video ID de una URL de YouTube utilizando Regex"""
    pattern = r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|e(?:mbed)?)\/|\S*?[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})'
    match = re.search(pattern, url)
    if not match:
        raise ValueError("URL de YouTube no válida.")
    return match.group(1)

def get_video_title(url: str, video_id: str) -> str:
    """Obtiene el título del video utilizando yt-dlp de forma rápida sin descargar el video"""
    ydl_opts = {
        'skip_download': True,
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('title', f"video_{video_id}")
    except Exception:
        return f"Transcripcion_YouTube_{video_id}"

def sanitize_filename(filename: str) -> str:
    """Remueve caracteres inválidos para nombres de archivo"""
    return re.sub(r'[\\/*?:"<>|]', "", filename).replace(" ", "_")

def clean_text_for_pdf(text: str) -> str:
    """Limpia caracteres no compatibles con fuentes latin-1 estándar de FPDF"""
    replacements = {
        '“': '"', '”': '"', '‘': "'", '’': "'", '—': '-', '–': '-',
        'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u',
        'Á': 'A', 'É': 'E', 'Í': 'I', 'Ó': 'O', 'Ú': 'U',
        'ñ': 'n', 'Ñ': 'N', 'ü': 'u', 'Ü': 'U',
        '¿': '?', '¡': '!'
    }
    for orig, rep in replacements.items():
        text = text.replace(orig, rep)
    # Codificar en latin-1 descartando caracteres no mapeables
    return text.encode('latin-1', 'replace').decode('latin-1')

def download_audio_via_ytdlp(video_id: str) -> str:
    """Descarga el audio de un video de YouTube como archivo m4a de forma optimizada sin requerir ffmpeg"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    outtmpl = os.path.join(TEMP_DOWNLOADS_DIR, f"{video_id}.%(ext)s")
    
    ydl_opts = {
        'format': '140/m4a/bestaudio/best', # AAC m4a estándar de YouTube (no requiere transcoder/ffmpeg)
        'outtmpl': outtmpl,
        'quiet': True,
        'no_warnings': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
        
    for ext in ['m4a', 'webm', 'mp3', 'aac', 'ogg', 'wav']:
        filepath = os.path.join(TEMP_DOWNLOADS_DIR, f"{video_id}.{ext}")
        if os.path.exists(filepath):
            return filepath
            
    raise Exception("No se pudo descargar el archivo de audio del video.")

def transcribe_and_format_audio(filepath: str, title: str, url: str) -> str:
    """Sube el archivo de audio a Gemini, realiza la transcripción y el formateo, y limpia el recurso remoto"""
    if not settings.GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY no está configurada en el servidor.")
        
    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    mime_type = "audio/mp4" # m4a es audio/mp4 o audio/x-m4a
    
    # 1. Iniciar la carga resumible
    init_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={settings.GEMINI_API_KEY}"
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(filesize),
        "X-Goog-Upload-Header-Content-Type": mime_type,
        "Content-Type": "application/json",
    }
    json_data = {"file": {"display_name": filename}}
    
    with httpx.Client(timeout=30.0) as client:
        response = client.post(init_url, headers=headers, json=json_data)
        if response.status_code != 200:
            raise Exception(f"Fallo al iniciar carga en Gemini: {response.status_code} - {response.text}")
            
        upload_url = response.headers.get("X-Goog-Upload-URL")
        if not upload_url:
            raise Exception("No se recibió la URL de carga de Gemini.")
            
        # 2. Subir los bytes del archivo
        with open(filepath, "rb") as f:
            file_data = f.read()
            
        upload_headers = {
            "Content-Length": str(filesize),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        }
        
        response_upload = client.post(upload_url, headers=upload_headers, content=file_data)
        if response_upload.status_code != 200:
            raise Exception(f"Fallo al cargar bytes a Gemini: {response_upload.status_code} - {response_upload.text}")
            
        metadata = response_upload.json()
        file_resource_name = metadata.get("file", {}).get("name")
        if not file_resource_name:
            raise Exception("No se recibió el nombre del recurso de archivo de Gemini.")
            
        try:
            # 3. Esperar a que el archivo se procese (ACTIVE)
            file_info_url = f"https://generativelanguage.googleapis.com/v1beta/{file_resource_name}?key={settings.GEMINI_API_KEY}"
            for _ in range(30):
                info_resp = client.get(file_info_url)
                if info_resp.status_code == 200:
                    state = info_resp.json().get("state")
                    if state == "ACTIVE":
                        break
                    elif state == "FAILED":
                        raise Exception("El procesamiento del archivo falló en los servidores de Gemini.")
                time.sleep(2)
            else:
                raise Exception("Tiempo de espera agotado para el procesamiento del archivo en Gemini.")
                
            # 4. Generar el contenido estructurado
            gen_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={settings.GEMINI_API_KEY}"
            prompt = (
                "Analiza el siguiente audio de un video de soporte técnico/infraestructura. "
                "Primero, realiza la transcripción completa de forma interna. "
                "Luego, a partir de esa transcripción, genera un documento estructurado como un 'Instructivo Técnico' o 'Guía de Procedimiento Paso a Paso' en español. "
                "El documento final debe incluir:\n"
                "1. Un título descriptivo claro en formato de Encabezado de nivel 1 (#).\n"
                "2. Un breve resumen del objetivo del instructivo.\n"
                "3. Requisitos previos o herramientas necesarias (si las hay).\n"
                "4. Los pasos ordenados secuencialmente, explicando detalladamente qué hacer. "
                "Si en el audio se mencionan comandos exactos de terminal, configuraciones, switches de red, direcciones IP o sintaxis de código, "
                "escríbelos exactamente dentro de bloques de código (```).\n"
                "5. Notas o advertencias importantes sobre el procedimiento.\n\n"
                "Por favor, genera únicamente el instructivo formateado en Markdown, sin textos introductorios adicionales."
            )
            
            payload = {
                "contents": [
                    {
                        "parts": [
                            {
                                "file_data": {
                                    "mime_type": mime_type,
                                    "file_uri": f"https://generativelanguage.googleapis.com/v1beta/{file_resource_name}"
                                }
                            },
                            {
                                "text": prompt
                            }
                        ]
                    }
                ]
            }
            
            gen_resp = client.post(gen_url, json=payload, timeout=180.0)
            if gen_resp.status_code != 200:
                raise Exception(f"Fallo al generar contenido con Gemini: {gen_resp.status_code} - {gen_resp.text}")
                
            result = gen_resp.json()
            candidates = result.get("candidates", [])
            if not candidates:
                raise Exception("No se recibió respuesta del modelo Gemini.")
                
            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise Exception("No se recibió contenido de texto de Gemini.")
                
            return parts[0].get("text", "")
            
        finally:
            # 5. Eliminar el archivo de la API de Gemini Files
            try:
                del_url = f"https://generativelanguage.googleapis.com/v1beta/{file_resource_name}?key={settings.GEMINI_API_KEY}"
                client.delete(del_url)
            except Exception:
                pass

def generate_markdown_from_content(video_id: str, title: str, url: str, markdown_content: str) -> str:
    """Guarda la transcripción formateada en formato Markdown (.md)"""
    filename = sanitize_filename(f"{title}_{video_id}") + ".md"
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        if not markdown_content.strip().startswith("# "):
            f.write(f"# {title}\n\n")
            f.write(f"- **URL del Video**: [{url}]({url})\n")
            f.write(f"- **Video ID**: `{video_id}`\n\n")
        f.write(markdown_content)
        
    return filepath

def generate_docx_from_markdown(video_id: str, title: str, url: str, markdown_content: str) -> str:
    """Genera archivo Word (.docx) analizando el contenido estructurado del Markdown"""
    filename = sanitize_filename(f"{title}_{video_id}") + ".docx"
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    doc = Document()
    doc.add_heading(title, 0)
    
    p = doc.add_paragraph()
    p.add_run("URL del Video: ").bold = True
    p.add_run(url)
    p.add_run("\nID del Video: ").bold = True
    p.add_run(video_id)
    
    lines = markdown_content.splitlines()
    in_code_block = False
    code_content = []
    
    for line in lines:
        stripped = line.strip()
        
        if stripped.startswith("```"):
            if in_code_block:
                p_code = doc.add_paragraph()
                p_code.paragraph_format.left_indent = Inches(0.5)
                run = p_code.add_run("\n".join(code_content))
                run.font.name = 'Courier New'
                code_content = []
                in_code_block = False
            else:
                in_code_block = True
            continue
            
        if in_code_block:
            code_content.append(line)
            continue
            
        if stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            doc.add_paragraph(stripped[2:], style='List Bullet')
        elif re.match(r'^\d+\.\s+', stripped):
            content = re.sub(r'^\d+\.\s+', '', stripped)
            doc.add_paragraph(content, style='List Number')
        elif stripped == "":
            continue
        else:
            p_paragraph = doc.add_paragraph()
            parts = re.split(r'(\*\*.*?\*\*)', line)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    run = p_paragraph.add_run(part[2:-2])
                    run.bold = True
                else:
                    p_paragraph.add_run(part)
                    
    doc.save(filepath)
    return filepath

def generate_pdf_from_markdown(video_id: str, title: str, url: str, markdown_content: str) -> str:
    """Genera archivo PDF usando FPDF analizando la estructura Markdown"""
    filename = sanitize_filename(f"{title}_{video_id}") + ".pdf"
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    class PDF(FPDF):
        def header(self):
            self.set_font('Helvetica', 'B', 10)
            self.cell(0, 10, 'Reporte de Transcripcion - Soporte IT', border=False, align='R')
            self.ln(10)
            
        def footer(self):
            self.set_y(-15)
            self.set_font('Helvetica', 'I', 8)
            self.cell(0, 10, f'Pagina {self.page_no()}/{{nb}}', align='C')
            
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    
    # Título principal
    pdf.set_font('Helvetica', 'B', 14)
    pdf.multi_cell(0, 8, clean_text_for_pdf(title))
    pdf.ln(4)
    
    # Metadatos
    pdf.set_font('Helvetica', 'I', 9)
    pdf.cell(0, 6, clean_text_for_pdf(f"URL: {url}"), ln=True)
    pdf.cell(0, 6, clean_text_for_pdf(f"Video ID: {video_id}"), ln=True)
    pdf.ln(6)
    
    lines = markdown_content.splitlines()
    in_code_block = False
    
    for line in lines:
        stripped = line.strip()
        if not stripped and not in_code_block:
            pdf.ln(2)
            continue
            
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
            
        if in_code_block:
            pdf.set_font('Courier', '', 9)
            pdf.multi_cell(0, 5, clean_text_for_pdf(line))
            continue
            
        pdf.set_font('Helvetica', '', 10)
        
        if stripped.startswith("# "):
            pdf.ln(4)
            pdf.set_font('Helvetica', 'B', 14)
            pdf.multi_cell(0, 7, clean_text_for_pdf(stripped[2:]))
            pdf.ln(2)
        elif stripped.startswith("## "):
            pdf.ln(3)
            pdf.set_font('Helvetica', 'B', 12)
            pdf.multi_cell(0, 6, clean_text_for_pdf(stripped[3:]))
            pdf.ln(1.5)
        elif stripped.startswith("### "):
            pdf.ln(2)
            pdf.set_font('Helvetica', 'B', 11)
            pdf.multi_cell(0, 6, clean_text_for_pdf(stripped[4:]))
            pdf.ln(1)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            pdf.multi_cell(0, 5, clean_text_for_pdf(f"o {stripped[2:]}"))
        elif re.match(r'^\d+\.\s+', stripped):
            pdf.multi_cell(0, 5, clean_text_for_pdf(stripped))
        else:
            clean_line = line.replace("**", "").replace("__", "")
            pdf.multi_cell(0, 5, clean_text_for_pdf(clean_line))
            
    pdf.output(filepath)
    return filepath

@router.get("/", response_class=HTMLResponse)
async def get_scraper_page(request: Request, current_user: User = Depends(get_current_user)):
    """Renderiza la vista del Scraper de YouTube"""
    return templates.TemplateResponse(
        "scraper.html",
        {"request": request, "user": current_user}
    )

# Diccionario global para el estado de las tareas de transcripción en segundo plano
transcription_tasks: Dict[str, Dict[str, Any]] = {}

def bg_process_transcription(task_id: str, video_url: str, video_id: str):
    """Tarea ejecutada en segundo plano para procesar el video sin bloquear el servidor web"""
    try:
        # 1. Obtener título del video
        transcription_tasks[task_id] = {
            "status": "processing",
            "message": "Obteniendo información del video desde YouTube..."
        }
        title = get_video_title(video_url, video_id)
        
        # 2. Descargar flujo de audio
        transcription_tasks[task_id] = {
            "status": "processing",
            "message": "Descargando flujo de audio optimizado (m4a)..."
        }
        audio_path = download_audio_via_ytdlp(video_id)
        
        # 3. Transcribir y formatear con Gemini
        transcription_tasks[task_id] = {
            "status": "processing",
            "message": "Subiendo audio y transcribiendo con Gemini AI (esto puede tardar)..."
        }
        try:
            markdown_content = transcribe_and_format_audio(audio_path, title, video_url)
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                
        if not markdown_content:
            raise Exception("No se pudo obtener la transcripción formateada de Gemini.")
            
        # 4. Generar archivos
        transcription_tasks[task_id] = {
            "status": "processing",
            "message": "Generando documentos exportables (Markdown, Word, PDF)..."
        }
        md_path = generate_markdown_from_content(video_id, title, video_url, markdown_content)
        docx_path = generate_docx_from_markdown(video_id, title, video_url, markdown_content)
        pdf_path = generate_pdf_from_markdown(video_id, title, video_url, markdown_content)
        
        md_file = os.path.basename(md_path)
        docx_file = os.path.basename(docx_path)
        pdf_file = os.path.basename(pdf_path)
        
        transcription_tasks[task_id] = {
            "status": "completed",
            "result": {
                "title": title,
                "video_id": video_id,
                "md_file": md_file,
                "docx_file": docx_file,
                "pdf_file": pdf_file
            }
        }
    except Exception as e:
        error_msg = str(e)
        if "Subtitles are disabled" in error_msg or "Could not find a transcript" in error_msg:
            friendly_error = "El video no tiene subtítulos o transcripciones disponibles para este ID."
        else:
            friendly_error = f"Error al procesar el video: {error_msg}"
            
        transcription_tasks[task_id] = {
            "status": "failed",
            "error": friendly_error
        }

@router.post("/process", response_class=HTMLResponse)
async def process_youtube_url(
    request: Request,
    background_tasks: BackgroundTasks,
    video_url: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """Inicia el procesamiento del video en segundo plano y retorna de inmediato el spinner de polling"""
    video_url = video_url.strip()
    if not video_url:
        return templates.TemplateResponse(
            "partials/scraper_results.html",
            {"request": request, "error": "Por favor ingrese una URL válida."}
        )
        
    try:
        video_id = extract_video_id(video_url)
    except ValueError as e:
        return templates.TemplateResponse(
            "partials/scraper_results.html",
            {"request": request, "error": str(e)}
        )

    task_id = str(uuid.uuid4())
    transcription_tasks[task_id] = {
        "status": "pending",
        "message": "Inicializando transcripción en segundo plano..."
    }
    
    # Iniciar la tarea en segundo plano sin bloquear el request
    background_tasks.add_task(bg_process_transcription, task_id, video_url, video_id)
    
    # Retornar inmediatamente un spinner que hace polling cada 3 segundos a la ruta de estado
    return HTMLResponse(
        content=f"""
        <div class="p-6 bg-white border border-slate-100 rounded-2xl custom-shadow flex flex-col items-center justify-center space-y-4 text-center"
             hx-get="/scraper/status/{task_id}" 
             hx-trigger="every 3s" 
             hx-target="#scraper-results-container" 
             hx-swap="innerHTML">
            <div class="relative w-12 h-12 flex items-center justify-center">
                <div class="absolute w-12 h-12 border-4 border-brand-100 rounded-full"></div>
                <div class="absolute w-12 h-12 border-4 border-t-brand-600 rounded-full animate-spin"></div>
            </div>
            <div>
                <span class="block text-sm font-bold text-slate-800">Iniciando proceso en segundo plano...</span>
                <span class="block text-xs text-slate-400 mt-1">Este proceso se ejecuta asíncronamente para evitar cortes por timeout. Puedes esperar aquí.</span>
            </div>
        </div>
        """
    )

@router.get("/status/{task_id}", response_class=HTMLResponse)
async def get_task_status(
    request: Request,
    task_id: str,
    current_user: User = Depends(get_current_user)
):
    """Endpoint de polling para comprobar el estado de una tarea y actualizar la UI mediante HTMX"""
    task = transcription_tasks.get(task_id)
    if not task:
        return HTMLResponse(
            content="<div class='p-4 bg-rose-50 border border-rose-100 rounded-xl text-rose-600 text-sm font-semibold'>Tarea no encontrada o expirada.</div>"
        )
        
    status_val = task.get("status")
    
    if status_val in ["pending", "processing"]:
        message = task.get("message", "Procesando...")
        return HTMLResponse(
            content=f"""
            <div class="p-6 bg-white border border-slate-100 rounded-2xl custom-shadow flex flex-col items-center justify-center space-y-4 text-center"
                 hx-get="/scraper/status/{task_id}" 
                 hx-trigger="every 3s" 
                 hx-target="#scraper-results-container" 
                 hx-swap="innerHTML">
                <div class="relative w-12 h-12 flex items-center justify-center">
                    <div class="absolute w-12 h-12 border-4 border-brand-100 rounded-full"></div>
                    <div class="absolute w-12 h-12 border-4 border-t-brand-600 rounded-full animate-spin"></div>
                </div>
                <div>
                    <span class="block text-sm font-bold text-slate-800">{message}</span>
                    <span class="block text-xs text-slate-400 mt-1">Procesando de forma segura sin límites de tiempo en el servidor...</span>
                </div>
            </div>
            """
        )
    elif status_val == "completed":
        res = task.get("result", {})
        return templates.TemplateResponse(
            "partials/scraper_results.html",
            {
                "request": request,
                "title": res.get("title"),
                "video_id": res.get("video_id"),
                "md_file": res.get("md_file"),
                "docx_file": res.get("docx_file"),
                "pdf_file": res.get("pdf_file"),
                "success": True
            }
        )
    else: # failed
        error_msg = task.get("error", "Error desconocido.")
        return templates.TemplateResponse(
            "partials/scraper_results.html",
            {"request": request, "error": error_msg}
        )

@router.get("/download/{filename}")
async def download_file(filename: str, current_user: User = Depends(get_current_user)):
    """Permite la descarga directa de un archivo de transcripción generado"""
    # Sanitizar el nombre del archivo para prevenir Path Traversal
    filename = os.path.basename(filename)
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="El archivo solicitado ya no está disponible en el servidor.")
        
    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/octet-stream"
    )
