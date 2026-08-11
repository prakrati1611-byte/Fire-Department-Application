import pandas as pd
import mysql.connector
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.feature_extraction.text import TfidfVectorizer
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
    SELECT a.app_id, a.type, a.building_type, a.location,
           a.description, a.priority_level, a.status,
           COALESCE(ic.overall_score, 0) as checklist_score,
           COALESCE(fl.amount, 0) as fee_amount
    FROM applications a
    LEFT JOIN inspection_checklists ic ON a.app_id = ic.app_id
    LEFT JOIN fee_ledger fl ON a.app_id = fl.app_id
    WHERE a.type IS NOT NULL
""")
apps = pd.DataFrame(cursor.fetchall())
cursor.close()
conn.close()

print(f"Total rows loaded: {len(apps)}")

# ── Normalize building_type to canonical categories ────────
# This is your most predictive feature — map free text to clean groups
BUILDING_MAP = {
    'Residential': 'Residential', 'Apartment': 'Residential', 'House': 'Residential',
    'Commercial':  'Commercial',  'Shop': 'Commercial',  'Office': 'Commercial',
    'Retail': 'Commercial',
    'Industrial':  'Industrial',  'Factory': 'Industrial', 'Warehouse': 'Industrial',
    'Logistics': 'Industrial',
    'Healthcare':  'Healthcare',  'Hospital': 'Healthcare', 'Clinic': 'Healthcare',
    'Educational': 'Educational', 'School': 'Educational', 'College': 'Educational',
    'Mall': 'Commercial', 'Mixed': 'Mixed',
}

def normalize_building_type(val):
    if pd.isna(val) or str(val).strip() == '':
        return 'Unknown'
    val = str(val).strip()
    for key, canonical in BUILDING_MAP.items():
        if key.lower() in val.lower():
            return canonical
    return 'Unknown'

apps['building_type_clean'] = apps['building_type'].apply(normalize_building_type)

print("\nBuilding type distribution after normalization:")
print(apps['building_type_clean'].value_counts())

# ── Drop rows with no signal (no building_type AND no description) ──
apps['has_signal'] = (
    (apps['building_type_clean'] != 'Unknown') |
    (apps['description'].fillna('').str.strip().str.len() > 5)
)
n_dropped = (~apps['has_signal']).sum()
if n_dropped > 0:
    print(f"\n⚠️  Dropping {n_dropped} rows with no building_type and no description.")
apps = apps[apps['has_signal']].reset_index(drop=True)
print(f"Usable rows: {len(apps)}")

print("\nClass distribution:")
print(apps['type'].value_counts())

# ── Encode features ────────────────────────────────────────
le_building = LabelEncoder()
le_priority = LabelEncoder()
le_status   = LabelEncoder()
le_type     = LabelEncoder()

apps['building_enc']    = le_building.fit_transform(apps['building_type_clean'])
apps['priority_enc']    = le_priority.fit_transform(apps['priority_level'].fillna('Normal'))
apps['status_enc']      = le_status.fit_transform(apps['status'].fillna('Pending'))
apps['checklist_score'] = pd.to_numeric(apps['checklist_score'], errors='coerce').fillna(0)
apps['fee_amount']      = pd.to_numeric(apps['fee_amount'],      errors='coerce').fillna(0)

# ── TF-IDF on description (bigrams, English stopwords) ────
descriptions = apps['description'].fillna('').astype(str).str.strip()
N_TFIDF = min(30, max(5, len(apps) // 4))

if descriptions.str.len().sum() > 10:
    tfidf = TfidfVectorizer(max_features=N_TFIDF, stop_words='english', ngram_range=(1, 2))
    desc_features = tfidf.fit_transform(descriptions).toarray()
    print(f"\n✅ TF-IDF features: {N_TFIDF}")
else:
    tfidf = None
    desc_features = np.zeros((len(apps), 0))
    print("\n⚠️  No description text found.")

# ── Feature matrix ─────────────────────────────────────────
X = np.hstack([
    apps[['building_enc', 'priority_enc', 'status_enc',
          'checklist_score', 'fee_amount']].values.astype(float),
    desc_features
])
y = le_type.fit_transform(apps['type'])

print(f"\nFeature matrix: {X.shape}")
print(f"Classes ({len(le_type.classes_)}): {le_type.classes_}")

# ── Scale ──────────────────────────────────────────────────
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# ── Feature importance preview ─────────────────────────────
rf_quick = RandomForestClassifier(n_estimators=100, random_state=42)
rf_quick.fit(X_scaled, y)
feature_names = ['building_type', 'priority', 'status', 'checklist_score', 'fee_amount'] + \
                ([f'tfidf_{w}' for w in tfidf.get_feature_names_out()] if tfidf else [])
importances = pd.Series(rf_quick.feature_importances_, index=feature_names).sort_values(ascending=False)
print("\nTop 10 most predictive features:")
print(importances.head(10).round(3).to_string())

# ── Cross-validation ───────────────────────────────────────
n_splits = min(5, apps['type'].value_counts().min())
print(f"\n── {n_splits}-fold Stratified Cross-Validation ──────────────")

candidates = {
    "Logistic Regression": LogisticRegression(max_iter=1000, C=0.5, random_state=42),
    "SVM (RBF)":           SVC(kernel='rbf', C=1.0, probability=True, random_state=42),
    "Gradient Boosting":   GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=42),
    "Random Forest":       RandomForestClassifier(n_estimators=200, max_depth=6,
                               min_samples_leaf=2, random_state=42),
}

cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
best_name, best_score, best_model = None, -1, None
for name, clf in candidates.items():
    scores = cross_val_score(clf, X_scaled, y, cv=cv, scoring='accuracy')
    print(f"  {name:25s}  acc = {scores.mean():.3f} ± {scores.std():.3f}")
    if scores.mean() > best_score:
        best_score, best_name, best_model = scores.mean(), name, clf

baseline = 1 / len(le_type.classes_)
print(f"\n✅ Best: {best_name}  (CV acc = {best_score:.3f})")
print(f"   Random baseline:          {baseline:.3f}")
print(f"   Improvement over baseline: {best_score / baseline:.1f}×")

# ── Train final model on ALL data ─────────────────────────
best_model.fit(X_scaled, y)

# ── Save ───────────────────────────────────────────────────
joblib.dump({
    'model':        best_model,
    'scaler':       scaler,
    'tfidf':        tfidf,
    'le_building':  le_building,
    'le_priority':  le_priority,
    'le_status':    le_status,
    'le_type':      le_type,
    'building_map': BUILDING_MAP,
    'has_tfidf':    tfidf is not None,
    'model_name':   best_name,
    'cv_accuracy':  best_score,
}, 'ml/classifier.pkl')

print("✅ Saved classifier.pkl")