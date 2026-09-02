-- =====================================================
-- Migración: tabla dispositivos_confiables
-- Para recordar dispositivos en la verificación 2FA
-- =====================================================

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
