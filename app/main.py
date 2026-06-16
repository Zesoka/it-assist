from fastapi import FastAPI, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
import datetime

from app.config import settings
from app.database import get_db, engine
from app.models import Base, User
from app.auth import (
    verify_password, 
    create_session_token, 
    get_current_user_optional, 
    COOKIE_NAME
)

# Importar enrutadores
from app.routers import admin, dashboard, scraper, chat

# Crear las tablas en la base de datos automáticamente al iniciar
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Herramientas IT Corporativo",
    description="Panel interno para soporte e infraestructura de IT en el sector salud",
    version="1.0.0"
)

# Registrar SessionMiddleware para almacenar el historial de chat de forma segura en cookies
app.add_middleware(
    SessionMiddleware, 
    secret_key=settings.SECRET_KEY,
    session_cookie="it_tools_session",
    same_site="lax",
    https_only=False  # Permitir HTTP en la Intranet local (preparado para SSL offloading posterior)
)

# Configurar archivos estáticos (creamos el directorio si no existe)
import os
os.makedirs("app/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# Incluir routers
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(scraper.router)
app.include_router(chat.router)

# Ruta del Login (vista)
@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request, db: Session = Depends(get_db)):
    # Si el usuario ya está autenticado, redirigir al Dashboard
    user = get_current_user_optional(request, db)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        
    return templates.TemplateResponse("login.html", {"request": request})

# Ruta del Login (procesar)
@app.post("/login", response_class=HTMLResponse)
async def post_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    username = username.strip().lower()
    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", 
            {
                "request": request, 
                "error": "Usuario o contraseña incorrectos.",
                "username": username
            }
        )
        
    # Crear token de sesión
    access_token = create_session_token(
        data={"sub": user.username, "role": user.role}
    )
    
    # Redireccionar al Dashboard principal y asignar la cookie de sesión
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=COOKIE_NAME,
        value=access_token,
        httponly=True,
        max_age=settings.SESSION_EXPIRE_MINUTES * 60,
        expires=settings.SESSION_EXPIRE_MINUTES * 60,
        samesite="lax",
        secure=False  # False para HTTP local en intranet
    )
    return response

# Ruta del Logout
@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    # Limpiar el historial de chat guardado en sesión
    request.session.clear()
    return response

# Manejador global para redireccionar al login cuando expira o no hay sesión
@app.exception_handler(status.HTTP_307_TEMPORARY_REDIRECT)
@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def unauthorized_redirect_handler(request: Request, exc: Exception):
    if request.headers.get("HX-Request"):
        # Responder con header HTMX para forzar redirección en el navegador
        return HTMLResponse(
            content="",
            status_code=200,
            headers={"HX-Redirect": "/login"}
        )
    return RedirectResponse(url="/login")
