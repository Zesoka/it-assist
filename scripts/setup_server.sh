#!/bin/bash
# ==============================================================================
# Script de Configuración del Servidor (Ubuntu 24.04 LTS - Contenedor LXC Proxmox)
# Proyecto: Sistema Corporativo de Herramientas IT - Sector Salud
# ==============================================================================

# Detener el script si ocurre un error
set -e

# Asegurar que el script se ejecuta como root
if [ "$EUID" -ne 0 ]; then
  echo "[-] Por favor, ejecute este script como root o usando sudo."
  exit 1
fi

echo "[+] Iniciando configuración del servidor..."
echo "[+] Directorio actual: $(pwd)"

# Variables de configuración
APP_DIR="/opt/herramientas-it"
DB_NAME="herramientas_it"
DB_USER="it_user"

# Cargar configuraciones existentes si existen para evitar romper la contraseña de la BD
if [ -f "$APP_DIR/.env" ]; then
  echo "[+] .env existente detectado. Cargando parámetros..."
  DB_PASS=$(grep '^DATABASE_URL=' "$APP_DIR/.env" | sed -E 's|DATABASE_URL=postgresql://[^:]+:([^@]+)@.*|\1|' || echo "")
  SECRET_KEY=$(grep '^SECRET_KEY=' "$APP_DIR/.env" | cut -d'=' -f2- || echo "")
fi

# Si no se encontraron, generar nuevos
if [ -z "$DB_PASS" ]; then
  DB_PASS=$(openssl rand -hex 16)
fi
if [ -z "$SECRET_KEY" ]; then
  SECRET_KEY=$(openssl rand -hex 32)
fi

echo "--------------------------------------------------"
echo "Configuración cargada/generada:"
echo " - Directorio de Instalación: $APP_DIR"
echo " - Base de Datos: $DB_NAME"
echo " - Usuario DB: $DB_USER"
echo " - Contraseña DB: $DB_PASS"
echo "--------------------------------------------------"

# 1. Actualización de paquetes de Ubuntu 24.04 LTS
echo "[+] 1. Actualizando paquetes del sistema..."
apt update && apt upgrade -y

# 2. Instalación de dependencias (PostgreSQL 16, Python 3.12, venv, git, ufw, g++ para compilaciones si fuesen necesarias)
echo "[+] 2. Instalando dependencias del sistema (PostgreSQL 16, Python 3.12, UFW)..."
apt install -y python3-pip python3-venv postgresql postgresql-contrib git ufw curl build-essential libpq-dev

# 3. Inicializar y Configurar PostgreSQL
echo "[+] 3. Configurando PostgreSQL..."
systemctl start postgresql
systemctl enable postgresql

# Crear base de datos y usuario de red
echo "[+] Creando base de datos y usuario en PostgreSQL..."
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" || echo "[!] La base de datos ya existe."
sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';" || echo "[!] El usuario de base de datos ya existe."
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"

# En PostgreSQL 15+, es obligatorio conceder permisos en el esquema 'public' para el usuario
sudo -u postgres psql -d $DB_NAME -c "GRANT ALL ON SCHEMA public TO $DB_USER;"

# 4. Crear estructura del proyecto e inicializar entorno virtual
echo "[+] 4. Creando directorio de la aplicación..."
mkdir -p "$APP_DIR"
cp -r app requirements.txt scripts systemd "$APP_DIR/" || echo "[!] Copiando archivos del directorio actual..."

cd "$APP_DIR"

echo "[+] Inicializando entorno virtual de Python (venv)..."
python3 -m venv venv
source venv/bin/activate

echo "[+] Instalando librerías de Python (requirements.txt)..."
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# 5. Configurar archivo .env
echo "[+] 5. Creando archivo de variables de entorno (.env)..."
if [ -f .env ]; then
  echo "[+] El archivo .env ya existe. No se sobrescribirá para preservar configuraciones y claves existentes."
else
  cat <<EOF > .env
DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost/$DB_NAME
SECRET_KEY=$SECRET_KEY
SESSION_EXPIRE_MINUTES=1440
# Reemplace esta clave vacía con su API Key de Google Gemini en producción
GEMINI_API_KEY=
EOF
  chmod 600 .env
  echo "[+] Archivo .env configurado correctamente en $APP_DIR/.env"
fi

# 6. Sembrar la base de datos (crear tablas y usuarios por defecto)
echo "[+] 6. Inicializando base de datos y cargando semilla de usuarios..."
venv/bin/python scripts/seed_db.py

# 7. Configuración de UFW (Firewall)
echo "[+] 7. Configurando políticas del Firewall UFW..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# Permitir SSH interno (Puerto 22)
ufw allow 22/tcp comment 'SSH Acceso Interno'

# Permitir Puerto Web de la Aplicación (Puerto 8000)
ufw allow 8000/tcp comment 'Web Panel Soporte IT (Gunicorn)'

# Habilitar el Firewall
ufw --force enable
echo "[+] Estado del Firewall:"
ufw status verbose

# 8. Registrar e Inicializar el Servicio Systemd
echo "[+] 8. Registrando el servicio Systemd herramientas-it.service..."
cp systemd/herramientas-it.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable herramientas-it.service
systemctl start herramientas-it.service

echo "--------------------------------------------------"
echo "[-] ¡Configuración completada con éxito!"
echo " - La aplicación está corriendo y configurada para iniciar automáticamente."
echo " - Accede desde la Intranet a través del puerto 8000 de este contenedor."
echo " - Por favor, edita '$APP_DIR/.env' para agregar tu 'GEMINI_API_KEY' si usarás el Chat de IA."
echo " - Usuario Administrador por defecto: admin / Contraseña: admin123"
echo " - Usuario Técnico por defecto: tecnico / Contraseña: tecnico123"
echo "--------------------------------------------------"

systemctl status herramientas-it.service --no-pager
