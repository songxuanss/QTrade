-- Legacy pending approvals are intentionally not valid for a later switch revision.
ALTER TABLE kill_switch_recovery_request
    ADD COLUMN switch_revision integer CHECK (switch_revision > 0);
