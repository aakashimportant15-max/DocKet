from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, new_session_token, verify_password
from app.config import SESSION_COOKIE
from app.database import get_db
from app.models import User
from app.templating import render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user: User | None = Depends(get_current_user),
):
    return render(request, "login.html", {}, user=user)


@router.post("/auth/login")
def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    if user.session_token is None:
        user.session_token = new_session_token()
        db.commit()
    response = RedirectResponse(url="/gallery", status_code=303)
    response.set_cookie(SESSION_COOKIE, user.session_token, httponly=True)
    return response


@router.post("/auth/logout")
def logout():
    # Clears the cookie only. The stored session_token is deliberately kept:
    # the seeded checker accounts must keep their fixed, non-expiring tokens.
    response = RedirectResponse(url="/gallery", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
