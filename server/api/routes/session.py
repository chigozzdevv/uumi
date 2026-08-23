from fastapi import APIRouter, Response, status

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
    response.delete_cookie("__session", path="/", secure=True, httponly=True, samesite="lax")
    return response
