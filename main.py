from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, event, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import bcrypt
from sqlalchemy import Float
import httpx
import uuid

# 1. CONFIGURACIÓN DE BASE DE DATOS (Reemplaza con tu URL de Neon)
DATABASE_URL = "postgresql://neondb_owner:npg_Lj1aJqiPS2rx@ep-tiny-queen-ai4ubol1-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. MODELOS DE BASE DE DATOS
class UserDB(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String) # 'Generador' o 'Centro'

class CenterDB(Base):
    __tablename__ = "centers"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    address = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    certifications = Column(String)

# --- NUEVO: Tabla de Recolectores ---
class CollectorDB(Base):
    __tablename__ = "collectors"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    center_id = Column(Integer)  # Centro al que pertenece

class OrderDB(Base):
    __tablename__ = "orders_v3"
    
    id = Column(Integer, primary_key=True, index=True)
    folio = Column(String, unique=True, index=True)  # Folio único e impredecible
    user_id = Column(Integer)
    center_id = Column(Integer)
    collector_id = Column(Integer, nullable=True)  # NUEVO: Recolector asignado
    user_lat = Column(Float)
    user_lng = Column(Float)
    address = Column(String, default="")            # NUEVO: Dirección legible
    status = Column(String, default="Pendiente")
    items = Column(String, default="") 

class OrderCreate(BaseModel):
    user_id: int
    center_id: int
    user_lat: float
    user_lng: float
    items: list[str] = []

# Crea las tablas nuevas (collectors) si no existen
Base.metadata.create_all(bind=engine)

# --- Migración: agregar columnas nuevas a orders_v3 si no existen ---
def _add_column_if_not_exists(conn, table: str, column: str, col_type: str):
    """Agrega una columna a la tabla solo si no existe aún."""
    result = conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name='{table}' AND column_name='{column}'"
    ))
    if result.fetchone() is None:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        conn.commit()

with engine.connect() as conn:
    _add_column_if_not_exists(conn, "orders_v3", "collector_id", "INTEGER")
    _add_column_if_not_exists(conn, "orders_v3", "address", "VARCHAR DEFAULT ''")
    _add_column_if_not_exists(conn, "orders_v3", "folio", "VARCHAR UNIQUE")

def _generate_folio() -> str:
    """Genera un folio único de 8 caracteres hexadecimales con prefijo CT."""
    return f"CT-{uuid.uuid4().hex[:8].upper()}"

# 3. ESQUEMAS DE PYDANTIC
class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str
    lat: float = 0.0
    lng: float = 0.0

class UserUpdate(BaseModel):
    name: str
    email: str

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str

    class Config:
        from_attributes = True

class CenterResponse(BaseModel):
    id: int
    name: str
    address: str
    latitude: float
    longitude: float
    certifications: str

class UserLogin(BaseModel):
    email: str
    password: str

# --- NUEVO: Esquemas para Recolectores ---
class CollectorCreate(BaseModel):
    name: str
    email: str
    password: str
    center_id: int

class CollectorLogin(BaseModel):
    email: str
    password: str

class AssignCollector(BaseModel):
    collector_id: int

# 4. SEGURIDAD
def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(pwd_bytes, salt)
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_byte_enc = plain_password.encode('utf-8')
    hashed_password_byte = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_byte_enc, hashed_password_byte)

# --- NUEVO: Geocodificación inversa con Nominatim (OpenStreetMap, gratis) ---
async def reverse_geocode(lat: float, lng: float) -> str:
    """Convierte coordenadas a una dirección legible usando Nominatim."""
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lng}&format=json&accept-language=es"
            response = await client.get(url, headers={"User-Agent": "CircularTech/1.0"})
            if response.status_code == 200:
                data = response.json()
                return data.get("display_name", "Dirección no disponible")
    except Exception:
        pass
    return "Dirección no disponible"

# 5. INICIALIZAR FASTAPI
app = FastAPI(title="CircularTech API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================================================
#  ENDPOINTS DE USUARIOS (Registro y Login)
# ============================================================

@app.post("/api/register")
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(UserDB).filter(UserDB.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    
    new_user = UserDB(
        name=user.name,
        email=user.email,
        password_hash=get_password_hash(user.password),
        role=user.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    if user.role == "Centro":
        new_center = CenterDB(
            name=user.name,
            latitude=user.lat,
            longitude=user.lng,
            address="Dirección no especificada",
            certifications="Centro Autorizado CircularTech" 
        )
        db.add(new_center)
        db.commit()
        
    return {"message": "Usuario registrado exitosamente"}

@app.post("/api/login")
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    # Primero buscamos en usuarios normales (Generador / Centro)
    db_user = db.query(UserDB).filter(UserDB.email == user.email).first()
    
    if db_user and verify_password(user.password, db_user.password_hash):
        final_id = db_user.id
        
        if db_user.role == "Centro":
            center_record = db.query(CenterDB).filter(CenterDB.name == db_user.name).first()
            if center_record:
                final_id = center_record.id
                
        return {
            "message": "Login exitoso", 
            "user": {
                "id": final_id,
                "name": db_user.name, 
                "role": db_user.role,
                "email": db_user.email
            }
        }
    
    # Si no se encontró como usuario, buscamos en recolectores
    collector = db.query(CollectorDB).filter(CollectorDB.email == user.email).first()
    if collector and verify_password(user.password, collector.password_hash):
        return {
            "message": "Login exitoso",
            "user": {
                "id": collector.id,
                "name": collector.name,
                "role": "Recolector",
                "email": collector.email,
                "center_id": collector.center_id
            }
        }
    
    raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")

# ============================================================
#  ENDPOINTS DE CENTROS
# ============================================================

@app.get("/api/centers", response_model=list[CenterResponse])
def get_centers(db: Session = Depends(get_db)):
    centers = db.query(CenterDB).all()
    
    if not centers:
        return [
            {
                "id": 1, 
                "name": "RECITAB Centro de Reciclaje Parrilla", 
                "address": "villa parrilla, villahermosa", 
                "latitude": 17.8662, 
                "longitude": -92.9244, 
                "certifications": "R2v3, ISO 14001"
            },
            {
                "id": 2, 
                "name": "Reciclaje de la sierra", 
                "address": "Carlos Pellicer Cámara 110, 1° de Mayo, Villahermosa", 
                "latitude": 17.96844, 
                "longitude": -92.9268, 
                "certifications": "NOM-161-SEMARNAT"
            }
        ]
        
    return centers

# ============================================================
#  ENDPOINTS DE ÓRDENES
# ============================================================

@app.post("/api/orders")
async def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    items_str = ", ".join(order.items)
    
    # Geocodificación inversa: convertimos las coordenadas en dirección
    address = await reverse_geocode(order.user_lat, order.user_lng)
    
    folio = _generate_folio()
    
    new_order = OrderDB(
        folio=folio,
        user_id=order.user_id,
        center_id=order.center_id,
        user_lat=order.user_lat,
        user_lng=order.user_lng,
        address=address,
        items=items_str 
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    return {"message": "Orden creada", "order_id": new_order.id, "folio": new_order.folio}
    
@app.get("/api/orders")
def get_orders(db: Session = Depends(get_db)):
    orders = db.query(OrderDB).order_by(OrderDB.id.desc()).all()
    return orders

@app.get("/api/orders/user/{user_id}")
def get_user_orders(user_id: int, db: Session = Depends(get_db)):
    orders = db.query(OrderDB).filter(
        OrderDB.user_id == user_id
    ).order_by(OrderDB.id.desc()).all()
    return orders

@app.get("/api/orders/center/{center_id}")
def get_center_orders(center_id: int, db: Session = Depends(get_db)):
    orders = db.query(OrderDB).filter(
        OrderDB.center_id == center_id
    ).order_by(OrderDB.id.desc()).all()
    return orders

@app.put("/api/orders/{order_id}/complete")
def complete_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    order.status = "Completado"
    db.commit()
    return {"message": "Orden marcada como Completada"}

# --- NUEVO: Asignar orden a un recolector ---
@app.put("/api/orders/{order_id}/assign")
def assign_order(order_id: int, data: AssignCollector, db: Session = Depends(get_db)):
    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    
    collector = db.query(CollectorDB).filter(CollectorDB.id == data.collector_id).first()
    if not collector:
        raise HTTPException(status_code=404, detail="Recolector no encontrado")
    
    order.collector_id = data.collector_id
    order.status = "Asignado"
    db.commit()
    
    return {"message": f"Orden asignada a {collector.name}"}

# --- NUEVO: Órdenes asignadas a un recolector ---
@app.get("/api/orders/collector/{collector_id}")
def get_collector_orders(collector_id: int, db: Session = Depends(get_db)):
    orders = db.query(OrderDB).filter(
        OrderDB.collector_id == collector_id,
        OrderDB.status.in_(["Asignado", "En camino"])
    ).order_by(OrderDB.id.desc()).all()
    return orders

# --- NUEVO: Recolector marca que va en camino ---
@app.put("/api/orders/{order_id}/en-camino")
def order_en_camino(order_id: int, db: Session = Depends(get_db)):
    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    order.status = "En camino"
    db.commit()
    return {"message": "Estado actualizado a En camino"}

# ============================================================
#  ENDPOINTS DE RECOLECTORES
# ============================================================

# --- NUEVO: Centro registra un recolector ---
@app.post("/api/collectors")
def register_collector(collector: CollectorCreate, db: Session = Depends(get_db)):
    # Verificar que el correo no esté en uso (ni en users ni en collectors)
    existing_user = db.query(UserDB).filter(UserDB.email == collector.email).first()
    existing_collector = db.query(CollectorDB).filter(CollectorDB.email == collector.email).first()
    
    if existing_user or existing_collector:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    
    # Verificar que el centro exista
    center = db.query(CenterDB).filter(CenterDB.id == collector.center_id).first()
    if not center:
        raise HTTPException(status_code=404, detail="Centro no encontrado")
    
    new_collector = CollectorDB(
        name=collector.name,
        email=collector.email,
        password_hash=get_password_hash(collector.password),
        center_id=collector.center_id
    )
    db.add(new_collector)
    db.commit()
    db.refresh(new_collector)
    
    return {"message": "Recolector registrado exitosamente", "collector_id": new_collector.id}

# --- NUEVO: Listar recolectores de un centro ---
@app.get("/api/collectors/center/{center_id}")
def get_center_collectors(center_id: int, db: Session = Depends(get_db)):
    collectors = db.query(CollectorDB).filter(
        CollectorDB.center_id == center_id
    ).all()
    return [{"id": c.id, "name": c.name, "email": c.email} for c in collectors]

# ============================================================
#  ENDPOINTS DE PERFIL
# ============================================================

@app.get("/api/users/{user_id}")
def get_user_profile(user_id: int, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return {"name": user.name, "email": user.email}

@app.put("/api/users/{user_id}")
def update_user_profile(user_id: int, user_data: UserUpdate, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    if user.role == "Centro":
        center = db.query(CenterDB).filter(CenterDB.name == user.name).first()
        if center:
            center.name = user_data.name
            
    user.name = user_data.name
    user.email = user_data.email
    db.commit()
    
    return {"message": "Perfil actualizado con éxito"}

@app.get("/api/orders/user/{user_id}/completed")
def get_user_completed_orders(user_id: int, db: Session = Depends(get_db)):
    orders = db.query(OrderDB).filter(
        OrderDB.user_id == user_id,
        OrderDB.status == "Completado"
    ).all()
    return orders