-- M5 gap closure: skill recency (spec sec 10.3) alongside stored duration.

ALTER TABLE extracted_skills ADD COLUMN recency_years REAL;
