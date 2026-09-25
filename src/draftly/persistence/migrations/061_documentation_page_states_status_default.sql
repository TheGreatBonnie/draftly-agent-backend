-- 061: give documentation_page_states.status a sensible default.
--
-- The page-workflow seed INSERT (PageWorkflowRepository.create_pages) originally
-- omitted the `status` column while the schema declared it NOT NULL with no
-- default (060), so every seed row raised NotNullViolationError and failed the
-- whole page workflow. The repository now supplies 'pending' explicitly; this
-- migration is the schema-side safety net for already-deployed databases.
ALTER TABLE documentation_page_states
    ALTER COLUMN status SET DEFAULT 'pending';