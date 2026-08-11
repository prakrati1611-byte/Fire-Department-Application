import pandas as pd
import mysql.connector
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score
import joblib
import numpy as np

# ── DB Connection ──────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="Root",
        database="fire"
    )

# ── Load Data ──────────────────────────────────────────────
conn = get_db()
cursor = conn.cursor(dictionary=True)
cursor.execute("""
    SELECT
        a.app_id,
        a.inspection_score,
        COUNT(DISTINCT i.inspection_id)                 AS total_inspections,
        COALESCE(DATEDIFF(NOW(), MAX(i.date)), 0)       AS days_since_inspection,
        COUNT(CASE WHEN fu.status='Pending' THEN 1 END) AS pending_followups,
        MAX(CASE WHEN n.status='Active' THEN 1 ELSE 0 END) AS noc_active,
        COUNT(ah.history_id)                            AS status_changes
    FROM applications a
    LEFT JOIN inspections i          ON a.app_id = i.app_id
    LEFT JOIN follow_ups fu          ON a.app_id = fu.app_id
    LEFT JOIN nocs n                 ON a.app_id = n.app_id
    LEFT JOIN application_history ah ON a.app_id = ah.app_id
    WHERE a.inspection_score IS NOT NULL
    GROUP BY a.app_id, a.inspection_score
""")
df = pd.DataFrame(cursor.fetchall()).fillna(0)
cursor.close()
conn.close()

print(f"\nTotal rows fetched: {len(df)}")

if len(df) == 0:
    print("No data found. Make sure inspection_score is filled in applications table.")
    exit()

if len(df) < 30:
    print(f"Warning: Only {len(df)} rows. Model accuracy will be unreliable.")
    print("Try adding more inspection records with scores before training.")

# ── Create Risk Label ──────────────────────────────────────
df['inspection_score'] = pd.to_numeric(
    df['inspection_score'], errors='coerce').fillna(50)

df['risk_label'] = pd.cut(
    df['inspection_score'],
    bins=[0, 40, 70, 100],
    labels=['High', 'Medium', 'Low'],
    include_lowest=True
)

df = df.dropna(subset=['risk_label'])
print(f"Rows after label assignment: {len(df)}")
print("\nClass distribution:")
print(df['risk_label'].value_counts())
print(df['risk_label'].value_counts(normalize=True).mul(100).round(1).astype(str) + '%')

# ── Check class imbalance ──────────────────────────────────
min_class = df['risk_label'].value_counts().min()
if min_class < 5:
    print(f"\nWarning: Smallest class has only {min_class} samples.")
    print("Results will be unreliable. Add more data or use class_weight.")

# ── Features and Target ────────────────────────────────────
features = ['total_inspections', 'days_since_inspection',
            'pending_followups', 'noc_active', 'status_changes']

X = df[features].values.astype(float)
y = df['risk_label']

# ── Train Test Split ───────────────────────────────────────
try:
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=0.2,
        random_state=42,
        stratify=y        # ensures all 3 classes in both splits
    )
except ValueError:
    # fallback if any class has too few samples for stratify
    print("Warning: Stratify failed due to small class size. Using random split.")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42)

print(f"\nTraining samples : {len(X_train)}")
print(f"Testing samples  : {len(X_test)}")

# ── Train Model ────────────────────────────────────────────
model = GradientBoostingClassifier(
    n_estimators=50,       # reduced to prevent overfitting on small data
    max_depth=3,           # shallow trees generalise better
    min_samples_leaf=3,    # each leaf needs at least 3 records
    learning_rate=0.1,
    random_state=42
)
model.fit(X_train, y_train)

# ── Evaluate ───────────────────────────────────────────────
y_pred = model.predict(X_test)

print("\n" + "="*50)
print("  MODEL EVALUATION — Risk Scorer")
print("="*50)
print(f"\nTest Accuracy : {accuracy_score(y_test, y_pred)*100:.1f}%")

# Cross-validation — more reliable than single split
try:
    cv = min(5, min_class)   # can't have more folds than smallest class size
    cv_scores = cross_val_score(model, X, y, cv=cv, scoring='accuracy')
    print(f"Cross-val ({cv}-fold): {cv_scores.mean()*100:.1f}% ± {cv_scores.std()*100:.1f}%")
except Exception as e:
    print(f"Cross-val skipped: {e}")

print("\nClassification Report:")
print(classification_report(y_test, y_pred, zero_division=0))

print("Confusion Matrix:")
labels = ['High', 'Medium', 'Low']
cm = confusion_matrix(y_test, y_pred, labels=labels)
print(pd.DataFrame(cm, index=labels, columns=labels))

# ── Feature Importance ─────────────────────────────────────
print("\nFeature Importances:")
imp = pd.Series(model.feature_importances_, index=features).sort_values(ascending=False)
for feat, score in imp.items():
    bar = '█' * int(score * 50)
    print(f"  {feat:<28} {bar} {score:.3f}")

# ── Save Model ─────────────────────────────────────────────
joblib.dump({'model': model, 'features': features}, 'ml/risk_scorer.pkl')
print("\nSaved ml/risk_scorer.pkl")
print("Done.")