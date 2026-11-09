"""Router registry. Order matters: state handlers run before the fallback."""

from handlers.accounts import router as accounts_router
from handlers.admin import router as admin_router
from handlers.common import router as common_router
from handlers.menu import fallback_router, menu_router
from handlers.search import router as search_router
from handlers.seller import router as seller_router
from handlers.start import router as start_router
from handlers.wallet import router as wallet_router

routers = [
    start_router,
    common_router,
    menu_router,
    seller_router,
    accounts_router,
    search_router,
    wallet_router,
    admin_router,
    fallback_router,
]

__all__ = ["routers"]
