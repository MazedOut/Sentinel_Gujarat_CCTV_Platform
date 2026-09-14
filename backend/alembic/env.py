import sys
from pathlib import Path
# Add project root to sys.path so we can import backend modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from geoalchemy2 import Geometry  # Import geoalchemy2 for PostGIS types

from alembic import context

# Import our config and models
from backend.app.core.config import settings
from backend.app.db.base import Base
# Make sure to import all models so Alembic can find them!
from backend.app.models.db.camera import Camera
from backend.app.models.db.watchlist import Watchlist
from backend.app.models.db.alert import Alert
from backend.app.models.db.user import User
from backend.app.models.db.audit_log import AuditLog
# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the sqlalchemy.url from our application settings dynamically
# Use effective_database_url which falls back to postgres_* parts when DATABASE_URL is not set
config.set_main_option("sqlalchemy.url", settings.effective_database_url)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in ["spatial_ref_sys", "layer", "topology", "state", "place_lookup", "tract", "tabblock20", "geocode_settings", "street_type_lookup", "place", "bg", "direction_lookup", "pagc_lex", "loader_lookuptables", "secondary_unit_lookup", "county", "zcta5", "addrfeat", "faces", "pagc_gaz", "zip_state", "county_lookup", "state_lookup", "zip_state_loc", "zip_lookup_all", "loader_platform", "zip_lookup_base", "cousub", "zip_lookup", "loader_variables", "countysub_lookup", "featnames", "edges", "tabblock", "pagc_rules", "addr", "geocode_settings_default"]:
        return False
    return True

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, 
            target_metadata=target_metadata,
            include_object=include_object
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
