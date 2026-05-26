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
