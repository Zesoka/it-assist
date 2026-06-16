from datetime import datetime, timedelta
from typing import Optional
from fastapi import Request, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import jwt
import bcrypt

from app.config import settings
from app.database import get_db
from app.models import User

# Configuración del token JWT
ALGORITHM = "HS256"
COOKIE_NAME = "session_token"

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def create_session_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.SESSION_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Obtiene el usuario actual de forma opcional (sin lanzar excepción si no está autenticado)"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        user = db.query(User).filter(User.username == username, User.is_active == True).first()
        return user
    except jwt.PyJWTError:
        return None

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Obtiene el usuario actual y lanza una redirección a login si no existe"""
    user = get_current_user_optional(request, db)
    if not user:
        # Si la petición es de HTMX, podemos enviar un header especial para que redirija en el cliente
        if request.headers.get("HX-Request"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                headers={"HX-Redirect": "/login"}
            )
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"}
        )
    return user

def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """Verifica que el usuario actual sea de tipo administrador"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado. Se requieren privilegios de administrador."
        )
    return current_user
