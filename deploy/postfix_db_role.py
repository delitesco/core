"""Create or update the database role Postfix connects with.

Postfix faces the internet and only asks which domains mail is accepted for, so it
gets a role that can read those columns and nothing else, not the app's own user.
"""

import os

from psycopg2 import sql

from app.db import engine

ROLE = "postfix"

password = os.environ["POSTFIX_DB_PASSWORD"]

connection = engine.raw_connection()
with connection.cursor() as cursor:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
    if not cursor.fetchone():
        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(ROLE)))
    cursor.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(ROLE), sql.Literal(password)
        )
    )
    cursor.execute(
        sql.SQL("GRANT SELECT (domain, verified) ON custom_domain TO {}").format(
            sql.Identifier(ROLE)
        )
    )
    cursor.execute(
        sql.SQL("GRANT SELECT (domain) ON public_domain TO {}").format(
            sql.Identifier(ROLE)
        )
    )
connection.commit()
connection.close()
print(f"Database role {ROLE} can read the mail domains")
