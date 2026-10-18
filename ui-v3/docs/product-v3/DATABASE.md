# V3 SQLite schema v1

`envmanager_v3/database.py` uses SQLite `PRAGMA user_version=1`. Version 1
retains the original four tables (`settings`, `previews`, `executions`,
`history`), their column order and constraints, and all existing record values.
It adds no Service or Execution behavior and no environment/Registry writes.

## Opening and migration

- New files and empty SQLite databases create the four tables and stamp v1 in
  one transaction. Normal connections retain WAL, synchronous FULL and foreign
  key enforcement.
- Existing files are inspected through a read-only SQLite connection before a
  writable connection is opened or journal mode is changed. Versions greater
  than 1 raise `DatabaseSchemaError`; unknown negative versions are also refused.
  No downgrade or speculative later migration is implemented.
- Existing v0 and v1 schemas must match the four-table column, primary/unique
  key and foreign key contracts. Missing/changed tables or columns, unexpected
  tables/views/triggers, or changed key constraints are refused. Corrupt files
  surface SQLite errors. There is no automatic table repair or data deletion.
- For valid legacy v0 databases, `BEGIN IMMEDIATE` excludes other writers,
  then version/structure are checked again. A separate read-only connection
  uses SQLite's backup API to include committed WAL records. A completed,
  integrity-checked backup is required before stamping v1. The stamp commits
  transactionally; WAL mode is set only after successful initialization.
- A normal v1 reopen validates the schema without generating another backup.
  Failures close the initializing connection. Migration failures roll back the
  schema/version transaction and keep a completed backup. A failed backup copy
  removes its incomplete output and prevents migration.

Read-only inspection honors live WAL data (`immutable=1` is deliberately not
used). SQLite may update transient `-shm` read-lock metadata when reading a live
WAL database. Refusal does not issue database writes or change database/WAL
content, schema, user_version or journal mode; `-shm` byte identity is not a
storage-data guarantee.

## Backup and recovery

Backups are independent SQLite files beside the source, named
`<database filename>.schema-v0-<unique suffix>.bak`. Existing backup files are
never overwritten. `Database.backup_path` identifies the backup created by that
initialization, or is `None` when no migration backup was needed. The backup
retains `user_version=0` and is a pre-upgrade recovery copy, not a rolling backup
of subsequent product activity.

For manual recovery, stop all product processes and close all SQLite connections
first. Preserve the current database and any remaining `-wal`/`-shm` files
together before replacing anything. Restore the chosen `.bak` as the database
file with no stale sidecars at the restored path. Reopening with this product
validates the restored v0 database, makes a new backup and stamps v1 again.
No automatic restore is performed.

## Verification

Run from `H:\Yuri368\环境变量配置V3`:

```powershell
& 'H:\Yuri368\环境变量配置V3-git\.venv\Scripts\python.exe' -m unittest tests_v3.test_database -v
```

Tests use temporary persistent SQLite files, including actual close/reopen,
legacy records in all four tables, backup contents, committed live-WAL data,
future-version refusal with unchanged database/WAL bytes, malformed schemas,
backup failure and injected transactional migration failures. They do not open
the owner's live product database.
