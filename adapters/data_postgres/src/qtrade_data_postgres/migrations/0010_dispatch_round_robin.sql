-- A durable monotonically increasing turn makes new groups join the tail.
-- Wall-clock ordering alone cannot preserve fairness after clock corrections.
CREATE SEQUENCE qtrade_data.dispatch_turn;
CREATE TABLE qtrade_data.dispatch_cursor (
    queue text NOT NULL CHECK (queue IN ('collection','research')),
    kind text NOT NULL CHECK (kind IN ('source','scope','run')),
    source text NOT NULL CHECK (length(source) BETWEEN 1 AND 256),
    scope_key text NOT NULL,
    run_key text NOT NULL,
    last_turn bigint NOT NULL DEFAULT nextval('qtrade_data.dispatch_turn') CHECK (last_turn>0),
    PRIMARY KEY (queue,kind,source,scope_key,run_key),
    CHECK ((kind='source' AND scope_key='' AND run_key='')
        OR (kind='scope' AND scope_key ~ '^[0-9a-f-]{36}$' AND run_key='')
        OR (kind='run' AND scope_key ~ '^[0-9a-f-]{36}$' AND run_key ~ '^[0-9a-f-]{36}$'))
);
