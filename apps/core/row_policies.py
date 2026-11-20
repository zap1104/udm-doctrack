"""Install office-scoped policies for one separately provisioned PostgreSQL login."""

import re
from pathlib import Path

from django.core.management.base import CommandError
from django.db import transaction
from psycopg import sql

from .public_database import managed_tables

# Policy expressions below contain only repository-owned identifiers.
ROOTS = {
    "tracking_trackingrecord": "tracking",
    "tracking_externaltrackingrecord": "external_record",
    "documents_document": "repository",
}
CHILDREN = {
    "tracking_trackingrecord": {
        "tracking_trackingrecord_document_types": "trackingrecord_id",
        "tracking_routingstep": "record_id",
        "tracking_recordactivity": "record_id",
        "tracking_attachmentgroup": "record_id",
        "tracking_attachment": "record_id",
        "tracking_recordaccessgrant": "record_id",
    },
    "tracking_externaltrackingrecord": {
        "tracking_externaltrackingrecord_document_types": "externaltrackingrecord_id",
        "tracking_externalroutingstep": "record_id",
        "tracking_externalactivity": "record_id",
        "tracking_externalattachmentgroup": "record_id",
        "tracking_externalattachment": "record_id",
        "tracking_externalactionrevision": "record_id",
        "tracking_externalrecordaccessgrant": "record_id",
    },
    "documents_document": {
        "documents_document_document_types": "document_id",
        "documents_document_tags": "document_id",
        "documents_externaldocumentoffice": "document_id",
        "documents_deletionreview": "document_id",
        "documents_documentfile": "document_id",
        "documents_documentmetadata": "document_id",
        "documents_metadatasuggestion": "document_id",
        "documents_documentaccessgrant": "document_id",
        "documents_tagreview": "document_id",
        "documents_tagprediction": "document_id",
        "documents_tagcontribution": "document_id",
        "documents_snapshotsource": "document_id",
        "documents_extractionrun": "document_id",
    },
}
CONTENT_TABLES = {
    "tracking_attachment",
    "tracking_externalattachment",
    "documents_documentfile",
    "documents_extractionrun",
    "documents_metadatasuggestion",
    "documents_tagprediction",
}
INDIRECT = {
    "documents_documentfileversion": ("documents_documentfile", "document_file_id"),
    "documents_extractionunit": ("documents_extractionrun", "run_id"),
    "documents_searchchunk": ("documents_extractionunit", "unit_id"),
    "documents_inferenceintent": ("documents_extractionrun", "run_id"),
    "documents_tagapplicationintent": ("documents_metadatasuggestion", "suggestion_id"),
}


def root_predicate(table, alias, operation):
    return f"doctrack_private.{ROOTS[table]}({alias}, '{operation}')"


def locked(table, alias):
    return f"current_setting('doctrack.locked_row', true) = '{table}:' || {alias}.id"


def read_scope(table, alias, operation="view_record"):
    scope = root_predicate(table, alias, operation)
    if table == "documents_document":
        return f"({scope} OR doctrack_private.filing({alias}))"
    return scope


def write_scope(table, alias):
    scope = root_predicate(table, alias, "write")
    if table == "documents_document":
        return f"({scope} OR doctrack_private.filing({alias}))"
    return f"({scope} OR {locked(table, alias)})"


def parent_scope(table, child, column, condition):
    return f"EXISTS (SELECT FROM public.{table} p WHERE p.id = {child}.{column} AND {condition})"


def predicates():
    for table in ROOTS:
        access = read_scope(table, table)
        create = root_predicate(table, table, "create")
        if table == "documents_document":
            create = f"({create} OR doctrack_private.filing({table}))"
        # Sharing authority may lock the source slip without permission to edit its contents.
        lock = write_scope(table, table)
        if table != "documents_document":
            lock = f"({lock} OR {root_predicate(table, table, 'lock')})"
        if table == "tracking_trackingrecord":
            lock = f"({lock} OR {root_predicate(table, table, 'share')})"
        yield table, access, create, lock, write_scope(table, table), "false"
        for child, column in CHILDREN[table].items():
            access = parent_scope(
                table,
                child,
                column,
                read_scope(table, "p", "view_content" if child in CONTENT_TABLES else "view_record"),
            )
            condition = (
                root_predicate(table, "p", "share") if child.endswith("accessgrant") else write_scope(table, "p")
            )
            modify = parent_scope(table, child, column, condition)
            insert = modify
            if child == "tracking_recordactivity":
                insert = f"({modify} OR ({access} AND event IN ('VIEWED','PRINTED') AND actor_id = nullif(current_setting('doctrack.actor_id', true),'')::bigint))"
            yield child, access, insert, modify, modify, modify
    actor_id = "nullif(current_setting('doctrack.actor_id',true),'')::bigint"
    owner = f"owner_id={actor_id} AND EXISTS (SELECT FROM public.accounts_user u WHERE u.id=owner_id AND u.is_active AND u.office_id IS NOT DISTINCT FROM owner_office_id_snapshot)"
    yield "core_uploaddraft", owner, owner, owner, owner, owner
    staged = "EXISTS (SELECT FROM public.core_uploaddraft d WHERE d.id=core_stagedupload.draft_id)"
    yield "core_stagedupload", staged, staged, staged, staged, staged
    for table in ("core_notificationread", "core_notificationpreference", "documents_saveddocumentview"):
        column = "owner_id" if table == "documents_saveddocumentview" else "user_id"
        own = f"{column}={actor_id}"
        yield table, own, own, own, own, own
    for child, (parent, column) in INDIRECT.items():
        visible = f"EXISTS (SELECT FROM public.{parent} p WHERE p.id = {child}.{column})"
        # Inherit write authority as well as visibility through the actual FK chain.
        ancestor, original_column = parent, column
        joins = f"public.{ancestor} q0"
        depth = 0
        while ancestor in INDIRECT:
            ancestor, fk = INDIRECT[ancestor]
            joins += f" JOIN public.{ancestor} q{depth + 1} ON q{depth + 1}.id=q{depth}.{fk}"
            depth += 1
        root = next(key for key, children in CHILDREN.items() if ancestor in children)
        joins += f" JOIN public.{root} r ON r.id=q{depth}.{CHILDREN[root][ancestor]}"
        modify = f"EXISTS (SELECT FROM {joins} WHERE q0.id={child}.{original_column} AND ({root_predicate(root, 'r', 'write')} OR doctrack_private.filing(r)))"
        yield child, visible, modify, modify, modify, modify


def install_policies(connection, role):
    if connection.vendor != "postgresql" or not re.fullmatch(r"doctrack_web(?:_[a-z0-9_]{1,40})?", role):
        raise CommandError("Use PostgreSQL and a dedicated login named doctrack_web or doctrack_web_<suffix>.")
    identifier = sql.Identifier(role)
    with transaction.atomic(using=connection.alias), connection.cursor() as cur:
        cur.execute(
            """
            SELECT rolcanlogin AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls)
               AND current_user <> rolname AND NOT EXISTS (SELECT FROM pg_auth_members WHERE member = r.oid)
               AND NOT EXISTS (SELECT FROM pg_shdepend WHERE refclassid='pg_authid'::regclass
                               AND refobjid=r.oid AND deptype='o')
               AND NOT has_schema_privilege(oid, 'public', 'CREATE')
               AND NOT has_database_privilege(oid, current_database(), 'CREATE')
            FROM pg_roles r WHERE rolname=%s
        """,
            [role],
        )
        if cur.fetchone() != (True,):
            raise CommandError(
                "The web login must exist, own no objects and have no elevated roles or CREATE privileges."
            )
        tables = managed_tables(connection)
        protected = list(predicates())
        cur.execute(
            """
            SELECT tablename, policyname FROM pg_policies
            WHERE schemaname='public' AND tablename=ANY(%s)
              AND ('public'=ANY(roles) OR %s=ANY(roles))
              AND policyname <> ALL(%s)
        """,
            [
                [row[0] for row in protected],
                role,
                ["doctrack_web_" + op for op in ("select", "insert", "update", "delete")],
            ],
        )
        if cur.fetchone():
            raise CommandError("Review existing policies applying to the web login before installation.")
        cur.execute(
            """
            SELECT EXISTS (SELECT FROM pg_class WHERE relnamespace='public'::regnamespace
                AND relname=ANY(%s) AND has_table_privilege(%s, oid, 'TRUNCATE,TRIGGER,REFERENCES'))
        """,
            [tables, role],
        )
        if cur.fetchone()[0]:
            raise CommandError("Remove elevated table privileges from the web login before installation.")
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("CREATE SCHEMA IF NOT EXISTS doctrack_private")
        cur.execute("SELECT nspowner = current_user::regrole FROM pg_namespace WHERE nspname='doctrack_private'")
        if cur.fetchone() != (True,):
            raise CommandError("The migration login must own the private policy schema.")
        cur.execute("REVOKE ALL ON SCHEMA doctrack_private FROM PUBLIC")
        cur.execute(Path(__file__).with_name("sql").joinpath("request_rows.sql").read_text(encoding="utf-8-sig"))
        cur.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA doctrack_private FROM PUBLIC")
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public, doctrack_private TO {}").format(identifier))
        cur.execute(sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA doctrack_private TO {}").format(identifier))
        for table, read, insert, update, check, delete in protected:
            target = sql.Identifier("public", table)
            cur.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(target))
            for command, clauses in (
                ("SELECT", f"USING ({read})"),
                ("INSERT", f"WITH CHECK ({insert})"),
                ("UPDATE", f"USING ({update}) WITH CHECK ({check})"),
                ("DELETE", f"USING ({delete})"),
            ):
                name = sql.Identifier("doctrack_web_" + command.lower())
                cur.execute(sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(name, target))
                cur.execute(
                    sql.SQL("CREATE POLICY {} ON {} FOR {} TO {} {}").format(
                        name, target, sql.SQL(command), identifier, sql.SQL(clauses)
                    )
                )
        # Account/session infrastructure is used before authentication. Django owns
        # account administration. The web connection is a trusted backend secret.
        for table in tables:
            if table == "django_migrations":
                continue
            privileges = (
                "SELECT, INSERT"
                if table in {"core_auditlog", "core_filelifecycleevent"}
                else "SELECT, INSERT, UPDATE, DELETE"
            )
            cur.execute(
                sql.SQL("GRANT {} ON {} TO {}").format(sql.SQL(privileges), sql.Identifier("public", table), identifier)
            )
        cur.execute(
            """
            SELECT DISTINCT s.relname FROM pg_class s JOIN pg_depend d ON d.objid=s.oid
            AND d.classid='pg_class'::regclass AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i')
            JOIN pg_class t ON t.oid=d.refobjid WHERE s.relkind='S' AND s.relnamespace='public'::regnamespace
            AND t.relnamespace='public'::regnamespace AND t.relname=ANY(%s)
        """,
            [[t for t in tables if t != "django_migrations"]],
        )
        for (seq,) in cur.fetchall():
            cur.execute(sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(sql.Identifier("public", seq), identifier))
