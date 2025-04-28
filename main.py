# main.py
from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import sessionmaker, Session, relationship, declarative_base  # Updated import
from contextlib import asynccontextmanager
from datetime import datetime
from passlib.context import CryptContext
import os
from typing import Optional
import uuid

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./social_media.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()  # Using the imported declarative_base

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Models
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_admin = Column(Boolean, default=False)
    posts = relationship("Post", back_populates="user")
    
    def verify_password(self, password):
        return pwd_context.verify(password, self.hashed_password)


class Post(Base):
    __tablename__ = "posts"
    
    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text)
    image_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    user = relationship("User", back_populates="posts")


# Create tables
Base.metadata.create_all(bind=engine)

# Lifespan context manager (replaces on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Code to run on startup
    create_superuser()
    yield
    # Code to run on shutdown
    pass

# Create superuser function
def create_superuser():
    db = SessionLocal()
    try:
        # Check if any user exists
        user_count = db.query(User).count()
        if user_count == 0:
            # Create admin user
            hashed_password = pwd_context.hash("admin")
            admin_user = User(
                username="admin",
                email="admin@example.com",
                hashed_password=hashed_password,
                is_admin=True
            )
            db.add(admin_user)
            db.commit()
            print("Admin user created: admin / admin")
    finally:
        db.close()

# FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Create upload directory if it doesn't exist
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    username = request.cookies.get("username")
    if not username:
        return None
    
    user = db.query(User).filter(User.username == username).first()
    return user


# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    return templates.TemplateResponse("index.html", {"request": request, "user": current_user})


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
async def register_user(
    username: str = Form(...),
    password: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    # Check if username or email already exists
    existing_user = db.query(User).filter(
        (User.username == username) | (User.email == email)
    ).first()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Username or email already registered")
    
    # Create new user
    hashed_password = pwd_context.hash(password)
    is_admin = False
    
    # Make the first user an admin
    if db.query(User).count() == 0:
        is_admin = True
    
    new_user = User(
        username=username,
        email=email,
        hashed_password=hashed_password,
        is_admin=is_admin
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    return response


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username).first()
    
    if not user or not user.verify_password(password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(key="username", value=user.username)
    
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="username")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    return templates.TemplateResponse(
        "dashboard.html", 
        {"request": request, "user": current_user}
    )


@app.post("/post")
async def create_post(
    request: Request,
    content: str = Form(...),
    image: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    
    image_path = None
    if image and image.filename:
        # Save image
        file_extension = os.path.splitext(image.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        image_path = f"{UPLOAD_DIR}/{unique_filename}"
        
        with open(image_path, "wb") as file:
            file.write(await image.read())
        
        # Convert to relative path for storage
        image_path = f"uploads/{unique_filename}"
    
    new_post = Post(
        content=content,
        image_path=image_path,
        user_id=current_user.id
    )
    
    db.add(new_post)
    db.commit()
    
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/profile/{username}", response_class=HTMLResponse)
async def user_profile(
    request: Request, 
    username: str, 
    current_user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    profile_user = db.query(User).filter(User.username == username).first()
    
    if not profile_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    posts = db.query(Post).filter(Post.user_id == profile_user.id).order_by(Post.created_at.desc()).all()
    
    return templates.TemplateResponse(
        "profile.html", 
        {
            "request": request, 
            "profile_user": profile_user, 
            "posts": posts, 
            "user": current_user
        }
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request, 
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user or not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    users = db.query(User).all()
    posts = db.query(Post).order_by(Post.created_at.desc()).all()
    
    return templates.TemplateResponse(
        "admin.html", 
        {
            "request": request, 
            "users": users, 
            "posts": posts, 
            "user": current_user
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)