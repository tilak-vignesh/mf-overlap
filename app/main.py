from fastapi import FastAPI

from app.api import funds, portfolio

app = FastAPI(title="ai-shi", description="Mutual fund overlap and concentration-risk agent")

app.include_router(funds.router)
app.include_router(portfolio.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
