"""
migrate_db.py — Migração do banco cobrax.db para o novo schema.

Mudanças aplicadas:
  units  → ADD COLUMN last_notified_at DATETIME
  debts  → Remover colunas `amount` e `last_notified_at`
            (via recreate, já que SQLite não suporta DROP COLUMN em versões antigas)

Execute com: uv run python migrate_db.py
"""
import sqlite3
import sys

DB_PATH = "data/cobrax.db"

def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("=== Cobrax DB Migration ===\n")

    # ── 1. units: adicionar last_notified_at ──────────────────────────────────
    if not column_exists(cur, "units", "last_notified_at"):
        print("[units] Adicionando coluna: last_notified_at...")
        cur.execute("ALTER TABLE units ADD COLUMN last_notified_at DATETIME;")
        conn.commit()
        print("[units] ✓ Coluna last_notified_at adicionada (todos os valores = NULL).")
    else:
        print("[units] ✓ Coluna last_notified_at já existe — nenhuma ação necessária.")

    # ── 2. debts: recriar sem `amount` e sem `last_notified_at` ──────────────
    needs_recreate = column_exists(cur, "debts", "amount") or column_exists(cur, "debts", "last_notified_at")

    if needs_recreate:
        print("\n[debts] Recriando tabela sem as colunas `amount` e `last_notified_at`...")
        
        # Etapa A: criar tabela temporária com o schema novo
        cur.execute("""
            CREATE TABLE debts_new (
                id INTEGER NOT NULL,
                unit_id INTEGER NOT NULL,
                doc_number VARCHAR NOT NULL,
                due_date DATE NOT NULL,
                status VARCHAR,
                PRIMARY KEY (id),
                FOREIGN KEY(unit_id) REFERENCES units (id)
            );
        """)
        
        # Etapa B: copiar dados preservando apenas as colunas necessárias
        cur.execute("""
            INSERT INTO debts_new (id, unit_id, doc_number, due_date, status)
            SELECT id, unit_id, doc_number, due_date, status FROM debts;
        """)
        
        # Etapa C: trocar as tabelas
        cur.execute("DROP TABLE debts;")
        cur.execute("ALTER TABLE debts_new RENAME TO debts;")
        
        # Etapa D: recriar os índices
        cur.execute("CREATE INDEX IF NOT EXISTS ix_debts_id ON debts (id);")
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_debts_doc_number ON debts (doc_number);")
        
        conn.commit()
        print("[debts] ✓ Tabela recriada com sucesso. Dados preservados.")
    else:
        print("\n[debts] ✓ Colunas `amount` e `last_notified_at` já foram removidas — nenhuma ação necessária.")

    # ── 3. Verificação final ──────────────────────────────────────────────────
    print("\n=== Schema Final ===")
    for table in ["units", "debts"]:
        cur.execute(f"PRAGMA table_info({table})")
        cols = cur.fetchall()
        print(f"\n[{table}]")
        for col in cols:
            print(f"  {col[1]:25s} {col[2]}")

    conn.close()
    print("\n✓ Migração concluída.")

if __name__ == "__main__":
    migrate()
