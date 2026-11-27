from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DB_URL = "sqlite:///data/cobrax.db"

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """
    WAL permite que o webhook e o sender (processos separados) usem o mesmo
    arquivo: leitores não bloqueiam o escritor. busy_timeout faz um processo
    esperar até 5s por um lock em vez de falhar com "database is locked".
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
