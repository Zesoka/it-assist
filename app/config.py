import os
from dotenv import load_dotenv

# Cargar variables de entorno desde un archivo .env si existe
load_dotenv()

class Settings:
    # Clave secreta para firmar cookies de sesión JWT
    SECRET_KEY: str = os.getenv("SECRET_KEY", "super-secret-session-signing-key-change-in-prod")
    
    # URL de base de datos. Por defecto usa SQLite local para facilitar pruebas de desarrollo.
    # En producción se debe usar PostgreSQL: postgresql://username:password@localhost:5408/dbname
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "sqlite:///./it_tools.db"
    )
    
    # API Key para Google Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    
    # URLs de herramientas externas de Intranet
    PHPIPAM_URL: str = os.getenv("PHPIPAM_URL", "http://192.168.1.50/phpipam")
    SAFWEB_URL: str = os.getenv("SAFWEB_URL", "http://192.168.1.60/safweb")
    
    # Duración de la sesión (en minutos)
    SESSION_EXPIRE_MINUTES: int = int(os.getenv("SESSION_EXPIRE_MINUTES", "1440")) # 24 horas

settings = Settings()
