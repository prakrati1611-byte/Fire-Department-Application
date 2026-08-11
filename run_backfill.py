# run_backfill.py
from app import app, get_db, run_ml_on_application

with app.app_context():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    
    cur.execute("SELECT COUNT(*) AS n FROM applications")
    print(f"Total applications in DB: {cur.fetchone()['n']}")
    
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE ml_risk_level IS NULL")
    print(f"Applications with NULL ml_risk_level: {cur.fetchone()['n']}")
    
    cur.execute("SELECT app_id, ml_risk_level FROM applications LIMIT 10")
    rows = cur.fetchall()
    print("Sample rows:", rows)
    
    cur.execute("SELECT app_id FROM applications WHERE ml_risk_level IS NULL")
    ids = [r['app_id'] for r in cur.fetchall()]
    cur.close(); conn.close()

    print(f"Scoring {len(ids)} applications...")
    for aid in ids:
        run_ml_on_application(aid)
        print(f"  done: app_id {aid}")
    print("All done.")