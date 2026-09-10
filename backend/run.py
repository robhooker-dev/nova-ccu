"""Dev launcher: uvicorn, single port. Run with: python run.py"""
import uvicorn
from app import config

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT, reload=True)
