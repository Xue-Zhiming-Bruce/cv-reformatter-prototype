from fastapi import FastAPI

app = FastAPI(title="CV Reformatter MVP")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
