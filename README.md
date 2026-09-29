# Fire Department Management System

A full-stack fire-safety NOC (No Objection Certificate) management platform with three role-based portals — **Applicant**, **Officer**, and **Admin** — and an integrated machine learning layer that scores every application for risk and predicts its type, so officers can prioritize review instead of working strictly by submission order.

Built as a 4-person Minor Project under the guidance of Prof. Harshit Bharti.

## Why this exists

Manual NOC review is slow and unprioritized — every application gets treated the same regardless of actual risk. This system digitizes the full application lifecycle (submission → inspection → decision → NOC issuance → renewal) and layers ML risk scoring on top so the applications most likely to need attention surface first.

## Features

**Applicant portal**
- Submit and track NOC applications, edit/withdraw pending ones
- Upload supporting documents
- View assigned inspections and inspection history
- Fee ledger and NOC download
- In-app messaging with assigned officer, notifications
- Personal insights dashboard

**Officer portal**
- Dashboard of assigned applications with ML-generated risk/priority signals
- Approve/reject applications with reasoning
- Schedule and update inspections against a checklist (extinguishers, alarms, sprinklers, smoke detectors)
- Real-time alerts feed, analytics view
- Messaging with applicants

**Admin portal**
- User management (add officers, suspend/activate accounts, change roles)
- Full application, fee, and NOC oversight (mark paid, waive fees, revoke NOCs)
- Org-wide announcements
- Audit log with CSV export
- Configurable reports (officer performance, application volume, etc.) with CSV export
- System settings: fee schedule, NOC validity/renewal rules, notification toggles, session/security policy

## Machine Learning Pipeline

Two models run on every application:

- **`RandomForestClassifier`** — predicts application type from building type, priority level, inspection checklist score, and a TF-IDF vector of the application description
- **`GradientBoostingClassifier`** — scores risk using engineered features aggregated per application: total inspections, days since last inspection, checklist score, count of failed safety checks, pending follow-ups, active-NOC flag, and number of status changes

`run_ml_on_application(app_id)` pulls this feature set with a single aggregating SQL query (joining `applications`, `inspections`, `inspection_checklists`, `follow_ups`, `nocs`, and `application_history`), runs both models, and writes `ml_risk_level`, `ml_risk_score`, `ml_predicted_type`, and `ml_confidence` back onto the application row — which is what powers the risk badge on the officer dashboard.

`run_backfill.py` is a standalone script to score any existing applications that don't yet have a risk level.

> Add your held-out accuracy/precision/recall for both models here, plus the training set size.

## Tech Stack

| Layer      | Technology 
| Backend    | Python, Flask 
| Database   | MySQL 
| ML         | scikit-learn, joblib 
| Frontend   | HTML, CSS, JavaScript (Jinja templates) 
| Deployment | Gunicorn 

## Getting Started

### Prerequisites
- Python 3.x
- MySQL Server
- pip

### Installation

```bash
git clone https://github.com/prakrati1611-byte/Fire-Department.git
cd Fire-Department
pip install -r requirements.txt
```

### Configuration

```bash
MYSQLHOST=localhost
MYSQLUSER=root
MYSQLPASSWORD=root
MYSQLDATABASE=fire
MYSQLPORT=3306
PORT=5000
```

Create a `.env` file in the project root with these values.

### Database

Set up a MySQL database named `fire` with the schema this app expects — `users`, `applications`, `inspections`, `inspection_checklists`, `follow_ups`, `nocs`, `application_history`, `audit_log`, and settings tables.

Trained model files are expected at `ml/classifier.pkl` and `ml/risk_scorer.pkl`.

### Run

```bash
python app.py
```

Visit `http://localhost:5000`.

### Backfilling ML scores

```bash
python run_backfill.py
```

## Roles

| Role      | Access 
| Applicant | Submit/track own applications 
| Officer   | Review, inspect, and decide on assigned applications 
| Admin     | Full system oversight — users, fees, NOCs, reports, settings 

## Security note

- Passwords are stored and compared in plaintext. Use `werkzeug.security.generate_password_hash` / `check_password_hash` instead.
- `app.secret_key` is hardcoded in `app.py`. Move it to an environment variable and rotate it.

## Team

## Team

Built as a 4-person Minor Project under the guidance of Prof. Harshit Bharti. Role breakdown:

- **Prakrati (prakrati1611-byte)** — Frontend (all three portals), machine learning pipeline (RandomForest + GradientBoosting), backend (Flask, MySQL schema, ML integration)

