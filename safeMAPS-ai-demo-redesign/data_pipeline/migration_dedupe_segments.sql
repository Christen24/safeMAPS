-- Fixes: road_segments had no unique constraint beyond the auto-increment
-- `id`, so `ON CONFLICT DO NOTHING` on insert was a structural no-op —
-- every re-run of osm_loader.py duplicated every edge in the table.
--
-- Run this once against the live DB. It's safe to run even if there are
-- no duplicates yet (the DELETE will just remove 0 rows).

-- 1. Remove duplicate edges, keeping the lowest id per
--    (osm_id, source_node, target_node) — a single OSM way legitimately
--    produces multiple rows sharing an osm_id (one per intersection-to-
--    intersection split), so uniqueness must be on the full triple, not
--    osm_id alone.
DELETE FROM road_segments a
USING road_segments b
WHERE a.id > b.id
  AND a.osm_id = b.osm_id
  AND a.source_node = b.source_node
  AND a.target_node = b.target_node;

-- 2. Add the constraint so future re-runs actually dedupe via ON CONFLICT.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'road_segments_unique_edge'
    ) THEN
        ALTER TABLE road_segments
            ADD CONSTRAINT road_segments_unique_edge
            UNIQUE (osm_id, source_node, target_node);
    END IF;
END $$;

-- 3. Sanity check — run manually afterward if you want to see the before/after:
--    SELECT count(*) FROM road_segments;
