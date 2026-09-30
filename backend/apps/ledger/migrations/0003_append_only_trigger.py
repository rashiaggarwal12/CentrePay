"""Enforce append-only at the database, not just in LedgerEntry.save().

A queryset .update()/.delete() or a manual SQL fix bypasses model methods; this
trigger doesn't. Postgres only (SQLite dev/test runs rely on the model guard).
"""

from django.db import migrations

CREATE = """
CREATE OR REPLACE FUNCTION ledger_entry_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_ledgerentry is append-only: UPDATE and DELETE are blocked';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entry_append_only
BEFORE UPDATE OR DELETE ON ledger_ledgerentry
FOR EACH ROW EXECUTE FUNCTION ledger_entry_append_only();
"""

DROP = """
DROP TRIGGER IF EXISTS ledger_entry_append_only ON ledger_ledgerentry;
DROP FUNCTION IF EXISTS ledger_entry_append_only();
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CREATE)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(DROP)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_initial")]

    operations = [migrations.RunPython(forwards, backwards)]
