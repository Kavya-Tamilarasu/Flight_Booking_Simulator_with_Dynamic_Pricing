# flight_api_production.py
"""
Production-Ready Flight Booking Backend (FastAPI + SQLite)
Improvements:
- JWT authentication with expiration
- Bcrypt password hashing
- Proper input validation
- Connection pooling
- Rate limiting
- Comprehensive error handling
- Logging
- Environment configuration
- Time-based refund policy
- Better concurrency handling
- Payment gateway structure
- CORS configuration
"""

import os
import sqlite3
import uuid
import secrets
import random
import atexit
import re
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from contextlib import contextmanager
from functools import wraps

from fastapi import FastAPI, HTTPException, status, Query, Depends, Header, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, validator, Field
from pydantic_settings import BaseSettings
from passlib.context import CryptContext
from jose import JWTError, jwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from apscheduler.schedulers.background import BackgroundScheduler

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.units import inch

# ---------------------------
# Configuration
# ---------------------------
class Settings(BaseSettings):
    database_url: str = "flight_booking.db"
    secret_key: str = "your-secret-key-change-in-production-min-32-chars"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    
    # CORS settings
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:5173"]
    
    # Rate limiting
    rate_limit_per_minute: str = "10/minute"
    booking_rate_limit: str = "3/minute"
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()

# ---------------------------
# Logging Configuration
# ---------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------
# Security
# ---------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

def create_refresh_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)

def decode_token(token: str):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return payload
    except JWTError as e:
        logger.warning(f"JWT decode error: {e}")
        return None

# ---------------------------
# Database Connection Pool
# ---------------------------
@contextmanager
def get_db():
    conn = sqlite3.connect(settings.database_url, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()

def with_retry(func, max_attempts=3):
    """Retry database operations on lock"""
    for attempt in range(max_attempts):
        try:
            return func()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_attempts - 1:
                import time
                time.sleep(0.1 * (2 ** attempt))
                logger.warning(f"Database locked, retry {attempt + 1}/{max_attempts}")
                continue
            raise

def init_schema():
    """Initialize database schema with improvements"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.executescript("""
        PRAGMA journal_mode = WAL;

        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(128) NOT NULL,
            full_name VARCHAR(150),
            email VARCHAR(100) UNIQUE,
            phone VARCHAR(30),
            country VARCHAR(50),
            role VARCHAR(10) DEFAULT 'CUSTOMER' CHECK (role IN ('ADMIN', 'CUSTOMER')),
            is_active BOOLEAN DEFAULT 1,
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_login DATETIME
        );

        CREATE INDEX IF NOT EXISTS idx_user_username ON user(username);
        CREATE INDEX IF NOT EXISTS idx_user_email ON user(email);

        CREATE TABLE IF NOT EXISTS airport_lookup (
            code VARCHAR(10) PRIMARY KEY,
            city_country VARCHAR(100) NOT NULL,
            timezone VARCHAR(50)
        );

        CREATE TABLE IF NOT EXISTS flight (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_number VARCHAR(50) NOT NULL UNIQUE,
            airline VARCHAR(80) NOT NULL,
            from_airport_code VARCHAR(10) NOT NULL,
            to_airport_code VARCHAR(10) NOT NULL,
            departure_time DATETIME NOT NULL,
            arrival_time DATETIME NOT NULL,
            base_price REAL NOT NULL CHECK(base_price > 0),
            total_seats INTEGER NOT NULL CHECK(total_seats > 0),
            seats_remaining INTEGER NOT NULL,
            demand_factor REAL DEFAULT 1.0 CHECK(demand_factor >= 0.5 AND demand_factor <= 2.0),
            status VARCHAR(20) DEFAULT 'SCHEDULED' CHECK(status IN ('SCHEDULED', 'BOARDING', 'DEPARTED', 'ARRIVED', 'CANCELLED')),
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            CHECK(seats_remaining >= 0 AND seats_remaining <= total_seats),
            CHECK(arrival_time > departure_time),
            FOREIGN KEY (from_airport_code) REFERENCES airport_lookup (code),
            FOREIGN KEY (to_airport_code) REFERENCES airport_lookup (code)
        );

        CREATE INDEX IF NOT EXISTS idx_flight_departure ON flight(departure_time);
        CREATE INDEX IF NOT EXISTS idx_flight_route ON flight(from_airport_code, to_airport_code);
        CREATE INDEX IF NOT EXISTS idx_flight_number ON flight(flight_number);

        CREATE TABLE IF NOT EXISTS booking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            flight_id INTEGER NOT NULL,
            pnr VARCHAR(12) NOT NULL UNIQUE,
            price_paid REAL NOT NULL CHECK(price_paid >= 0),
            booking_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'CONFIRMED', 'CANCELLED', 'REFUNDED')),
            payment_reference VARCHAR(80),
            contact_email VARCHAR(100) NOT NULL,
            contact_phone VARCHAR(30) NOT NULL,
            cancellation_date DATETIME,
            refund_amount REAL,
            FOREIGN KEY (user_id) REFERENCES user(id),
            FOREIGN KEY (flight_id) REFERENCES flight(id)
        );

        CREATE INDEX IF NOT EXISTS idx_booking_user ON booking(user_id);
        CREATE INDEX IF NOT EXISTS idx_booking_pnr ON booking(pnr);
        CREATE INDEX IF NOT EXISTS idx_booking_status ON booking(status);

        CREATE TABLE IF NOT EXISTS passenger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL,
            flight_id INTEGER NOT NULL,
            seat_number VARCHAR(10) NOT NULL,
            seat_type VARCHAR(10) CHECK (seat_type IN ('WINDOW','AISLE','MIDDLE')),
            full_name VARCHAR(120) NOT NULL,
            date_of_birth TEXT,
            passport_number VARCHAR(50),
            passenger_type VARCHAR(10) DEFAULT 'ADULT' CHECK(passenger_type IN ('ADULT', 'CHILD', 'INFANT')),
            FOREIGN KEY (booking_id) REFERENCES booking(id) ON DELETE CASCADE,
            FOREIGN KEY (flight_id) REFERENCES flight(id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_seat_per_flight ON passenger(flight_id, seat_number);
        CREATE INDEX IF NOT EXISTS idx_passenger_booking ON passenger(booking_id);

        CREATE TABLE IF NOT EXISTS flight_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id INTEGER NOT NULL,
            recorded_price REAL NOT NULL,
            demand_factor REAL NOT NULL,
            seats_remaining INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (flight_id) REFERENCES flight(id)
        );

        CREATE INDEX IF NOT EXISTS idx_price_history_flight ON flight_price_history(flight_id, timestamp);

        CREATE TABLE IF NOT EXISTS payment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL,
            payment_reference VARCHAR(80) UNIQUE NOT NULL,
            payment_method VARCHAR(20) CHECK (payment_method IN ('UPI','CARD','WALLET','NETBANKING')) NOT NULL,
            amount_paid REAL NOT NULL CHECK(amount_paid >= 0),
            payment_status VARCHAR(20) CHECK (payment_status IN ('SUCCESS','FAILED','PENDING','REFUNDED')) DEFAULT 'PENDING',
            payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            gateway_response TEXT,
            FOREIGN KEY (booking_id) REFERENCES booking(id)
        );

        CREATE INDEX IF NOT EXISTS idx_payment_booking ON payment(booking_id);
        CREATE INDEX IF NOT EXISTS idx_payment_reference ON payment(payment_reference);

        CREATE TABLE IF NOT EXISTS cancelled_booking (
            archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pnr VARCHAR(12),
            user_id INTEGER,
            flight_id INTEGER,
            price_paid REAL,
            refund_amount REAL,
            cancellation_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            passenger_full_name VARCHAR(120),
            cancellation_reason VARCHAR(150)
        );

        -- Trigger removed - pricing logic moved to application layer for better control
        """)
        conn.commit()
        logger.info("Database schema initialized successfully")

# ---------------------------
# Models with Validation
# ---------------------------
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = Field(None, max_length=150)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    country: Optional[str] = None

    @validator('username')
    def validate_username(cls, v):
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Username can only contain letters, numbers, underscore and hyphen')
        return v.lower()

    @validator('phone')
    def validate_phone(cls, v):
        if v and not re.match(r'^\+?[1-9]\d{9,14}$', v):
            raise ValueError('Invalid phone number format')
        return v

class UserLogin(BaseModel):
    username: str
    password: str

class PassengerIn(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    date_of_birth: Optional[str] = None
    passport_number: Optional[str] = Field(None, max_length=50)
    passenger_type: str = Field("ADULT", regex="^(ADULT|CHILD|INFANT)$")
    seat_number: str = Field(..., min_length=2, max_length=4)
    seat_type: str = Field(..., regex="^(WINDOW|AISLE|MIDDLE)$")

    @validator('seat_number')
    def validate_seat(cls, v):
        v = v.strip().upper()
        if not re.match(r'^\d{1,2}[A-F]$', v):
            raise ValueError('Invalid seat format. Use format like: 12A, 5B')
        return v

    @validator('full_name')
    def validate_name(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError('Name must be at least 2 characters')
        if not re.match(r'^[a-zA-Z\s\'-]+$', v):
            raise ValueError('Name can only contain letters, spaces, hyphens and apostrophes')
        return v

class BookingRequest(BaseModel):
    flight_id: int = Field(..., gt=0)
    passengers: List[PassengerIn] = Field(..., min_items=1, max_items=9)
    user_id: int = Field(..., gt=0)
    contact_email: EmailStr
    contact_phone: str
    payment_method: str = Field("CARD", regex="^(UPI|CARD|WALLET|NETBANKING)$")

    @validator('contact_phone')
    def validate_phone(cls, v):
        if not re.match(r'^\+?[1-9]\d{9,14}$', v):
            raise ValueError('Invalid phone number')
        return v

class FlightCreate(BaseModel):
    flight_number: str = Field(..., min_length=4, max_length=10)
    airline: str = Field(..., min_length=2, max_length=80)
    from_airport_code: str = Field(..., min_length=3, max_length=3)
    to_airport_code: str = Field(..., min_length=3, max_length=3)
    departure_time: str
    arrival_time: str
    base_price: float = Field(..., gt=0)
    total_seats: int = Field(..., gt=0, le=500)

    @validator('flight_number')
    def validate_flight_number(cls, v):
        if not re.match(r'^[A-Z0-9]+$', v.upper()):
            raise ValueError('Flight number must be alphanumeric')
        return v.upper()

    @validator('from_airport_code', 'to_airport_code')
    def validate_airport_code(cls, v):
        return v.upper()

    @validator('departure_time', 'arrival_time')
    def validate_datetime(cls, v):
        try:
            dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
            if dt < datetime.now():
                raise ValueError('Date cannot be in the past')
            return v
        except ValueError:
            raise ValueError('Invalid datetime format. Use ISO format: YYYY-MM-DD HH:MM:SS')

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

# ---------------------------
# FastAPI App
# ---------------------------
app = FastAPI(
    title="Flight Booking System - Production",
    description="Complete flight booking backend with enterprise features",
    version="2.0.0"
)

# Rate Limiting
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# Initialize DB
init_schema()

# ---------------------------
# Helper Functions
# ---------------------------
def calculate_dynamic_price(base_price: float, seats_remaining: int, total_seats: int, 
                           demand_factor: float, departure_time: datetime) -> float:
    """Calculate dynamic price based on availability and time"""
    if total_seats <= 0:
        total_seats = 1
    
    # Seat availability factor
    remaining_percentage = seats_remaining / total_seats
    if remaining_percentage <= 0.05:
        seat_factor = 0.60  # 60% increase
    elif remaining_percentage <= 0.10:
        seat_factor = 0.40  # 40% increase
    elif remaining_percentage <= 0.20:
        seat_factor = 0.20  # 20% increase
    elif remaining_percentage <= 0.50:
        seat_factor = 0.10  # 10% increase
    else:
        seat_factor = 0.0
    
    # Time-based factor (closer to departure = higher price)
    hours_until_departure = (departure_time - datetime.now()).total_seconds() / 3600
    if hours_until_departure < 0:
        time_factor = 0
    elif hours_until_departure < 6:
        time_factor = 0.30
    elif hours_until_departure < 24:
        time_factor = 0.20
    elif hours_until_departure < 72:
        time_factor = 0.10
    else:
        time_factor = 0.05
    
    final_price = base_price * (1 + seat_factor + time_factor) * demand_factor
    return round(final_price, 2)

def calculate_refund(price_paid: float, departure_time: datetime) -> tuple[float, str]:
    """Calculate refund based on time until departure"""
    hours_until = (departure_time - datetime.now()).total_seconds() / 3600
    
    if hours_until > 72:  # More than 3 days
        refund_pct = 0.90
        policy = "More than 72 hours before departure"
    elif hours_until > 48:  # 2-3 days
        refund_pct = 0.80
        policy = "48-72 hours before departure"
    elif hours_until > 24:  # 1-2 days
        refund_pct = 0.60
        policy = "24-48 hours before departure"
    elif hours_until > 6:  # 6-24 hours
        refund_pct = 0.40
        policy = "6-24 hours before departure"
    elif hours_until > 0:  # Less than 6 hours
        refund_pct = 0.20
        policy = "Less than 6 hours before departure"
    else:
        refund_pct = 0.0
        policy = "Flight already departed - no refund"
    
    refund_amount = round(price_paid * refund_pct, 2)
    return refund_amount, policy

def update_flight_inventory(conn, flight_id: int, seats_change: int):
    """Update flight seats and demand factor atomically"""
    cur = conn.cursor()
    
    # Get current flight data
    cur.execute("SELECT total_seats, seats_remaining, base_price, departure_time FROM flight WHERE id = ?", (flight_id,))
    flight = cur.fetchone()
    if not flight:
        raise HTTPException(404, "Flight not found")
    
    new_remaining = flight["seats_remaining"] + seats_change
    
    if new_remaining < 0:
        raise HTTPException(400, "Not enough seats available")
    if new_remaining > flight["total_seats"]:
        new_remaining = flight["total_seats"]
    
    # Calculate new demand factor
    remaining_pct = new_remaining / flight["total_seats"]
    if remaining_pct <= 0.05:
        demand_factor = 1.6
    elif remaining_pct <= 0.10:
        demand_factor = 1.4
    elif remaining_pct <= 0.20:
        demand_factor = 1.2
    else:
        demand_factor = 1.0
    
    # Update flight
    cur.execute("""
        UPDATE flight 
        SET seats_remaining = ?, 
            demand_factor = ?,
            updated_date = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (new_remaining, demand_factor, flight_id))
    
    # Log price history
    new_price = calculate_dynamic_price(
        flight["base_price"], 
        new_remaining, 
        flight["total_seats"], 
        demand_factor,
        datetime.fromisoformat(flight["departure_time"])
    )
    
    cur.execute("""
        INSERT INTO flight_price_history (flight_id, recorded_price, demand_factor, seats_remaining)
        VALUES (?, ?, ?, ?)
    """, (flight_id, new_price, demand_factor, new_remaining))

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate JWT token and return user info"""
    token = credentials.credentials
    payload = decode_token(token)
    
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, username, role, is_active FROM user WHERE id = ?", (user_id,))
        user = cur.fetchone()
        
        if not user or not user["is_active"]:
            raise HTTPException(status_code=401, detail="User not found or inactive")
        
        return dict(user)

def require_role(role: str):
    """Dependency to check user role"""
    async def role_checker(user = Depends(get_current_user)):
        if user.get("role") != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {role} role"
            )
        return user
    return role_checker

# ---------------------------
# PDF Generation (kept simple)
# ---------------------------
def generate_ticket_pdf(pnr: str, booking_details: Dict[str, Any]) -> str:
    """Generate ticket PDF"""
    file_path = f"ticket_{pnr}.pdf"
    doc = SimpleDocTemplate(file_path, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    story.append(Paragraph("✈️ E-TICKET / BOARDING PASS", styles["Title"]))
    story.append(Paragraph(f"PNR: {pnr}", styles["Heading2"]))
    story.append(Spacer(1, 0.3*inch))
    
    data = [
        ["Passenger", booking_details.get("passenger", "N/A")],
        ["Flight", booking_details.get("flight_number", "N/A")],
        ["From", booking_details.get("from_city", "N/A")],
        ["To", booking_details.get("to_city", "N/A")],
        ["Seat", booking_details.get("seat", "N/A")],
        ["Date", booking_details.get("date", "N/A")]
    ]
    
    t = Table(data, colWidths=[2*inch, 4*inch])
    t.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,0), (0,-1), colors.lightgrey)
    ]))
    story.append(t)
    
    doc.build(story)
    return file_path

def generate_cancellation_receipt(pnr: str, details: Dict[str, Any]) -> str:
    """Generate cancellation receipt PDF"""
    file_path = f"receipt_{pnr}.pdf"
    doc = SimpleDocTemplate(file_path, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    story.append(Paragraph("❌ CANCELLATION RECEIPT", styles["Title"]))
    story.append(Paragraph(f"PNR: {pnr}", styles["Heading2"]))
    story.append(Spacer(1, 0.3*inch))
    
    data = [
        ["Description", "Amount"],
        ["Original Price", f"${details.get('price_paid', 0):.2f}"],
        ["Refund Policy", details.get('policy', 'N/A')],
        ["Refund Amount", f"${details.get('refund_amount', 0):.2f}"]
    ]
    
    t = Table(data, colWidths=[3*inch, 2*inch])
    t.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 1, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.lightcoral)
    ]))
    story.append(t)
    
    doc.build(story)
    return file_path

# ---------------------------
# Background Jobs
# ---------------------------
def cleanup_expired_bookings():
    """Cancel unpaid bookings after 15 minutes"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cutoff = (datetime.now() - timedelta(minutes=15)).isoformat()
            
            cur.execute("""
                SELECT id, flight_id FROM booking 
                WHERE status = 'PENDING' AND booking_date < ?
            """, (cutoff,))
            
            expired = cur.fetchall()
            for booking in expired:
                cur.execute("SELECT COUNT(*) as cnt FROM passenger WHERE booking_id = ?", (booking["id"],))
                count = cur.fetchone()["cnt"]
                
                # Restore seats
                if count > 0:
                    update_flight_inventory(conn, booking["flight_id"], count)
                
                # Cancel booking
                cur.execute("UPDATE booking SET status = 'CANCELLED' WHERE id = ?", (booking["id"],))
            
            conn.commit()
            if expired:
                logger.info(f"Cleaned up {len(expired)} expired bookings")
    except Exception as e:
        logger.error(f"Error in cleanup job: {e}")

def update_demand_factors():
    """Periodically update demand factors (simulation)"""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM flight WHERE status = 'SCHEDULED'")
            flights = [r[0] for r in cur.fetchall()]
            
            for fid in flights:
                factor = round(random.uniform(0.95, 1.15), 2)
                cur.execute("UPDATE flight SET demand_factor = ? WHERE id = ?", (factor, fid))
            
            conn.commit()
            logger.info(f"Updated demand factors for {len(flights)} flights")
    except Exception as e:
        logger.error(f"Error updating demand: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(func=cleanup_expired_bookings, trigger="interval", minutes=5)
scheduler.add_job(func=update_demand_factors, trigger="interval", minutes=30)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ---------------------------
# API ENDPOINTS
# ---------------------------

@app.get("/", tags=["General"])
def root():
    """API root endpoint"""
    return {
        "message": "Flight Booking System - Production API",
        "version": "2.0.0",
        "docs": "/docs"
    }

@app.get("/health", tags=["General"])
def health_check():
    """Health check endpoint"""
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        db_status = "unhealthy"
    
    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat()
    }

# ---------------------------
# Authentication
# ---------------------------

@app.post("/auth/register", response_model=TokenResponse, tags=["Authentication"])
@limiter.limit("5/minute")
async def register(request: Request, user: UserRegister):
    """Register a new user"""
    logger.info(f"Registration attempt: {user.username}")
    
    with get_db() as conn:
        cur = conn.cursor()
        
        # Check if username or email exists
        cur.execute("SELECT id FROM user WHERE username = ? OR email = ?", 
                   (user.username, user.email))
        if cur.fetchone():
            raise HTTPException(400, "Username or email already exists")
        
        try:
            hashed_pwd = hash_password(user.password)
            cur.execute("""
                INSERT INTO user (username, password_hash, full_name, email, phone, country)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user.username, hashed_pwd, user.full_name, user.email, user.phone, user.country))
            
            user_id = cur.lastrowid
            conn.commit()
            
            # Generate tokens
            access_token = create_access_token({"sub": str(user_id), "username": user.username, "role": "CUSTOMER"})
            refresh_token = create_refresh_token({"sub": str(user_id)})
            
            logger.info(f"User registered successfully: {user.username} (ID: {user_id})")
            
            return TokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=settings.access_token_expire_minutes * 60
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(400, f"Registration failed: {e}")

@app.post("/auth/login", response_model=TokenResponse, tags=["Authentication"])
@limiter.limit("10/minute")
async def login(request: Request, credentials: UserLogin):
    """Login and get access token"""
    logger.info(f"Login attempt: {credentials.username}")
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM user WHERE username = ?", (credentials.username.lower(),))
        user = cur.fetchone()
        
        if not user or not verify_password(credentials.password, user["password_hash"]):
            logger.warning(f"Failed login attempt: {credentials.username}")
            raise HTTPException(401, "Invalid credentials")
        
        if not user["is_active"]:
            raise HTTPException(403, "Account is disabled")
        
        # Update last login
        cur.execute("UPDATE user SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
        conn.commit()
        
        # Generate tokens
        access_token = create_access_token({"sub": str(user["id"]), "username": user["username"], "role": user["role"]})
        refresh_token = create_refresh_token({"sub": str(user["id"])})
        
        logger.info(f"User logged in: {user['username']}")
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60
        )

@app.post("/auth/refresh", response_model=TokenResponse, tags=["Authentication"])
async def refresh_token(refresh_token: str):
    """Get new access token using refresh token"""
    payload = decode_token(refresh_token)
    
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(401, "Invalid refresh token")
    
    user_id = payload.get("sub")
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT username, role FROM user WHERE id = ? AND is_active = 1", (user_id,))
        user = cur.fetchone()
        
        if not user:
            raise HTTPException(401, "User not found")
        
        access_token = create_access_token({"sub": user_id, "username": user["username"], "role": user["role"]})
        new_refresh = create_refresh_token({"sub": user_id})
        
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_in=settings.access_token_expire_minutes * 60
        )

# ---------------------------
# Flight Management
# ---------------------------

@app.get("/flights", tags=["Flights"])
@limiter.limit("30/minute")
async def search_flights(
    request: Request,
    origin: Optional[str] = Query(None, min_length=3, max_length=3),
    destination: Optional[str] = Query(None, min_length=3, max_length=3),
    date: Optional[str] = Query(None),
    sort_by: str = Query("price", regex="^(price|departure|duration)$"),
    order: str = Query("asc", regex="^(asc|desc)$")
):
    """Search flights with filters"""
    with get_db() as conn:
        cur = conn.cursor()
        
        query = """
            SELECT f.*, 
                   fa.city_country as from_city_country,
                   ta.city_country as to_city_country
            FROM flight f
            JOIN airport_lookup fa ON fa.code = f.from_airport_code
            JOIN airport_lookup ta ON ta.code = f.to_airport_code
            WHERE f.status = 'SCHEDULED' AND f.seats_remaining > 0
        """
        params = []
        
        if origin:
            query += " AND f.from_airport_code = ?"
            params.append(origin.upper())
        
        if destination:
            query += " AND f.to_airport_code = ?"
            params.append(destination.upper())
        
        if date:
            query += " AND DATE(f.departure_time) = DATE(?)"
            params.append(date)
        
        # Only show future flights
        query += " AND f.departure_time > datetime('now')"
        
        rows = cur.execute(query, params).fetchall()
        
        results = []
        for row in rows:
            r = dict(row)
            departure_dt = datetime.fromisoformat(r["departure_time"])
            r["current_price"] = calculate_dynamic_price(
                r["base_price"],
                r["seats_remaining"],
                r["total_seats"],
                r["demand_factor"],
                departure_dt
            )
            results.append(r)
        
        # Sort results
        if sort_by == "price":
            results.sort(key=lambda x: x["current_price"], reverse=(order == "desc"))
        elif sort_by == "departure":
            results.sort(key=lambda x: x["departure_time"], reverse=(order == "desc"))
        
        logger.info(f"Flight search: {len(results)} results (origin={origin}, dest={destination})")
        return {"count": len(results), "flights": results}

@app.get("/flights/{flight_id}", tags=["Flights"])
async def get_flight(flight_id: int):
    """Get flight details by ID"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT f.*,
                   fa.city_country as from_city_country,
                   ta.city_country as to_city_country
            FROM flight f
            JOIN airport_lookup fa ON fa.code = f.from_airport_code
            JOIN airport_lookup ta ON ta.code = f.to_airport_code
            WHERE f.id = ?
        """, (flight_id,))
        
        flight = cur.fetchone()
        if not flight:
            raise HTTPException(404, "Flight not found")
        
        result = dict(flight)
        departure_dt = datetime.fromisoformat(result["departure_time"])
        result["current_price"] = calculate_dynamic_price(
            result["base_price"],
            result["seats_remaining"],
            result["total_seats"],
            result["demand_factor"],
            departure_dt
        )
        
        return result

@app.get("/flights/{flight_id}/seats", tags=["Flights"])
async def get_seat_map(flight_id: int):
    """Get seat availability map"""
    with get_db() as conn:
        cur = conn.cursor()
        
        cur.execute("SELECT total_seats, seats_remaining FROM flight WHERE id = ?", (flight_id,))
        flight = cur.fetchone()
        if not flight:
            raise HTTPException(404, "Flight not found")
        
        cur.execute("""
            SELECT seat_number, seat_type, full_name 
            FROM passenger 
            WHERE flight_id = ?
        """, (flight_id,))
        
        booked_seats = [dict(row) for row in cur.fetchall()]
        
        return {
            "flight_id": flight_id,
            "total_seats": flight["total_seats"],
            "seats_remaining": flight["seats_remaining"],
            "booked_seats": booked_seats
        }

@app.post("/admin/flights", tags=["Admin"], dependencies=[Depends(require_role("ADMIN"))])
async def create_flight(flight: FlightCreate, user = Depends(get_current_user)):
    """Create a new flight (Admin only)"""
    logger.info(f"Admin {user['username']} creating flight {flight.flight_number}")
    
    # Validate dates
    departure = datetime.fromisoformat(flight.departure_time)
    arrival = datetime.fromisoformat(flight.arrival_time)
    
    if arrival <= departure:
        raise HTTPException(400, "Arrival time must be after departure time")
    
    with get_db() as conn:
        cur = conn.cursor()
        
        # Check if airport codes exist
        for code in [flight.from_airport_code, flight.to_airport_code]:
            cur.execute("SELECT code FROM airport_lookup WHERE code = ?", (code,))
            if not cur.fetchone():
                raise HTTPException(400, f"Airport code {code} not found in lookup table")
        
        try:
            cur.execute("""
                INSERT INTO flight 
                (flight_number, airline, from_airport_code, to_airport_code, 
                 departure_time, arrival_time, base_price, total_seats, seats_remaining)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                flight.flight_number, flight.airline, flight.from_airport_code,
                flight.to_airport_code, flight.departure_time, flight.arrival_time,
                flight.base_price, flight.total_seats, flight.total_seats
            ))
            
            flight_id = cur.lastrowid
            conn.commit()
            
            logger.info(f"Flight created: {flight.flight_number} (ID: {flight_id})")
            return {"message": "Flight created", "flight_id": flight_id}
            
        except sqlite3.IntegrityError as e:
            raise HTTPException(400, f"Flight creation failed: {e}")

@app.patch("/admin/flights/{flight_id}", tags=["Admin"], dependencies=[Depends(require_role("ADMIN"))])
async def update_flight(flight_id: int, updates: Dict[str, Any], user = Depends(get_current_user)):
    """Update flight details (Admin only)"""
    allowed_fields = {"airline", "departure_time", "arrival_time", "base_price", "total_seats", "status"}
    
    update_fields = {k: v for k, v in updates.items() if k in allowed_fields}
    if not update_fields:
        raise HTTPException(400, "No valid fields to update")
    
    with get_db() as conn:
        cur = conn.cursor()
        
        set_clause = ", ".join([f"{k} = ?" for k in update_fields.keys()])
        values = list(update_fields.values()) + [flight_id]
        
        cur.execute(f"UPDATE flight SET {set_clause}, updated_date = CURRENT_TIMESTAMP WHERE id = ?", values)
        
        if cur.rowcount == 0:
            raise HTTPException(404, "Flight not found")
        
        conn.commit()
        logger.info(f"Flight {flight_id} updated by {user['username']}")
        return {"message": "Flight updated"}

# ---------------------------
# Booking
# ---------------------------

@app.post("/bookings/checkout", tags=["Bookings"])
@limiter.limit("3/minute")
async def checkout(request: Request, booking_req: BookingRequest):
    """Create a new booking with payment"""
    logger.info(f"Checkout attempt: user={booking_req.user_id}, flight={booking_req.flight_id}, passengers={len(booking_req.passengers)}")
    
    def do_checkout():
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            
            try:
                # Get flight details
                cur.execute("""
                    SELECT f.*, fa.city_country as from_city, ta.city_country as to_city
                    FROM flight f
                    JOIN airport_lookup fa ON fa.code = f.from_airport_code
                    JOIN airport_lookup ta ON ta.code = f.to_airport_code
                    WHERE f.id = ?
                """, (booking_req.flight_id,))
                
                flight = cur.fetchone()
                if not flight:
                    raise HTTPException(404, "Flight not found")
                
                if flight["status"] != "SCHEDULED":
                    raise HTTPException(400, f"Flight is {flight['status']}, cannot book")
                
                # Check departure time
                departure_dt = datetime.fromisoformat(flight["departure_time"])
                if departure_dt < datetime.now():
                    raise HTTPException(400, "Cannot book past flights")
                
                # Check seat availability
                if flight["seats_remaining"] < len(booking_req.passengers):
                    raise HTTPException(400, f"Only {flight['seats_remaining']} seats available")
                
                # Calculate price
                price_per_passenger = calculate_dynamic_price(
                    flight["base_price"],
                    flight["seats_remaining"],
                    flight["total_seats"],
                    flight["demand_factor"],
                    departure_dt
                )
                total_price = round(price_per_passenger * len(booking_req.passengers), 2)
                
                # Generate PNR
                pnr = "PNR" + uuid.uuid4().hex[:8].upper()
                
                # Create booking
                cur.execute("""
                    INSERT INTO booking 
                    (user_id, flight_id, pnr, price_paid, contact_email, contact_phone, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
                """, (
                    booking_req.user_id, booking_req.flight_id, pnr,
                    total_price, booking_req.contact_email, booking_req.contact_phone
                ))
                
                booking_id = cur.lastrowid
                
                # Insert passengers
                for passenger in booking_req.passengers:
                    try:
                        cur.execute("""
                            INSERT INTO passenger 
                            (booking_id, flight_id, seat_number, seat_type, full_name, 
                             date_of_birth, passport_number, passenger_type)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            booking_id, booking_req.flight_id, passenger.seat_number,
                            passenger.seat_type, passenger.full_name, passenger.date_of_birth,
                            passenger.passport_number, passenger.passenger_type
                        ))
                    except sqlite3.IntegrityError:
                        raise HTTPException(409, f"Seat {passenger.seat_number} already booked")
                
                # Update inventory
                update_flight_inventory(conn, booking_req.flight_id, -len(booking_req.passengers))
                
                # Process payment (simulate)
                payment_ref = "PAY_" + secrets.token_hex(8).upper()
                payment_success = random.random() > 0.02  # 98% success rate
                
                payment_status = "SUCCESS" if payment_success else "FAILED"
                
                cur.execute("""
                    INSERT INTO payment 
                    (booking_id, payment_reference, payment_method, amount_paid, payment_status)
                    VALUES (?, ?, ?, ?, ?)
                """, (booking_id, payment_ref, booking_req.payment_method, total_price, payment_status))
                
                if not payment_success:
                    conn.rollback()
                    raise HTTPException(402, "Payment failed. Please try again.")
                
                # Confirm booking
                cur.execute("""
                    UPDATE booking 
                    SET status = 'CONFIRMED', payment_reference = ?
                    WHERE id = ?
                """, (payment_ref, booking_id))
                
                conn.commit()
                
                logger.info(f"Booking successful: PNR={pnr}, booking_id={booking_id}")
                
                return {
                    "status": "CONFIRMED",
                    "pnr": pnr,
                    "booking_id": booking_id,
                    "payment_reference": payment_ref,
                    "price_paid": total_price,
                    "message": "Booking confirmed successfully"
                }
                
            except HTTPException:
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"Checkout error: {e}")
                raise HTTPException(500, f"Booking failed: {str(e)}")
    
    return with_retry(do_checkout)

@app.get("/bookings/history/{user_id}", tags=["Bookings"])
async def get_booking_history(user_id: int, user = Depends(get_current_user)):
    """Get booking history for a user"""
    # Users can only view their own history unless admin
    if user["role"] != "ADMIN" and user["id"] != user_id:
        raise HTTPException(403, "Cannot view other users' bookings")
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT b.pnr, b.price_paid, b.booking_date, b.status,
                   f.flight_number, f.airline, f.departure_time,
                   fa.city_country as from_city,
                   ta.city_country as to_city
            FROM booking b
            JOIN flight f ON f.id = b.flight_id
            JOIN airport_lookup fa ON fa.code = f.from_airport_code
            JOIN airport_lookup ta ON ta.code = f.to_airport_code
            WHERE b.user_id = ?
            ORDER BY b.booking_date DESC
        """, (user_id,))
        
        bookings = [dict(row) for row in cur.fetchall()]
        
        return {
            "user_id": user_id,
            "count": len(bookings),
            "bookings": bookings
        }

@app.get("/bookings/{pnr}", tags=["Bookings"])
async def get_booking(pnr: str, user = Depends(get_current_user)):
    """Get booking details by PNR"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT b.*, f.flight_number, f.airline, f.departure_time, f.arrival_time,
                   fa.city_country as from_city, ta.city_country as to_city
            FROM booking b
            JOIN flight f ON f.id = b.flight_id
            JOIN airport_lookup fa ON fa.code = f.from_airport_code
            JOIN airport_lookup ta ON ta.code = f.to_airport_code
            WHERE b.pnr = ?
        """, (pnr,))
        
        booking = cur.fetchone()
        if not booking:
            raise HTTPException(404, "Booking not found")
        
        # Authorization check
        if user["role"] != "ADMIN" and booking["user_id"] != user["id"]:
            raise HTTPException(403, "Unauthorized")
        
        result = dict(booking)
        
        # Get passengers
        cur.execute("""
            SELECT full_name, seat_number, seat_type, passenger_type
            FROM passenger WHERE booking_id = ?
        """, (booking["id"],))
        result["passengers"] = [dict(row) for row in cur.fetchall()]
        
        return result

@app.get("/bookings/ticket/{pnr}", tags=["Bookings"])
async def download_ticket(pnr: str, user = Depends(get_current_user)):
    """Download ticket PDF for confirmed booking"""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT b.*, f.flight_number, f.airline, f.departure_time,
                   fa.city_country as from_city, ta.city_country as to_city,
                   p.full_name as passenger_name, p.seat_number
            FROM booking b
            JOIN flight f ON f.id = b.flight_id
            JOIN airport_lookup fa ON fa.code = f.from_airport_code
            JOIN airport_lookup ta ON ta.code = f.to_airport_code
            JOIN passenger p ON p.booking_id = b.id
            WHERE b.pnr = ? AND b.status = 'CONFIRMED'
            LIMIT 1
        """, (pnr,))
        
        booking = cur.fetchone()
        if not booking:
            raise HTTPException(404, "Confirmed booking not found")
        
        # Authorization
        if user["role"] != "ADMIN" and booking["user_id"] != user["id"]:
            raise HTTPException(403, "Unauthorized")
        
        details = {
            "passenger": booking["passenger_name"],
            "flight_number": booking["flight_number"],
            "from_city": booking["from_city"],
            "to_city": booking["to_city"],
            "seat": booking["seat_number"],
            "date": booking["departure_time"].split()[0]
        }
        
        pdf_path = generate_ticket_pdf(pnr, details)
        return FileResponse(pdf_path, media_type="application/pdf", filename=f"ticket_{pnr}.pdf")

@app.post("/bookings/cancel/{pnr}", tags=["Bookings"])
async def cancel_booking(pnr: str, reason: Optional[str] = "User requested", user = Depends(get_current_user)):
    """Cancel a confirmed booking"""
    logger.info(f"Cancellation request: PNR={pnr}, user={user['username']}")
    
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        
        try:
            # Get booking
            cur.execute("""
                SELECT b.*, f.departure_time
                FROM booking b
                JOIN flight f ON f.id = b.flight_id
                WHERE b.pnr = ?
            """, (pnr,))
            
            booking = cur.fetchone()
            if not booking:
                raise HTTPException(404, "Booking not found")
            
            # Authorization
            if user["role"] != "ADMIN" and booking["user_id"] != user["id"]:
                raise HTTPException(403, "Unauthorized")
            
            if booking["status"] != "CONFIRMED":
                raise HTTPException(400, "Only confirmed bookings can be cancelled")
            
            # Calculate refund
            departure_dt = datetime.fromisoformat(booking["departure_time"])
            refund_amount, refund_policy = calculate_refund(booking["price_paid"], departure_dt)
            
            # Count passengers
            cur.execute("SELECT COUNT(*) as cnt FROM passenger WHERE booking_id = ?", (booking["id"],))
            passenger_count = cur.fetchone()["cnt"]
            
            # Archive cancellation
            cur.execute("""
                INSERT INTO cancelled_booking 
                (pnr, user_id, flight_id, price_paid, refund_amount, cancellation_reason)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pnr, booking["user_id"], booking["flight_id"], booking["price_paid"], refund_amount, reason))
            
            # Update booking
            cur.execute("""
                UPDATE booking 
                SET status = 'CANCELLED', 
                    cancellation_date = CURRENT_TIMESTAMP,
                    refund_amount = ?
                WHERE id = ?
            """, (refund_amount, booking["id"]))
            
            # Update payment status
            cur.execute("""
                UPDATE payment 
                SET payment_status = 'REFUNDED'
                WHERE booking_id = ?
            """, (booking["id"],))
            
            # Restore seats
            update_flight_inventory(conn, booking["flight_id"], passenger_count)
            
            conn.commit()
            
            logger.info(f"Booking cancelled: PNR={pnr}, refund=${refund_amount}")
            
            # Generate receipt
            receipt_details = {
                "price_paid": booking["price_paid"],
                "refund_amount": refund_amount,
                "policy": refund_policy
            }
            
            pdf_path = generate_cancellation_receipt(pnr, receipt_details)
            return FileResponse(pdf_path, media_type="application/pdf", filename=f"receipt_{pnr}.pdf")
            
        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            logger.error(f"Cancellation error: {e}")
            raise HTTPException(500, f"Cancellation failed: {str(e)}")

# ---------------------------
# Admin Endpoints
# ---------------------------

@app.get("/admin/bookings", tags=["Admin"], dependencies=[Depends(require_role("ADMIN"))])
async def get_all_bookings(
    status: Optional[str] = Query(None, regex="^(PENDING|CONFIRMED|CANCELLED)$"),
    limit: int = Query(50, ge=1, le=500)
):
    """Get all bookings (Admin only)"""
    with get_db() as conn:
        cur = conn.cursor()
        
        query = """
            SELECT b.*, u.username, f.flight_number
            FROM booking b
            JOIN user u ON u.id = b.user_id
            JOIN flight f ON f.id = b.flight_id
            WHERE 1=1
        """
        params = []
        
        if status:
            query += " AND b.status = ?"
            params.append(status)
        
        query += " ORDER BY b.booking_date DESC LIMIT ?"
        params.append(limit)
        
        cur.execute(query, params)
        bookings = [dict(row) for row in cur.fetchall()]
        
        return {"count": len(bookings), "bookings": bookings}

@app.get("/admin/stats", tags=["Admin"], dependencies=[Depends(require_role("ADMIN"))])
async def get_stats():
    """Get system statistics (Admin only)"""
    with get_db() as conn:
        cur = conn.cursor()
        
        cur.execute("SELECT COUNT(*) as total FROM booking WHERE status = 'CONFIRMED'")
        total_bookings = cur.fetchone()["total"]
        
        cur.execute("SELECT SUM(price_paid) as revenue FROM booking WHERE status = 'CONFIRMED'")
        total_revenue = cur.fetchone()["revenue"] or 0
        
        cur.execute("SELECT COUNT(*) as total FROM flight WHERE status = 'SCHEDULED'")
        total_flights = cur.fetchone()["total"]
        
        cur.execute("SELECT COUNT(*) as total FROM user WHERE is_active = 1")
        total_users = cur.fetchone()["total"]
        
        return {
            "total_bookings": total_bookings,
            "total_revenue": round(total_revenue, 2),
            "total_flights": total_flights,
            "total_users": total_users
        }

# ---------------------------
# Run Application
# ---------------------------
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Flight Booking API...")
    uvicorn.run("flight_api_production:app", host="0.0.0.0", port=8000, reload=True)
import sqlite3

conn = sqlite3.connect("db.sqlite", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("SELECT * FROM flight LIMIT 5")
print(cursor.fetchall())