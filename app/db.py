import sqlalchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session
from sqlalchemy.orm import sessionmaker

from app import config


engine = create_engine(
    config.DB_URI,
    connect_args={"application_name": config.DB_CONN_NAME},
    # a connection that died while idle (database restart) is replaced on checkout
    pool_pre_ping=True,
)

# Bound to the engine, not to one long-lived connection: when that connection broke,
# every query of the process failed until it was restarted.
Session = scoped_session(sessionmaker(bind=engine))

# Session is actually a proxy, more info on
# https://docs.sqlalchemy.org/en/14/orm/contextual.html?highlight=scoped_session#implicit-method-access
Session: sqlalchemy.orm.Session
