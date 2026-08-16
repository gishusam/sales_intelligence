ALTER TABLE campaigns
ADD COLUMN IF NOT EXISTS communication_type VARCHAR(30);

ALTER TABLE campaigns
DROP CONSTRAINT IF EXISTS campaigns_communication_type_check;

ALTER TABLE campaigns
ADD CONSTRAINT campaigns_communication_type_check
CHECK (
  communication_type IS NULL
  OR communication_type IN ('cold_outreach', 'newsletter')
);
