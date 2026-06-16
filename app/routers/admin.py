from fastapi import APIRouter, Request, Depends, HTTPException, Form, status
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.auth import get_current_admin, get_password_hash
from app.models import User

router = APIRouter(prefix="/admin", tags=["Admin User Management"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
async def get_admin_dashboard(
    request: Request, 
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Renderiza el panel CRUD de administración de usuarios"""
    users = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "admin_crud.html",
        {
            "request": request,
            "admin_user": current_admin,
            "users": users
        }
    )

@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Crea un nuevo usuario e inserta una fila en la tabla mediante HTMX"""
    # Validaciones rápidas
    username = username.strip().lower()
    full_name = full_name.strip()
    
    # Validar campos vacíos
    if not username or not full_name or not password:
        users = db.query(User).order_by(User.id.asc()).all()
        return templates.TemplateResponse(
            "partials/user_table_body.html",
            {"request": request, "users": users, "error": "Todos los campos son obligatorios."}
        )

    # Validar si el usuario ya existe
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        users = db.query(User).order_by(User.id.asc()).all()
        return templates.TemplateResponse(
            "partials/user_table_body.html",
            {"request": request, "users": users, "error": "El nombre de usuario ya está registrado."}
        )

    # Crear el usuario
    hashed_password = get_password_hash(password)
    new_user = User(
        username=username,
        full_name=full_name,
        password_hash=hashed_password,
        role=role,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Retornar la lista actualizada de usuarios
    users = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "partials/user_table_body.html",
        {"request": request, "users": users, "success": f"Usuario {new_user.username} creado correctamente."}
    )

@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def get_edit_form(
    request: Request,
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Retorna un fragmento de formulario de edición para el modal de HTMX"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    return templates.TemplateResponse(
        "partials/edit_user_modal.html",
        {"request": request, "user": user}
    )

@router.post("/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user(
    request: Request,
    user_id: int,
    full_name: str = Form(...),
    role: str = Form(...),
    password: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Actualiza la información del usuario y retorna el listado de usuarios actualizado"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    user.full_name = full_name.strip()
    user.role = role
    user.is_active = True if is_active == "on" or is_active == "true" else False
    
    # Evitar que el administrador se desactive a sí mismo o se quite el rol admin
    if user.id == current_admin.id:
        user.is_active = True
        user.role = "admin"
        
    if password and password.strip():
        user.password_hash = get_password_hash(password.strip())
        
    db.commit()
    
    users = db.query(User).order_by(User.id.asc()).all()
    return templates.TemplateResponse(
        "partials/user_table_body.html",
        {"request": request, "users": users, "success": f"Usuario {user.username} modificado correctamente."}
    )

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Elimina permanentemente a un usuario. Si es el mismo admin actual, arroja error"""
    if user_id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminar tu propia cuenta de administrador."
        )
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    db.delete(user)
    db.commit()
    
    # Retornamos un 200 OK con contenido vacío para que HTMX remueva el elemento de la lista
    return Response(status_code=status.HTTP_200_OK)
