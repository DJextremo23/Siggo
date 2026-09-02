-- =====================================================
-- Schema inicial de base de datos: Guardia OIG
-- Crear la base de datos antes de ejecutar este script
-- =====================================================

-- ── ROLES ──
CREATE TABLE IF NOT EXISTS roles (
    id_rol INT AUTO_INCREMENT PRIMARY KEY,
    nombre_rol VARCHAR(50) NOT NULL UNIQUE
);

INSERT IGNORE INTO roles (nombre_rol) VALUES ('admin'), ('fiscalizador');

-- ── USUARIOS ──
CREATE TABLE IF NOT EXISTS usuarios (
    id_usuario INT AUTO_INCREMENT PRIMARY KEY,
    nombre VARCHAR(100) NOT NULL,
    apellidos VARCHAR(100) NOT NULL,
    correo VARCHAR(150) NOT NULL UNIQUE,
    usuario VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    estado ENUM('activo', 'inactivo') DEFAULT 'activo',
    foto VARCHAR(255) DEFAULT NULL,
    fecha_ingreso DATE NOT NULL,
    totp_secret VARCHAR(64) DEFAULT NULL,
    dos_factores_activo BOOLEAN DEFAULT FALSE
);

-- ── USUARIOS ↔ ROLES ──
CREATE TABLE IF NOT EXISTS usuarios_roles (
    id_usuario INT NOT NULL,
    id_rol INT NOT NULL,
    PRIMARY KEY (id_usuario, id_rol),
    FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
    FOREIGN KEY (id_rol) REFERENCES roles(id_rol) ON DELETE CASCADE
);

-- ── FERIADOS ──
CREATE TABLE IF NOT EXISTS feriados (
    id_feriado INT AUTO_INCREMENT PRIMARY KEY,
    fecha DATE NOT NULL UNIQUE,
    descripcion TEXT NOT NULL
);

-- ── GUARDIAS ──
CREATE TABLE IF NOT EXISTS guardias (
    id_guardia INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario INT NOT NULL,
    fecha_guardia DATE NOT NULL,
    id_feriado INT DEFAULT NULL,
    estado VARCHAR(50) DEFAULT 'programada',
    UNIQUE KEY uq_guardia (id_usuario, fecha_guardia),
    FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
    FOREIGN KEY (id_feriado) REFERENCES feriados(id_feriado) ON DELETE SET NULL
);

-- ── ASISTENCIA ──
CREATE TABLE IF NOT EXISTS asistencia (
    id_asistencia INT AUTO_INCREMENT PRIMARY KEY,
    id_guardia INT NOT NULL UNIQUE,
    estado ENUM('asistio', 'falta', 'justificado') NOT NULL,
    FOREIGN KEY (id_guardia) REFERENCES guardias(id_guardia) ON DELETE CASCADE
);

-- ── COMPENSACIONES ──
CREATE TABLE IF NOT EXISTS compensaciones (
    id_compensacion INT AUTO_INCREMENT PRIMARY KEY,
    id_guardia INT NOT NULL UNIQUE,
    fecha_compensacion DATE NOT NULL,
    estado VARCHAR(50) DEFAULT 'pendiente',
    observacion TEXT DEFAULT NULL,
    FOREIGN KEY (id_guardia) REFERENCES guardias(id_guardia) ON DELETE CASCADE
);

-- ── VACACIONES ──
CREATE TABLE IF NOT EXISTS vacaciones (
    id_vacacion INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario INT NOT NULL,
    fecha_inicio DATE NOT NULL,
    fecha_fin DATE NOT NULL,
    FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario) ON DELETE CASCADE
);

-- ── INFORMES ──
CREATE TABLE IF NOT EXISTS informes (
    id_informe INT AUTO_INCREMENT PRIMARY KEY,
    id_guardia INT NOT NULL,
    id_usuario INT NOT NULL,
    titulo VARCHAR(255) NOT NULL,
    descripcion TEXT DEFAULT NULL,
    nombre_archivo VARCHAR(500) NOT NULL,
    ruta_archivo VARCHAR(500) NOT NULL,
    tipo_archivo VARCHAR(20) NOT NULL,
    extension VARCHAR(10) NOT NULL,
    tamano_archivo BIGINT NOT NULL,
    resultado_analisis JSON DEFAULT NULL,
    fecha_subida DATETIME DEFAULT CURRENT_TIMESTAMP,
    estado ENUM('activo', 'eliminado') DEFAULT 'activo',
    FOREIGN KEY (id_guardia) REFERENCES guardias(id_guardia) ON DELETE CASCADE,
    FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario) ON DELETE CASCADE
);

-- ── NOTIFICACIONES ──
CREATE TABLE IF NOT EXISTS notificaciones (
    id_notificacion INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario INT NOT NULL,
    titulo VARCHAR(255) NOT NULL,
    mensaje TEXT NOT NULL,
    fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
    leida BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario) ON DELETE CASCADE
);

-- ── INTENTOS DE LOGIN ──
CREATE TABLE IF NOT EXISTS intentos_login (
    id INT AUTO_INCREMENT PRIMARY KEY,
    identificador VARCHAR(255) NOT NULL,
    intento_en DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_identificador (identificador)
);

-- ── DISPOSITIVOS CONFIABLES (2FA recordar dispositivo) ──
CREATE TABLE IF NOT EXISTS dispositivos_confiables (
    id INT AUTO_INCREMENT PRIMARY KEY,
    id_usuario INT NOT NULL,
    token_hash VARCHAR(255) NOT NULL,
    dispositivo_info VARCHAR(500) DEFAULT NULL,
    creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
    ultimo_uso DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_usuario_token (id_usuario, token_hash),
    FOREIGN KEY (id_usuario) REFERENCES usuarios(id_usuario) ON DELETE CASCADE
);

-- ── VISTA: resumen_guardias ──
CREATE OR REPLACE VIEW resumen_guardias AS
SELECT
    g.id_guardia,
    g.id_usuario,
    g.fecha_guardia,
    CASE
        WHEN f.id_feriado IS NOT NULL THEN 'FERIADO'
        ELSE ELT(DAYOFWEEK(g.fecha_guardia),
            'Domingo', 'Lunes', 'Martes', 'Miércoles',
            'Jueves', 'Viernes', 'Sábado')
    END AS tipo_dia,
    COALESCE(a.estado, 'sin registro') AS asistencia
FROM guardias g
LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia;
