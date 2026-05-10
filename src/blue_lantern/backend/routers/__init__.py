from .auth import router as auth_router
from .pages import router as pages_router
from .api import router as api_router

__all__ = ["auth_router", "pages_router", "api_router"]
