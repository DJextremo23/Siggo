-- Agregar columna para cachear resultados del análisis con IA
-- Así no se consume cuota de Gemini cada vez que se visita la página

ALTER TABLE informes
ADD COLUMN resultado_analisis JSON DEFAULT NULL
AFTER tamano_archivo;
