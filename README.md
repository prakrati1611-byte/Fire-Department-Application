# Fire-Department
Fire Department Management System — ML-Integrated Risk Assessment
A Flask + MySQL web application that automates fire-safety NOC (No Objection Certificate) issuance tracking and follow-ups, replacing a fully manual review process. A machine learning layer flags high-risk applications so reviewers can prioritize what matters first.
Built as a 4-person team project under the guidance of Prof. Harshit Bharti.
Overview
Fire departments handling NOC applications typically rely on manual, paper-driven review — slow, inconsistent, and hard to prioritize. This project digitizes the workflow end-to-end and layers in ML-based risk scoring so officers see the riskiest applications first instead of working strictly by submission order.
Features
Application tracking — digitized NOC submission, status tracking, and follow-up workflow
ML risk classification — a trained model flags high-risk applications for prioritized review
Officer dashboard — live risk badges surfaced directly in the reviewer-facing interface
MySQL-backed persistence — structured storage for applications, statuses, and model outputs
Tech Stack
Layer	Technology
Backend	Python, Flask
Database	MySQL
ML	scikit-learn
Frontend	HTML, CSS, JavaScript
Machine Learning Approach
Two models work together in the pipeline:
`RandomForestClassifier` — predicts application type
`GradientBoostingClassifier` — scores application risk
Both were trained and tuned iteratively, with real debugging along the way: resolving MySQL schema mismatches between the application data and model input, fixing SQL compatibility issues, and handling data sparsity in the training set. Model output is written back to MySQL and rendered as a risk badge on the officer dashboard.
