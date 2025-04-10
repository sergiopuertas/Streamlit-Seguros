from lib import *

# ---------------------------
# Modelos Pydantic para request/response
# ---------------------------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class User(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    area: str

class NewCrime(BaseModel):
    date: datetime
    crimecodedesc: str
    areaname: str

class RegisterUser(BaseModel):
    email: EmailStr
    full_name: str
    area: str
    password: str
    role: str

class GroupedDataRequest(BaseModel):
    chosen_crime: Optional[List[str]] = None
    chosen_place: Optional[List[str]] = None
    frequency: str  # "Por trimestre", "Por mes" o "Por semana"
    init_time : Optional[datetime] = None
    end_time : Optional[datetime] = None


# ---------------------------
# FastAPI: Endpoints y Autenticación
# ---------------------------
app = FastAPI(title="Foresee")

TOKENS = {}

# ---------------------------
# Funciones de autenticación y verificación
# ---------------------------

def authenticate_user(email: str, password: str, eng: Engine ) -> str:
    data_components = DataComponents(eng)
    if data_components.verify_login(email, password):
        token = str(uuid.uuid4())
        TOKENS[token] = email
        return token
    raise HTTPException(status_code=401, detail="Credenciales inválidas")

def get_current_user(x_token: str = Header(...), eng: Engine = Depends(get_engine)) -> User:
    email = TOKENS.get(x_token)
    if not email:
        raise HTTPException(status_code=401, detail="Token inválido")

    data_components = DataComponents(eng)
    user = data_components.get_user(email)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user

# ---------------------------
# Endpoints de la API
# ---------------------------

# Endpoint de login con cookie
"""
:request
{
  "email": "ejemplo@mail.com",
  "password": "tu_clave"
}

:returns
{
  "token": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
}

:headers
{
  "Content-Type": "application/json"
}


"""
@app.post("/login")
def login(request: LoginRequest, response: Response, eng: Engine = Depends(get_engine)):
    token = authenticate_user(request.email, request.password, eng)
    # Guardamos el token en la cookie
    response.set_cookie(key="Authorization", value=f"Bearer {token}", httponly=True)
    return {"token": token}

# Endpoint para obtener permisos de un usuario
"""

:headers
{
  "Authorization": "Bearer <token>"
}

:cookie
{
  "Authorization": "Bearer <token>"
}

"""
@app.get("/permissions")
def permissions(user: User = Depends(get_current_user), eng: Engine = Depends(get_engine)):
    data_components = DataComponents(eng)
    email = user.email.iloc[0] if isinstance(user.email, pd.Series) else user.email

    perms = data_components.get_user_permissions(email)
    return {"permissions": perms}

# Endpoint para cerrar sesión (eliminar la cookie)
"""
:headers
{
  "Authorization": "Bearer <token>"
}
:cookie
{
  "Authorization": "Bearer <token>"
}
"""
@app.post("/logout")
def logout(response: Response):
    # Eliminamos la cookie
    response.delete_cookie("Authorization")
    return {"message": "Sesión cerrada exitosamente"}

# Endpoint para obtener lugares seguros según permisos
"""
:body
{
  "see": "SEE_LOCAL"  # O puede ser "SEE_ALL"
}

:returns
{
  "places": ["COMUNA 1", "COMUNA 2", "COMUNA 3"]
}

:headers
{
  "Authorization": "Bearer <token>"
}

:cookie
{
  "Authorization": "Bearer <token>"
}
"""
@app.get("/secure-places")
def secure_places(see: str, user: User = Depends(get_current_user), eng: Engine = Depends(get_engine)):
    data_components = DataComponents(eng)
    places = data_components.get_secure_unique_places(user.email, see)
    return {"places": places}

# Endpoint para obtener datos agrupados según filtros
"""
:body
{
  "chosen_crime": ["Robo", "Homicidio"],
  "chosen_place": ["COMUNA 1"],
  "frequency": "Por mes"
}

:headers
{
  "Authorization": "Bearer <token>"
}

:cookie
{
  "Authorization": "Bearer <token>"
}
"""
@app.post("/grouped-data")
def get_grouped_data(request: GroupedDataRequest,
                     user: User = Depends(get_current_user),
                     eng: Engine = Depends(get_engine)):
    data_components = DataComponents(eng)

    crimes = request.chosen_crime[0].replace("'", "").split(",") if request.chosen_crime else None
    places = data_components.get_secure_unique_places(user.email[0] if isinstance(user.email, pd.Series) else user.email, "SEE_LOCAL")
    if request.chosen_place and any(place not in places for place in request.chosen_place):
        raise HTTPException(status_code=403, detail="No autorizado para acceder a este lugar")

    crime_cond, place_cond = build_conditions(crimes, request.chosen_place)
    df = data_components.secure_fetch_grouped_data(crime_cond, place_cond, freqmap[request.frequency]).drop(columns=['pond'])
    df['period'] = pd.to_datetime(df['period']).dt.date
    if request.init_time and request.end_time:
        df = df[(df['period'] >= request.init_time.date()) & (df['period'] <= request.end_time.date())]
    return df.to_dict(orient="records")

# Endpoint para predecir datos
"""
:body
{
  "chosen_crime": ["Robo"],
  "chosen_place": ["COMUNA 1"],
  "frequency": "Por mes",
  "n_steps": 6,
  "ponder": true
}

:headers
{
  "Authorization": "Bearer <token>"
}

:cookie
{
  "Authorization": "Bearer <token>"
}
"""
@app.post("/predict")
def predict_data(chosen_crime: Optional[List[str]] = None,
                 chosen_place: Optional[List[str]] = None,
                 frequency: str = "Por mes",
                 n_steps: int = 6,
                 ponder: bool = False,
                 user: User = Depends(get_current_user),
                 eng: Engine = Depends(get_engine)):
    data_components = DataComponents(eng)
    perms = data_components.get_user_permissions(user.email[0] if isinstance(user.email, pd.Series) else user.email)
    if "PREDICT SI" not in perms:
        raise HTTPException(status_code=403, detail="No autorizado para predecir datos")

    crimes = chosen_crime[0].replace("'", "").split(",") if chosen_crime else None
    crime_cond, place_cond = build_conditions(crimes, chosen_place)

    df = data_components.secure_fetch_grouped_data(crime_cond, place_cond, freqmap[frequency])
    df['period'] = pd.to_datetime(df['period']).dt.date
    df = apply_ponderation_to_data(df, ponder)
    forecast = forecast_data(df, freqmap[frequency], n_steps)
    forecast.drop(forecast[forecast['tipo']=="Histórico"], axis=1)
    forecast['ds'] = pd.to_datetime(forecast['ds']).dt.date
    forecast['yhat'] = forecast['yhat'].round(0).astype(int)
    forecast['yhat_lower'] = forecast['yhat_lower'].round(0).astype(int)
    forecast['yhat_upper'] = forecast['yhat_upper'].round(0).astype(int)
    return forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].to_dict(orient='records')

# Endpoint para ingresar nuevos datos (requiere rol "Nuevos datos SI")
"""
:body
{
  "date": "2025-04-08",
  "crimecodedesc": "Robo",
  "areaname": "COMUNA 1"
}

:returns
{
  "status": "success"
}

:headers
{
  "Authorization": "Bearer <token>"
}

:cookie
{
  "Authorization": "Bearer <token>"
}
"""
@app.post("/new-data")
def new_data(record: NewCrime,
             user: User = Depends(get_current_user),
             eng: Engine = Depends(get_engine)):
    data_components = DataComponents(eng)
    perms = data_components.get_user_permissions(user.email[0] if isinstance(user.email, pd.Series) else user.email)
    if "Nuevos datos SI" not in perms:
        raise HTTPException(status_code=403, detail="No autorizado para ingresar datos")

    try:
        with eng.connect() as conn:
            recdf = pd.DataFrame([{
                "date": record.date,
                "crimecodedesc": category_map[record.crimecodedesc],
                "areaname": record.areaname
            }])
            recdf = apply_pond(recdf)
            recdf.to_sql(
                'main',
                conn,
                if_exists='append',
                index=False
            )
            conn.commit()
            return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint para registrar un nuevo usuario (requiere rol "Nuevos usuarios SI")
"""
:body
{
  "email": "nuevo@mail.com",
  "full_name": "Nuevo Usuario",
  "area": "COMUNA 1",
  "password": "clave123",
  "role": "SEE_LOCAL"
}

:returns
{
  "status": "Usuario creado"
}

:headers
{
  "Authorization": "Bearer <token>"
}

:cookie
{
  "Authorization": "Bearer <token>"
}
"""
@app.post("/register")
def register_user(new_user: RegisterUser,
                  user: User = Depends(get_current_user),
                  eng: Engine = Depends(get_engine)):
    data_components = DataComponents(eng)
    perms = data_components.get_user_permissions(user.email[0] if isinstance(user.email, pd.Series) else user.email)
    if "Nuevos usuarios SI" not in perms:
        raise HTTPException(status_code=403, detail="No autorizado para crear usuarios")
    if data_components.get_user(new_user.email):
        raise HTTPException(status_code=400, detail="El usuario ya existe")
    success = data_components.create_user(new_user.email, new_user.full_name, new_user.area, new_user.password, new_user.role)
    if success:
        return {"status": "Usuario creado"}
    raise HTTPException(status_code=500, detail="Error al crear usuario")

# Endpoint raíz para verificar si el API está corriendo
@app.get("/")
def root():
    return {"message": "Crime Data API is running"}

# ---------------------------
# Iniciar el servidor Uvicorn
# ---------------------------
if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
