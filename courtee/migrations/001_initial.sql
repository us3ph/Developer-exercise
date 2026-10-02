CREATE TABLE person (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('client', 'bank_adviser', 'broker'))
);

CREATE TABLE contact (
    id INTEGER PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES person(id),
    channel TEXT NOT NULL CHECK (channel IN ('whatsapp', 'email')),
    normalized_value TEXT NOT NULL,
    UNIQUE (channel, normalized_value)
);

CREATE TABLE dossier (
    id INTEGER PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    bank TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE dossier_participant (
    person_id INTEGER NOT NULL REFERENCES person(id),
    dossier_id INTEGER NOT NULL REFERENCES dossier(id),
    role TEXT NOT NULL,
    PRIMARY KEY (person_id, dossier_id)
);

CREATE TABLE message (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL CHECK (channel IN ('whatsapp', 'email')),
    direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
    external_id TEXT NOT NULL,
    content TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipients_json TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL DEFAULT '',
    headers_json TEXT NOT NULL DEFAULT '{}',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    payload_json TEXT NOT NULL DEFAULT '{}',
    dossier_id INTEGER REFERENCES dossier(id),
    routing_method TEXT NOT NULL,
    routing_state TEXT NOT NULL CHECK (routing_state IN ('attached', 'needs_choice', 'triage')),
    routing_reason TEXT NOT NULL,
    candidates_json TEXT NOT NULL DEFAULT '[]',
    date TEXT NOT NULL,
    received_at TEXT NOT NULL,
    UNIQUE (channel, external_id),
    CHECK ((routing_state = 'attached' AND dossier_id IS NOT NULL)
        OR (routing_state != 'attached' AND dossier_id IS NULL))
);

CREATE INDEX message_dossier_date ON message(dossier_id, date DESC, id DESC);
CREATE INDEX participant_dossier ON dossier_participant(dossier_id);
