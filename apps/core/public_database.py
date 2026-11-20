"""Remove Supabase client privileges from tables managed by this Django installation."""

from django.apps import apps
from django.db import connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from psycopg import sql


def managed_tables(connection, registry=apps):
    names = {
        m._meta.db_table
        for m in registry.get_models(include_auto_created=True)
        if m._meta.managed and not m._meta.proxy
    }
    names.update({MigrationRecorder.Migration._meta.db_table, "doctrack_cache"})
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relname FROM pg_catalog.pg_class
            WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r','p')
              AND relname = ANY(%s) ORDER BY relname
        """,
            [sorted(names)],
        )
        return [row[0] for row in cursor.fetchall()]


def close_api_grants(connection, registry=apps):
    if connection.vendor != "postgresql":
        return
    tables = managed_tables(connection, registry)
    with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')")
        roles = [sql.Identifier(name) for (name,) in cursor.fetchall()]
        if not roles or not tables:
            return
        grantees = sql.SQL(", ").join([sql.SQL("PUBLIC"), *roles])
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        # Explicit column grants survive REVOKE ALL ON TABLE.
        cursor.execute(
            """
            SELECT c.relname, array_agg(a.attname ORDER BY a.attnum)
            FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE c.relnamespace='public'::regnamespace AND c.relname=ANY(%s)
              AND NOT a.attisdropped AND a.attacl IS NOT NULL AND a.attnum > 0
            GROUP BY c.relname
        """,
            [tables],
        )
        for name, columns in cursor.fetchall():
            cursor.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ({}) ON TABLE {} FROM {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier("public", name), grantees
                )
            )
        cursor.execute(
            sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                sql.SQL(", ").join(sql.Identifier("public", name) for name in tables), grantees
            )
        )
        cursor.execute(
            """
            SELECT DISTINCT n.nspname, s.relname FROM pg_class s
            JOIN pg_namespace n ON n.oid=s.relnamespace
            JOIN pg_depend d ON d.classid='pg_class'::regclass AND d.objid=s.oid AND d.deptype IN ('a','i')
            JOIN pg_class t ON t.oid=d.refobjid AND d.refclassid='pg_class'::regclass
            WHERE s.relkind='S' AND t.relnamespace='public'::regnamespace AND t.relname = ANY(%s)
        """,
            [tables],
        )
        sequences = [sql.Identifier(schema, name) for schema, name in cursor.fetchall()]
        if sequences:
            cursor.execute(sql.SQL("REVOKE ALL ON SEQUENCE {} FROM {}").format(sql.SQL(", ").join(sequences), grantees))


def after_migrations(sender, using, **kwargs):
    close_api_grants(connections[using])
