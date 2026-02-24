from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import bcrypt
from sqlalchemy import Float

# 1. CONFIGURACIÓN DE BASE DE DATOS (Reemplaza con tu URL de Neon)
# Ejemplo: "postgresql://usuario:password@ep-host.region.aws.neon.tech/neondb?sslmode=require"
DATABASE_URL = "postgresql://neondb_owner:npg_Lj1aJqiPS2rx@ep-tiny-queen-ai4ubol1-pooler.c-4.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # <--- Esto hace un "ping" antes de cada consulta
    pool_recycle=300,         # <--- Recicla las conexiones cada 5 minutos
    connect_args={
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    }
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. MODELO DE BASE DE DATOS (La tabla en PostgreSQL)
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

class OrderDB(Base):
    __tablename__ = "orders_v2" # <--- EL HACK: Forzamos a Neon a crear una tabla nueva
    
    id = Column(Integer, primary_key=True, index=True)
    center_id = Column(Integer)
    user_lat = Column(Float)
    user_lng = Column(Float)
    status = Column(String, default="Pendiente")
    items = Column(String, default="") # <--- NUEVA COLUMNA para guardar la basura electrónica

# Crea la tabla en la base de datos si no existe
Base.metadata.create_all(bind=engine)

# 3. ESQUEMAS DE PYDANTIC (Validación de datos)
class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str
    lat: float = 0.0        # <--- Nuevo
    lng: float = 0.0        # <--- Nuevo

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

class OrderCreate(BaseModel):
    center_id: int
    user_lat: float
    user_lng: float
    items: list[str] = []

    class Config:
        from_attributes = True

# 4. SEGURIDAD (Encriptación directa con bcrypt)
def get_password_hash(password: str) -> str:
    # bcrypt requiere que el password sea un string de bytes
    pwd_bytes = password.encode('utf-8')
    # Generamos la sal y encriptamos
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(pwd_bytes, salt)
    # Devolvemos el string decodificado para guardarlo en la base de datos
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_byte_enc = plain_password.encode('utf-8')
    hashed_password_byte = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_byte_enc, hashed_password_byte)

# 5. INICIALIZAR FASTAPI
app = FastAPI(title="CircularTech API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 6. ENDPOINT DE REGISTRO
@app.post("/api/register")
def register_user(user: UserCreate, db: Session = Depends(get_db)):
    # 1. Verificamos si el correo ya existe
    db_user = db.query(UserDB).filter(UserDB.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="El correo ya está registrado")
    
    # 2. Creamos al usuario (Asegúrate de tener tu función de hashear contraseña aquí)
    new_user = UserDB(
        name=user.name,
        email=user.email,
        password_hash=get_password_hash(user.password), # Ojo: usa tu función de hash si la tienes
        role=user.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # 3. ¡LA MAGIA DEL MAPA! Si es centro, lo guardamos en CenterDB
    # 3. ¡LA MAGIA DEL MAPA! Si es centro, lo guardamos en CenterDB
    if user.role == "Centro":
        new_center = CenterDB(
            name=user.name,
            latitude=user.lat,   # <--- CORREGIDO
            longitude=user.lng,  # <--- CORREGIDO
            address="Dirección no especificada", # Agregamos esto para que no quede nulo
            certifications="Centro Autorizado CircularTech" 
        )
        db.add(new_center)
        db.commit()
        
    return {"message": "Usuario registrado exitosamente"}

    
    # Agrega este esquema debajo de tus otros esquemas (UserCreate, UserResponse)
class UserLogin(BaseModel):
    email: str
    password: str

@app.post("/api/login")
def login_user(user: UserLogin, db: Session = Depends(get_db)):
    # 1. Buscamos al usuario por correo en la tabla general
    db_user = db.query(UserDB).filter(UserDB.email == user.email).first()
    
    # 2. Verificamos que exista y que la contraseña coincida
    if not db_user or not verify_password(user.password, db_user.password_hash):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")
    
    # --- EL FIX DEL GAFETE (ID) ---
    # Por defecto, asumimos que su ID es el de usuario normal
    final_id = db_user.id
    
    # Pero si es un Centro, buscamos su ID real en la tabla de centros (usando su nombre)
    if db_user.role == "Centro":
        center_record = db.query(CenterDB).filter(CenterDB.name == db_user.name).first()
        if center_record:
            final_id = center_record.id # ¡Lo cambiamos por el ID del mapa!
            
    # 3. Si todo está bien, devolvemos sus datos con el ID corregido
    return {
        "message": "Login exitoso", 
        "user": {
            "id": final_id, # <--- Ahora Flutter recibirá el ID correcto
            "name": db_user.name, 
            "role": db_user.role,
            "email": db_user.email
        }
    }

@app.get("/api/centers", response_model=list[CenterResponse])
def get_centers(db: Session = Depends(get_db)):
    centers = db.query(CenterDB).all()
    
    # Si aún no has guardado centros en tu base de datos Neon, enviamos estos de prueba
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

@app.post("/api/orders")
def create_order(order: OrderCreate, db: Session = Depends(get_db)):
    # Convertimos la lista de Flutter a un texto simple para la base de datos
    items_str = ", ".join(order.items)
    
    new_order = OrderDB(
        center_id=order.center_id,
        user_lat=order.user_lat,
        user_lng=order.user_lng,
        items=items_str # <--- Guardamos la basura electrónica aquí
    )
    db.add(new_order)
    db.commit()
    db.refresh(new_order)
    return {"message": "Orden creada con éxito", "order_id": new_order.id, "items": new_order.items}
    
@app.get("/api/orders")
def get_orders(db: Session = Depends(get_db)):
    # Traemos todas las órdenes ordenadas de la más reciente a la más antigua
    orders = db.query(OrderDB).order_by(OrderDB.id.desc()).all()
    return orders

@app.get("/api/orders/center/{center_id}")
def get_center_orders(center_id: int, db: Session = Depends(get_db)):
    # Filtramos por el ID del centro y que solo sean las "Pendientes"
    orders = db.query(OrderDB).filter(
        OrderDB.center_id == center_id
    ).order_by(OrderDB.id.desc()).all()
    
    return orders

@app.put("/api/orders/{order_id}/complete")
def complete_order(order_id: int, db: Session = Depends(get_db)):
    # Buscamos la orden específica en la base de datos
    order = db.query(OrderDB).filter(OrderDB.id == order_id).first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
        
    # Actualizamos el estado
    order.status = "Completado"
    db.commit()
    
    return {"message": "Orden marcada como Completada"}