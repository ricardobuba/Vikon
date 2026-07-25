-- Se ejecuta automáticamente al inicializar el volumen de Postgres por primera vez.
-- pgvector queda listo para el RAG científico (cap. 10), aunque no se use hasta fases posteriores.
CREATE EXTENSION IF NOT EXISTS vector;
