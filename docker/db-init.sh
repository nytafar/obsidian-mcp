#!/bin/bash
# Create obsidian_mcp database, user, and pgvector extension
set -e

if [ -f ".env" ]; then
    source .env
elif [ -f "docker/.env" ]; then
    source docker/.env
fi

# Extract password from DATABASE_URL if not set directly
if [ -z "$DB_PASSWORD" ]; then
    DB_PASSWORD=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
fi

if [ -z "$DB_PASSWORD" ]; then
    echo "Error: Could not determine database password from .env"
    exit 1
fi

echo "Checking obsidian_mcp database..."

DB_EXISTS=$(docker exec postgres psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='obsidian_mcp'" || echo "")

if [ "$DB_EXISTS" = "1" ]; then
    echo "Database 'obsidian_mcp' already exists"
else
    echo "Creating database and user..."
    docker exec postgres psql -U postgres -c "CREATE DATABASE obsidian_mcp;"
    docker exec postgres psql -U postgres -c "CREATE USER obsidian_mcp WITH PASSWORD '${DB_PASSWORD}';" 2>/dev/null || \
        docker exec postgres psql -U postgres -c "ALTER USER obsidian_mcp WITH PASSWORD '${DB_PASSWORD}';"
    docker exec postgres psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE obsidian_mcp TO obsidian_mcp;"
    docker exec postgres psql -U postgres -d obsidian_mcp -c "GRANT ALL ON SCHEMA public TO obsidian_mcp;"
    echo "Database and user created"
fi

# Enable pgvector extension
echo "Enabling pgvector extension..."
docker exec postgres psql -U postgres -d obsidian_mcp -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "pgvector extension enabled"
else
    echo "WARNING: pgvector extension not available. Update postgres image to pgvector/pgvector:pg16"
    exit 1
fi

echo "Database initialization complete!"
