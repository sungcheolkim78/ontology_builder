import os

import anydoc
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.chat import get_chat_model, get_model_name, to_langchain_messages
from app.parser import DATA_DIR, parse_to_markdown_file

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from FastAPI"}


@app.get("/api/config")
def get_config():
    return {"model": get_model_name()}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


@app.post("/api/chat")
def chat(request: ChatRequest):
    model = get_chat_model()
    lc_messages = to_langchain_messages([m.model_dump() for m in request.messages])
    response = model.invoke(lc_messages)
    return {"role": "assistant", "content": response.content}


@app.post("/api/parse")
async def parse(file: UploadFile = File(...)):
    data = await file.read()
    try:
        return parse_to_markdown_file(file.filename, data)
    except (anydoc.ConvertError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/files")
def list_files():
    if not DATA_DIR.is_dir():
        return {"files": []}
    paths = sorted(DATA_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return {"files": [{"filename": p.name} for p in paths if p.is_file()]}


@app.get("/api/files/{filename}", response_class=PlainTextResponse)
def get_file(filename: str):
    safe_path = DATA_DIR / os.path.basename(filename)
    if not safe_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return safe_path.read_text()
