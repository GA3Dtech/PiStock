#!/bin/bash

# Stop the script immediately if any command fails
set -e

echo "=================================================="
echo "🛠️  Initializing the PiStock environment..."
echo "=================================================="

# 1. Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is not installed on this system."
    exit 1
fi

# 2. Clean up old virtual environment if it exists to start fresh
if [ -d ".venv" ]; then
    echo "🧹 Cleaning up old virtual environment..."
    rm -rf .venv
fi

# 3. Create the virtual environment
echo "📦 Creating virtual environment (.venv)..."
python3 -m venv .venv

# 4. Activate the virtual environment for the rest of the script
echo "🔌 Activating environment..."
source .venv/bin/activate

# 5. Upgrade pip
echo "🚀 Upgrading pip..."
pip install --upgrade pip

# 6. Install the core dependencies for PiStock
echo "📥 Installing FastAPI, SQLModel, and Uvicorn..."
pip install fastapi sqlmodel uvicorn python-multipart

# 7. Create a minimalist main.py file for immediate testing
if [ ! -f "main.py" ]; then
    echo "📝 Creating test main.py file..."
    cat << 'EOF' > main.py
from fastapi import FastAPI
from sqlmodel import SQLModel, Field, create_engine

app = FastAPI(title="PiStock MVP API")

# A ultra-simple test model
class Part(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    status: str

@app.get("/")
def read_root():
    return {"message": "PiStock Server is up and running!"}
EOF
fi

echo "=================================================="
echo "✅ Setup completed successfully!"
echo "=================================================="
echo "To start your environment:"
echo "  1. Activate it: source .venv/bin/activate"
echo "  2. Run the server: uvicorn main:app --reload"
echo "=================================================="
