-- =====================================================
-- Migración: cambio de contraseña forzado tras bloqueo de cuenta
-- Ejecutar contra la base de datos de Guardia OIG
-- =====================================================
-- Agrega la bandera que obliga al usuario a renovar su contraseña
-- en el primer inicio de sesión después de que un administrador
-- desbloquee su cuenta (o tras el bloqueo duro por fuerza bruta).

ALTER TABLE usuarios
ADD COLUMN debe_cambiar_password TINYINT(1) DEFAULT 0
AFTER bloqueos_consecutivos;
