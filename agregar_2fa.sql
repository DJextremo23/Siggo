-- =====================================================
-- Migración: Agregar soporte de 2FA (TOTP)
-- Ejecutar contra la BD guardiaoig
-- =====================================================

ALTER TABLE usuarios
ADD COLUMN totp_secret VARCHAR(64) DEFAULT NULL,
ADD COLUMN dos_factores_activo BOOLEAN DEFAULT FALSE;
