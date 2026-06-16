from fastapi import APIRouter, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import os
import re
import tempfile
from typing import List, Dict, Any

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from docx import Document
from fpdf import FPDF

from app.database import get_db
from app.auth import get_current_user
from app.models import User

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

def parse_vtt(vtt_content: str) -> List[Dict[str, Any]]:
    """Parsea un archivo VTT y retorna la transcripción en formato estructurado"""
    transcript = []
    pattern = r'(?:(\d{2}):)?(\d{2}):(\d{2})\.(\d{3})\s+-->\s+(?:(\d{2}):)?(\d{2}):(\d{2})\.(\d{3})'
    
    lines = vtt_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = re.match(pattern, line)
        if match:
            hours = int(match.group(1)) if match.group(1) else 0
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            ms = int(match.group(4))
            start_sec = hours * 3600 + minutes * 60 + seconds + ms / 1000.0
            
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip() != "" and not re.match(pattern, lines[i].strip()):
                clean_line = re.sub(r'<[^>]+>', '', lines[i].strip())
                if clean_line:
                    text_lines.append(clean_line)
                i += 1
            
            if text_lines:
                transcript.append({
                    'start': start_sec,
                    'text': " ".join(text_lines)
                })
            continue
        i += 1
        
    return transcript

def get_transcript_via_ytdlp(video_id: str) -> List[Dict[str, Any]]:
    """Descarga y parsea subtítulos/capturas usando yt-dlp como fallback"""
    import tempfile
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tempdir:
        ydl_opts = {
            'skip_download': True,
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['es.*', 'en.*'],
            'ignore_no_formats_error': True,
            'ignoreerrors': True,
            'outtmpl': os.path.join(tempdir, '%(id)s'),
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        files = os.listdir(tempdir)
        sub_file = None
        for lang in ['es', 'en']:
            for f in files:
                if re.search(rf"\.{lang}(-[a-zA-Z0-9]+)?\.vtt$", f):
                    sub_file = f
                    break
            if sub_file:
                break
                
        if not sub_file:
            for f in files:
                if f.endswith(".vtt"):
                    sub_file = f
                    break
                    
        if not sub_file:
            raise Exception("No se encontraron subtítulos ni transcripciones disponibles para este video.")
            
        filepath = os.path.join(tempdir, sub_file)
        with open(filepath, "r", encoding="utf-8") as f:
            vtt_content = f.read()
            
        return parse_vtt(vtt_content)

def generate_markdown(video_id: str, title: str, url: str, transcript: List[Dict[str, Any]]) -> str:
    """Genera archivo Markdown y retorna su ruta"""
    filename = sanitize_filename(f"{title}_{video_id}") + ".md"
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Transcripción: {title}\n\n")
        f.write(f"- **URL del Video**: [{url}]({url})\n")
        f.write(f"- **Video ID**: `{video_id}`\n\n")
        f.write("## Contenido\n\n")
        
        for item in transcript:
            start_sec = int(item['start'])
            minutes = start_sec // 60
            seconds = start_sec % 60
            timestamp = f"[{minutes:02d}:{seconds:02d}]"
            f.write(f"**{timestamp}** {item['text']}\n\n")
            
    return filepath

def generate_docx(video_id: str, title: str, url: str, transcript: List[Dict[str, Any]]) -> str:
    """Genera archivo Word (.docx) y retorna su ruta"""
    filename = sanitize_filename(f"{title}_{video_id}") + ".docx"
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    doc = Document()
    doc.add_heading(title, 0)
    
    # Metadatos del documento
    p = doc.add_paragraph()
    p.add_run("URL del Video: ").bold = True
    p.add_run(url)
    p.add_run("\nID del Video: ").bold = True
    p.add_run(video_id)
    
    doc.add_heading("Contenido de la Transcripción", level=1)
    
    for item in transcript:
        start_sec = int(item['start'])
        minutes = start_sec // 60
        seconds = start_sec % 60
        timestamp = f"[{minutes:02d}:{seconds:02d}]"
        
        tp = doc.add_paragraph()
        tp.add_run(f"{timestamp} ").bold = True
        tp.add_run(item['text'])
        
    doc.save(filepath)
    return filepath

def generate_pdf(video_id: str, title: str, url: str, transcript: List[Dict[str, Any]]) -> str:
    """Genera archivo PDF utilizando FPDF y retorna su ruta"""
    filename = sanitize_filename(f"{title}_{video_id}") + ".pdf"
    filepath = os.path.join(TEMP_DOWNLOADS_DIR, filename)
    
    class PDF(FPDF):
        def header(self):
            self.set_font('Helvetica', 'B', 12)
            self.cell(0, 10, 'Reporte de Transcripcion - Soporte IT', border=False, align='C')
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
    pdf.multi_cell(0, 10, clean_text_for_pdf(title))
    pdf.ln(5)
    
    # Metadatos
    pdf.set_font('Helvetica', 'I', 9)
    pdf.cell(0, 6, clean_text_for_pdf(f"URL: {url}"), ln=True)
    pdf.cell(0, 6, clean_text_for_pdf(f"Video ID: {video_id}"), ln=True)
    pdf.ln(10)
    
    # Transcripción
    pdf.set_font('Helvetica', '', 10)
    for item in transcript:
        start_sec = int(item['start'])
        minutes = start_sec // 60
        seconds = start_sec % 60
        timestamp = f"[{minutes:02d}:{seconds:02d}]"
        
        text_line = f"{timestamp} {item['text']}"
        pdf.multi_cell(0, 6, clean_text_for_pdf(text_line))
        pdf.ln(2)
        
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
        
        # 2. Descargar transcripción (idioma preferido: español, luego inglés)
        transcript = None
        try:
            try:
                # API moderna (versión >= 0.6.2, requiere instanciación)
                yt_api = YouTubeTranscriptApi()
                transcript_list = yt_api.list(video_id)
            except AttributeError:
                # API antigua (versión < 0.6.2, métodos estáticos)
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                
            try:
                # Buscar español o inglés
                transcript_obj = transcript_list.find_transcript(['es', 'en'])
                fetched = transcript_obj.fetch()
                transcript = fetched.to_raw_data()
            except Exception:
                # Intentar con el primer idioma disponible y traducir a español si es posible
                first_transcript = next(iter(transcript_list))
                try:
                    fetched = first_transcript.translate('es').fetch()
                    transcript = fetched.to_raw_data()
                except Exception:
                    fetched = first_transcript.fetch()
                    transcript = fetched.to_raw_data()
        except Exception as api_err:
            # Fallback secundario directo si falla la lista
            try:
                try:
                    yt_api = YouTubeTranscriptApi()
                    fetched = yt_api.fetch(video_id)
                except AttributeError:
                    fetched = YouTubeTranscriptApi.get_transcript(video_id, languages=['es', 'en'])
                
                if isinstance(fetched, list):
                    transcript = fetched
                else:
                    transcript = fetched.to_raw_data()
            except Exception as api_err2:
                # Fallback terciario con yt-dlp
                try:
                    transcript = get_transcript_via_ytdlp(video_id)
                except Exception as ytdlp_err:
                    raise Exception(f"Fallo en youtube-transcript-api (error: {str(api_err)}) y en yt-dlp (error: {str(ytdlp_err)})")

        if not transcript:
            raise Exception("No se pudo recuperar ninguna transcripción para este video.")

        # 3. Generar archivos asíncronos en servidor
        md_path = generate_markdown(video_id, title, video_url, transcript)
        docx_path = generate_docx(video_id, title, video_url, transcript)
        pdf_path = generate_pdf(video_id, title, video_url, transcript)
        
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
