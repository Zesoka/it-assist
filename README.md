# Portal de Soporte e Infraestructura IT

Este es un portal web corporativo de uso interno diseñado para equipos de soporte técnico, administración de redes e infraestructura de IT en el sector salud. La aplicación está optimizada para ser alojada en un contenedor LXC (Ubuntu 24.04 LTS) sobre un hipervisor Proxmox VE, accesible exclusivamente a través de la Intranet corporativa.

---

## 🚀 Stack Tecnológico

- **Backend**: FastAPI (Python 3.12/3.14) con SQLAlchemy ORM.
- **Base de Datos**: PostgreSQL 16 (Producción) / SQLite (Desarrollo).
- **Frontend**: HTML5 renderizado con Jinja2, estilizado con Tailwind CSS (CDN) y dotado de reactividad asíncrona mediante HTMX (sin recargas de página).
- **Autenticación**: Sesión segura gestionada mediante cookies firmadas digitalmente (`SessionMiddleware` con tokens JWT cifrados usando `bcrypt`).
- **Servidor de Producción**: Gunicorn administrando workers asíncronos de Uvicorn (`UvicornWorker`).
- **Integraciones**: 
  - Google Gemini API (`google-generativeai` SDK) para el agente de soporte IT.
  - Open-Meteo API para el Widget del Clima local.

---

## 🛠️ Características Principales

1. **Gestión de Usuarios y Roles**:
   - Acceso seguro mediante pantalla de Login corporativo.
   - Roles definidos: **Admin** (Administrador) y **Técnico** (Personal de Guardia).
   - Panel CRUD dinámico para Administradores que permite crear, editar y eliminar cuentas de técnicos en caliente usando HTMX.
2. **Dashboard Centralizado**:
   - Saludo personalizado con fecha y hora del sistema.
   - Widget meteorológico en tiempo real (con caché de 15 minutos en servidor) configurado para **Ingeniero Budge, Buenos Aires**.
   - Enlaces rápidos de Intranet a herramientas de red externas (`phpIPAM` y `SAF Web`) configurables dinámicamente desde variables de entorno.
3. **Módulo 1: Extractor de Transcripciones de YouTube**:
   - Permite descargar subtítulos de videos formativos o conferencias técnicas de YouTube.
   - Generación asíncrona en servidor de reportes en formatos Markdown (`.md`), Microsoft Word (`.docx`) y PDF (`.pdf`), con soporte para acentos españoles.
4. **Módulo 2: Agente de IA para Soporte IT**:
   - Chat interactivo conectado con Google Gemini (modelo `gemini-2.5-flash`).
   - Instrucción del sistema estricta: Asistir a técnicos brindando comandos y diagnósticos sobre clústeres de **Proxmox**, firewalls **FortiGate**, switches **Aruba**, redes **UniFi** y servidores **Linux**.
   - Historial de chat persistente en memoria por sesión de usuario.

---

## 📂 Estructura del Proyecto

```text
herramientas-it/
├── app/
│   ├── main.py                # Punto de entrada y middleware de FastAPI
│   ├── config.py              # Gestión de variables de entorno y configs
│   ├── database.py            # Configuración de base de datos SQLAlchemy
│   ├── models.py              # Definición de tablas de base de datos
│   ├── schemas.py             # Validaciones Pydantic para APIs
│   ├── auth.py                # Hashing de contraseñas y validación de tokens
│   ├── routers/               # Enrutadores modulares de FastAPI
│   │   ├── admin.py           # CRUD de administración de usuarios
│   │   ├── chat.py            # Módulo del chat conversacional de IA
│   │   ├── dashboard.py       # Panel principal y widget del clima
│   │   └── scraper.py         # Descargas y transcripciones de YouTube
│   └── templates/             # Vistas de Jinja2 HTML
│       ├── base.html          # Layout y barra lateral claros
│       ├── login.html         # Formulario de login
│       ├── dashboard.html     # Dashboard principal
│       ├── admin_crud.html    # Gestión de usuarios
│       ├── scraper.html       # Interfaz del scraper
│       ├── chat.html          # Interfaz de conversación de IA
│       └── partials/          # Fragmentos de HTML para swaps de HTMX
├── scripts/
│   ├── seed_db.py             # Script para poblar base de datos inicial
│   └── setup_server.sh        # Script Bash de auto-instalación en Ubuntu 24.04
├── systemd/
│   └── herramientas-it.service # Archivo de unidad Systemd para Linux
├── requirements.txt           # Dependencias de Python
└── .env                       # Variables de configuración del sistema (excluido en git)
```

---

## 💻 Desarrollo Local (Pruebas en Windows)

1. **Clonar e ingresar al directorio**:
   ```powershell
   git clone <url_de_tu_repositorio>
   cd herramientas-it
   ```
2. **Crear y activar el entorno virtual de Python**:
   ```powershell
   python -m venv venv
   Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process # En PowerShell si da error de ejecución
   .\venv\Scripts\Activate
   ```
3. **Instalar dependencias**:
   ```powershell
   pip install -r requirements.txt
   ```
4. **Sembrar base de datos local (SQLite)**:
   ```powershell
   python scripts/seed_db.py
   ```
   *Esto creará la base de datos `it_tools.db` con dos cuentas por defecto:*
   - **Administrador**: `admin` / `admin123`
   - **Técnico**: `tecnico` / `tecnico123`
5. **Configurar la API Key de Gemini**:
   Crea o edita el archivo `.env` en la raíz del proyecto e ingresa tu API Key para poder usar el chat de IA:
   ```env
   GEMINI_API_KEY=tu_api_key_de_gemini
   ```
6. **Lanzar servidor local**:
   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```
   *Acceso en:* [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 🎛️ Despliegue en Producción (Contenedor LXC Ubuntu 24.04 LTS)

El proyecto cuenta con un script de configuración automatizado para agilizar el despliegue a producción.

1. **Clonar el repositorio** dentro del contenedor LXC en la ruta `/opt`:
   ```bash
   sudo git clone <url_de_tu_repositorio> /opt/herramientas-it
   cd /opt/herramientas-it
   ```
2. **Dar permisos de ejecución y correr el instalador**:
   ```bash
   chmod +x scripts/setup_server.sh
   sudo ./scripts/setup_server.sh
   ```
   *El instalador se encargará de:*
   - Instalar y habilitar **PostgreSQL 16**.
   - Crear el usuario `it_user` y la base de datos `herramientas_it` con credenciales seguras.
   - Configurar el entorno virtual e instalar las librerías de Python.
   - Generar un archivo `.env` de producción con claves de sesión JWT aleatorias.
   - Levantar las tablas e insertar los usuarios iniciales.
   - Configurar el Firewall **UFW** (abriendo únicamente puertos SSH `22` y Web Gunicorn `8000`).
   - Crear, registrar y arrancar el servicio **Systemd** `herramientas-it.service`.
3. **Cargar la API Key de Gemini en producción**:
   Abre el archivo `.env` generado en el servidor:
   ```bash
   sudo nano /opt/herramientas-it/.env
   ```
   Agrega tu clave:
   ```env
   GEMINI_API_KEY=tu_api_key_de_gemini
   ```
   Guarda (`Ctrl+O`, `Enter`) y sal (`Ctrl+X`).
4. **Reiniciar para aplicar**:
   ```bash
   sudo systemctl restart herramientas-it
   ```
   *La aplicación ahora estará escuchando en el puerto `8000` del contenedor para toda la Intranet local.*
