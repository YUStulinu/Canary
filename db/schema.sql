-- Canary's tables in ClickHouse.
CREATE DATABASE IF NOT EXISTS canary;

-- Every log line the collector received. seq is assigned by the collector in arrival order, so the
-- detector can read new lines with "seq > last".
CREATE TABLE IF NOT EXISTS canary.logs (
    seq        UInt64,
    ts         DateTime64(6, 'UTC'),
    source     LowCardinality(String),
    level      LowCardinality(String),
    component  LowCardinality(String),
    session    String,
    message    String,
    label      LowCardinality(String),
    received   DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX seq_idx seq TYPE minmax GRANULARITY 1
) ENGINE = MergeTree
ORDER BY (source, ts);

-- The templates the detector's Drain has mined, per source.
CREATE TABLE IF NOT EXISTS canary.templates (
    source    LowCardinality(String),
    id        UInt32,
    template  String,
    updated   DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(updated)
ORDER BY (source, id);

-- Sessions (HDFS blocks, BGL minutes) the detector has scored.
CREATE TABLE IF NOT EXISTS canary.scored_sessions (
    source       LowCardinality(String),
    session      String,
    first_ts     DateTime64(6, 'UTC'),
    last_ts      DateTime64(6, 'UTC'),
    events       UInt32,
    misses       UInt32,      -- events the sequence model did not expect
    anomalous    UInt8,
    unexpected   Array(UInt32),  -- templates of the unexpected events
    incident     UInt64,
    truth        Int8,        -- the dataset's own label when known (1, 0), else -1
    scored       DateTime DEFAULT now()
) ENGINE = MergeTree
ORDER BY (source, first_ts);

-- Anomalous sessions grouped by what went wrong, with the language model's summary.
CREATE TABLE IF NOT EXISTS canary.incidents (
    id          UInt64,
    source      LowCardinality(String),
    signature   String,          -- the unexpected templates that define the group
    first_ts    DateTime64(6, 'UTC'),
    last_ts     DateTime64(6, 'UTC'),
    sessions    UInt32,
    templates   Array(String),
    samples     Array(String),   -- log lines shown to the model and the reader
    sample_alerts Array(UInt8),  -- the dataset's own alert label of each sample (BGL), for evaluation only
    summary     String,
    cause       String,
    evidence    Array(UInt16),   -- indexes into samples the summary rests on
    updated     DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(updated)
ORDER BY id;
