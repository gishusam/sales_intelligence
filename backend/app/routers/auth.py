from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import verify_password, create_access_token, hash_password, get_current_user, CurrentUser

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email:    str
    password: str


class LoginResponse(BaseModel):
    access_token:         str
    token_type:           str = "bearer"
    must_change_password: bool
    user:                 dict


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str


class ResetPasswordRequest(BaseModel):
    new_password: str


# ── Login ──────────────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT
            id,
            name,
            email,
            password_hash,
            role,
            must_change_password,
            can_manage_communication_templates
        FROM users
        WHERE email = :email AND is_active = TRUE
    """), {"email": body.email.lower().strip()}).fetchone()

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
        "can_manage_communication_templates": bool(
            row.can_manage_communication_templates
        ),
    })

    return {
        "access_token":         token,
        "token_type":           "bearer",
        "must_change_password": bool(row.must_change_password),
        "user": {
            "id":    row.id,
            "name":  row.name,
            "email": row.email,
            "role":  row.role,
            "can_manage_communication_templates": bool(
                row.can_manage_communication_templates
            ),
        }
    }


# ── Me ─────────────────────────────────────────────────────────────

@router.get("/me")
def get_me(
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    row = db.execute(text("""
        SELECT
            id,
            name,
            email,
            role,
            must_change_password,
            can_manage_communication_templates
        FROM users WHERE id = :id
    """), {"id": user.id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id":                   row.id,
        "name":                 row.name,
        "email":                row.email,
        "role":                 row.role,
        "must_change_password": bool(row.must_change_password),
        "can_manage_communication_templates": bool(
            row.can_manage_communication_templates
        ),
    }


# ── Change password ────────────────────────────────────────────────

@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    row = db.execute(text("""
        SELECT password_hash FROM users WHERE id = :id
    """), {"id": user.id}).fetchone()

    if not row or not verify_password(body.current_password, row.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters"
        )

    if body.new_password == body.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from current password"
        )

    db.execute(text("""
        UPDATE users SET
            password_hash        = :hash,
            must_change_password = FALSE,
            updated_at           = NOW()
        WHERE id = :id
    """), {"hash": hash_password(body.new_password), "id": user.id})
    db.commit()

    return {"message": "Password changed successfully"}


# ── Admin: list users ──────────────────────────────────────────────

@router.get("/users")
def list_users(
    user: CurrentUser = Depends(get_current_user),
    db:   Session     = Depends(get_db),
):
    if user.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Admin access required")

    rows = db.execute(text("""
        SELECT id, name, email, role, is_active, must_change_password
        FROM users ORDER BY created_at
    """)).fetchall()

    return [
        {
            "id":                   r.id,
            "name":                 r.name,
            "email":                r.email,
            "role":                 r.role,
            "is_active":            r.is_active,
            "must_change_password": r.must_change_password,
        }
        for r in rows
    ]


# ── Admin: reset a user's password ────────────────────────────────

@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    body:    ResetPasswordRequest,
    user:    CurrentUser = Depends(get_current_user),
    db:      Session     = Depends(get_db),
):
    if user.role not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Admin access required")

    db.execute(text("""
        UPDATE users SET
            password_hash        = :hash,
            must_change_password = TRUE,
            updated_at           = NOW()
        WHERE id = :id
    """), {"hash": hash_password(body.new_password), "id": user_id})
    db.commit()

    return {"message": "Password reset — user must change on next login"}