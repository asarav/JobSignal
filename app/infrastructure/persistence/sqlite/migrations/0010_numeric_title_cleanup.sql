-- The partial extractor used to turn a posting id from the URL into a "title",
-- so unreadable postings were stored as "4083293003" instead of "Untitled".
-- Repair those rows; a later crawl refreshes them with the corrected extractor.

UPDATE jobs
SET title = 'Untitled',
    normalized_title = 'untitled',
    title_family = 'untitled'
WHERE title <> ''
  AND title NOT GLOB '*[^0-9]*';
