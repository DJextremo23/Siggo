-- =====================================================
-- Migración: eliminar el rol 'jefatura' del sistema
-- Ejecutar contra la base de datos de Guardia OIG
-- =====================================================

-- 1) Reasignar a 'fiscalizador' a los usuarios que SOLO tienen 'jefatura',
--    para que no queden sin rol y bloqueados al iniciar sesión.
INSERT INTO usuarios_roles (id_usuario, id_rol)
SELECT ur.id_usuario, (SELECT id_rol FROM roles WHERE nombre_rol = 'fiscalizador')
FROM usuarios_roles ur
INNER JOIN roles r ON r.id_rol = ur.id_rol
WHERE r.nombre_rol = 'jefatura'
  AND ur.id_usuario NOT IN (
      SELECT ur2.id_usuario
      FROM usuarios_roles ur2
      INNER JOIN roles r2 ON r2.id_rol = ur2.id_rol
      WHERE r2.nombre_rol IN ('admin', 'fiscalizador')
  );

-- 2) Eliminar el rol 'jefatura'.
--    La FK de usuarios_roles usa ON DELETE CASCADE, por lo que se limpian
--    automáticamente las asignaciones restantes.
DELETE FROM roles WHERE nombre_rol = 'jefatura';
