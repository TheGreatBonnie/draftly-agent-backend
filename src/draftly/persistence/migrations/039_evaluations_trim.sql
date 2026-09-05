-- 039_evaluations_trim.sql
-- The per-(case, metric) fan-out rows are no longer written; metric and
-- threshold detail now lives in the summary row's metrics.granular JSONB.
-- Nothing reads the metric or threshold columns, so they can go. case_id
-- stays: search() uses it (`AND case_id IS NULL`) to surface run summaries.
ALTER TABLE evaluations DROP COLUMN IF EXISTS metric;
ALTER TABLE evaluations DROP COLUMN IF EXISTS threshold;