-- Historical jobs were normalized before workplace inference considered the
-- merged ``locations`` field, so every row kept workplace_type = 'unknown'.
-- Derive it once from the stored raw location text. Future crawls infer it
-- directly in the normalizer; this statement is a one-time repair.

UPDATE jobs
SET workplace_type = 'hybrid'
WHERE workplace_type = 'unknown'
  AND json_valid(locations_json)
  AND EXISTS (
    SELECT 1 FROM json_each(jobs.locations_json)
    WHERE lower(CAST(COALESCE(json_extract(value, '$.raw'), value) AS TEXT)) LIKE '%hybrid%'
  );

UPDATE jobs
SET workplace_type = 'remote'
WHERE workplace_type = 'unknown'
  AND json_valid(locations_json)
  AND EXISTS (
    SELECT 1 FROM json_each(jobs.locations_json)
    WHERE lower(CAST(COALESCE(json_extract(value, '$.raw'), value) AS TEXT))
      LIKE '%remote%'
       OR lower(CAST(COALESCE(json_extract(value, '$.raw'), value) AS TEXT))
      LIKE '%anywhere%'
       OR lower(CAST(COALESCE(json_extract(value, '$.raw'), value) AS TEXT))
      LIKE '%work from home%'
  );

UPDATE jobs
SET workplace_type = 'onsite'
WHERE workplace_type = 'unknown'
  AND json_valid(locations_json)
  AND EXISTS (
    SELECT 1 FROM json_each(jobs.locations_json)
    WHERE lower(CAST(COALESCE(json_extract(value, '$.raw'), value) AS TEXT)) LIKE '%on-site%'
       OR lower(CAST(COALESCE(json_extract(value, '$.raw'), value) AS TEXT)) LIKE '%onsite%'
  );
