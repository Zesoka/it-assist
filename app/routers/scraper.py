from fastapi import APIRouter, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import os
import re
import tempfile
import time
from typing import List, Dict, Any

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from docx import Document
from docx.shared import Inches
from fpdf import FPDF
import google.generativeai as genai

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
        
    audio_file = genai.upload_file(path=filepath)
    
    try:
        while audio_file.state.name == "PROCESSING":
            time.sleep(2)
            audio_file = genai.get_file(audio_file.name)
            
        if audio_file.state.name != "ACTIVE":
            raise Exception(f"El procesamiento en Gemini falló con estado: {audio_file.state.name}")
            
        model = genai.GenerativeModel("gemini-2.5-flash")
        
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
        
        response = model.generate_content([audio_file, prompt])
        return response.text
        
    finally:
        try:
            genai.delete_file(audio_file.name)
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

@router.post("/process", response_class=HTMLResponse)
async def process_youtube_url(
    request: Request,
    video_url: str = Form(...),
    current_user: User = Depends(get_current_user)
):
    """Procesa el video, descarga la transcripción y genera los archivos en segundo plano"""
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

    try:
        # 1. Obtener título del video (rápido vía yt-dlp)
        title = get_video_title(video_url, video_id)
        
        # 2. Descargar flujo de audio
        audio_path = download_audio_via_ytdlp(video_id)
        
        # 3. Transcribir y formatear inteligentemente con Gemini
        try:
            markdown_content = transcribe_and_format_audio(audio_path, title, video_url)
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                
        if not markdown_content:
            raise Exception("No se pudo obtener la transcripción formateada de Gemini.")
            
        # 4. Generar archivos
        md_path = generate_markdown_from_content(video_id, title, video_url, markdown_content)
        docx_path = generate_docx_from_markdown(video_id, title, video_url, markdown_content)
        pdf_path = generate_pdf_from_markdown(video_id, title, video_url, markdown_content)
        
        # Obtener los nombres de archivo para pasarlos al frontend
        md_file = os.path.basename(md_path)
        docx_file = os.path.basename(docx_path)
        pdf_file = os.path.basename(pdf_path)

        return templates.TemplateResponse(
            "partials/scraper_results.html",
            {
                "request": request,
                "title": title,
                "video_id": video_id,
                "md_file": md_file,
                "docx_file": docx_file,
                "pdf_file": pdf_file,
                "success": True
            }
        )

    except Exception as e:
        # Errores comunes: No hay subtítulos habilitados, video privado, etc.
        error_msg = str(e)
        if "Subtitles are disabled" in error_msg or "Could not find a transcript" in error_msg:
            friendly_error = "El video no tiene subtítulos o transcripciones disponibles para este ID."
        else:
            friendly_error = f"Error al procesar el video: {error_msg}"
            
        return templates.TemplateResponse(
            "partials/scraper_results.html",
            {"request": request, "error": friendly_error}
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
