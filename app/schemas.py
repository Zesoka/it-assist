from pydantic import BaseModel, Field
from typing import Optional
import datetime

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    full_name: str = Field(..., min_length=3, max_length=100)
    role: str = Field(..., pattern="^(admin|tecnico)$")

class UserCreate(UserBase):
    password: str = Field(..., min_length=4)

class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=3, max_length=100)
    role: Optional[str] = Field(None, pattern="^(admin|tecnico)$")
    password: Optional[str] = Field(None, min_length=4)
    is_active: Optional[bool] = None

class UserResponse(UserBase):
    id: int
    is_active: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True
        
class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None
