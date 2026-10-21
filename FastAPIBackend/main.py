import uvicorn

from app.api.app_factory import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=2889, reload=True)
