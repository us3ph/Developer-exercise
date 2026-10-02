CREATE TABLE message_analysis (
    message_id INTEGER PRIMARY KEY REFERENCES message(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'manual_review')),
    raw_output TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE attachment (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    position INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    classification TEXT,
    UNIQUE (message_id, position)
);

CREATE TABLE proposal (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES message(id),
    dossier_id INTEGER NOT NULL REFERENCES dossier(id),
    action_index INTEGER NOT NULL,
    action_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected')),
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, action_index)
);

CREATE TABLE dossier_history (
    id INTEGER PRIMARY KEY,
    dossier_id INTEGER NOT NULL REFERENCES dossier(id),
    message_id INTEGER NOT NULL REFERENCES message(id),
    action_index INTEGER NOT NULL,
    proposal_id INTEGER REFERENCES proposal(id),
    action_json TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    applied_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, action_index)
);

CREATE TABLE document_request (
    id INTEGER PRIMARY KEY,
    dossier_id INTEGER NOT NULL REFERENCES dossier(id),
    message_id INTEGER NOT NULL REFERENCES message(id),
    action_index INTEGER NOT NULL,
    document TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (message_id, action_index)
);

CREATE INDEX proposal_dossier_status ON proposal(dossier_id, status);
CREATE INDEX history_dossier_date ON dossier_history(dossier_id, created_at DESC, id DESC);
