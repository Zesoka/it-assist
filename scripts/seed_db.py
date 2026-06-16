import os
import sys

# Agregar el directorio raíz al PATH para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine
from app.models import Base, User
from app.auth import get_password_hash

def seed():
    # Asegurar que las tablas existan
    print("Verificando y creando tablas de base de datos...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Verificar si ya existen usuarios
        admin_exists = db.query(User).filter(User.role == "admin").first()
        if admin_exists:
            print(f"La base de datos ya cuenta con un Administrador registrado: '{admin_exists.username}'")
            return
            
        print("Inicializando base de datos con un usuario administrador por defecto...")
        
        # Crear usuario administrador
        default_admin = User(
            username="admin",
            full_name="Administrador de Sistemas",
            password_hash=get_password_hash("admin123"),
            role="admin",
            is_active=True
        )
        
        # Crear usuario técnico de prueba
        default_tecnico = User(
            username="tecnico",
            full_name="Tecnico de Guardia",
            password_hash=get_password_hash("tecnico123"),
            role="tecnico",
            is_active=True
        )
        
        db.add(default_admin)
        db.add(default_tecnico)
        db.commit()
        
        print("-" * 50)
        print("¡Base de datos sembrada con éxito!")
        print("Credenciales por defecto creadas:")
        print("  1. Rol Administrador:")
        print("     - Usuario: admin")
        print("     - Contraseña: admin123")
        print("  2. Rol Técnico:")
        print("     - Usuario: tecnico")
        print("     - Contraseña: tecnico123")
        print("-" * 50)
        
    except Exception as e:
        print(f"Error al sembrar la base de datos: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
