from fastapi import FastAPI
from app.bot import start_bot

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "ToughLove AI is running!"}

# Starta Telegram-boten automatiskt
@app.on_event("startup")
async def startup_event():
    await start_bot()
