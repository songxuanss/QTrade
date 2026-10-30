-- Publication lookup filters by stock/period before applying any page limit.
CREATE INDEX market_dataset_instruments
    ON market_dataset USING gin ((payload->'instrument_ids') jsonb_path_ops);
CREATE INDEX market_dataset_instrument_page
    ON market_dataset(workspace_id,(payload->>'granularity'),dataset_id DESC);
