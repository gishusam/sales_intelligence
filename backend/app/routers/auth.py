from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_password, create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT id, name, email, password_hash, role
        FROM users
        WHERE email = :email AND is_active = TRUE
    """), {"email": body.email}).fetchone()

   
    if row:
        print("PASSWORD LENGTH:", len(body.password))
        print("HASH LENGTH:", len(row.password_hash))
        print("HASH PREFIX:", row.password_hash[:10])


    if not row or not verify_password(body.password, row.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )

    token = create_access_token({
        "user_id": row.id,
        "name":    row.name,
        "email":   row.email,
        "role":    row.role,
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id":    row.id,
            "name":  row.name,
            "email": row.email,
            "role":  row.role,
        }
    }


@router.get("/me")
def get_me(db: Session = Depends(get_db)):
    """Optional: verify token and return current user info."""
    from app.auth import get_current_user
    pass
