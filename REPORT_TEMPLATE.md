# Hostel Helpdesk Telegram Bot — Project Report

> Replace every bracketed field with your team's real details and measured results. Do not claim a live deployment, real conversations or evaluation results that you have not actually obtained.

## Cover page

- Course: CSE276 — Artificial Intelligence Foundations
- Project title: Hostel Helpdesk Telegram Bot
- Team members: [Name, registration number, role for each member]
- Faculty/section: [Details]
- Submission date: [Date]
- Repository: [GitHub URL]
- Live Telegram bot: [@username]
- Deployment URL: [Public HTTPS URL; do not include secrets]

## 1. Abstract

[Summarise the hostel-service problem, Telegram/FastAPI architecture, complaint/ticket workflow, AI methods compared, deployment and measured results in 150–250 words.]

## 2. Problem definition

### 2.1 Problem being addressed
[Explain the difficulty of recording, tracking and escalating maintenance complaints through informal messages or verbal reports. Do not claim that a particular hostel officially adopted the tool unless permission was obtained.]

### 2.2 Target users and stakeholders
- Hostel residents: [role]
- Helpdesk/maintenance administrator: [role]
- Project team: [role]

### 2.3 Inputs, outputs and success criteria
| Input | Output | Success criterion |
|---|---|---|
| Complaint category | Ticket record | Ticket is saved once |
| Block/room/location | Ticket location | Location is retained |
| Issue description | Ticket description | Description is retrievable |
| Photo/document | Stored attachment | File saved and delivered to admin |
| Ticket ID | Ticket status | Only owner can view it |
| Human request | Admin notification | Admin can reply to the requester |

## 3. AI problem formulation and representation

### 3.1 Intent categories
[Describe Wi-Fi, plumbing, electrical, cleaning, furniture and unknown intents.]

### 3.2 Knowledge representation
[Explain keyword dictionaries, ordered regex rules, FAQs JSON, intent-to-answer mapping, matched-rule trace and unknown fallback.]

### 3.3 Conversation state representation
| State | Expected input | Next state |
|---|---|---|
| not consented (consent table) | I AGREE / /decline | idle after consent / no service session after decline |
| idle | New complaint | awaiting_category |
| awaiting_category | category | awaiting_location |
| awaiting_location | location | awaiting_description |
| awaiting_description | issue text | awaiting_media |
| awaiting_media | photo/document/skip | idle |
| idle | Check status | awaiting_ticket_id |
| awaiting_ticket_id | ticket ID | idle |
| human_mode | follow-up message/media | human_mode |

## 4. System architecture and implementation

[Insert a diagram of Telegram Bot API → FastAPI webhook → bot.py → SQLite / local persistent volume / admin.]

Describe:
- Telegram webhook registration and secret-header verification.
- FastAPI request handling and health endpoint.
- Per-chat session persistence.
- SQLite tables: processed_updates, consents, conversations, sessions, tickets, messages and attachments.
- Ticket ownership check using both ticket ID and chat ID.
- Attachment download, size limit, random server filename and admin forwarding.
- Admin commands `/help`, `/stats`, `/ticket`, `/reply` and `/close`.
- Webhook deduplication and failure/retry handling.

## 5. AI methods and baseline comparison

### 5.1 Method A — keyword baseline
[Explain tokenisation and exact word-set overlap.]

### 5.2 Method B — regex rule engine
[Explain ordered patterns, common variants, first matching intent and explanation trace.]

### 5.3 Evaluation dataset
- Number of labelled examples: [n]
- Category distribution: [counts]
- Source: [synthetically authored examples / consented anonymised user questions / both]
- Train/test split if applicable: [details]
- Label-generation and review process: [details]

Do not describe synthetic examples as real-user conversations. Keep the AI test dataset separate from live usage logs.

### 5.4 Measured comparison
| Metric | Keyword baseline | Regex rule engine |
|---|---:|---:|
| Correct predictions | [x/n] | [x/n] |
| Accuracy | [%] | [%] |
| Average execution time | [ms] | [ms] |
| Unknown/fallback count | [n] | [n] |

[Add confusion matrix, error examples and explanation traces.]

## 6. Live usage and performance analysis

### 6.1 Deployment details
- Platform: [Railway / local ngrok / other]
- Hosting region: [if known]
- Deployment date/time: [date and timezone]
- Runtime/Python version: [version]
- Database: [SQLite path on persistent volume / local disk]
- Public bot username: [username]

### 6.2 Usage statistics
Record actual values from the protected `/admin/stats` endpoint and preserve a dated screenshot/export.

| Metric | Actual measured value | Collection date |
|---|---:|---|
| External users | [n] | [date] |
| External conversations | [n] | [date] |
| Incoming messages | [n] | [date] |
| Tickets created | [n] | [date] |
| Human handovers | [n] | [date] |
| Attachments saved | [n] | [date] |

The code defines a conversation as a session window starting after more than 30 minutes of inactivity from the prior message in the same Telegram chat. This is the project's analytics definition. It is not independent proof of unique or genuine people.

### 6.3 Genuine user use
[Describe how permission was obtained, how user privacy was explained, what task the user attempted, what evidence was recorded, and how duplicates/team members were excluded. Include no phone numbers, chat IDs, names or private message contents in the report.]

Course requirement: [Record whether at least 25 genuine external-user conversations were actually completed. Never mark this complete using synthetic data or teammate-only tests.]

### 6.4 Reliability tests
| Test | Expected behaviour | Actual result | Evidence |
|---|---|---|---|
| Duplicate webhook update | Process once | [pass/fail] | [log/test] |
| Invalid webhook secret | HTTP 403 | [pass/fail] | [screenshot] |
| Restart persistence | Existing tickets remain | [pass/fail] | [before/after] |
| Cross-user ticket lookup | Deny access | [pass/fail] | [test] |
| Invalid attachment | Safe error | [pass/fail] | [log] |
| Admin notification failure | User sees honest warning | [pass/fail] | [test] |

## 7. Explainability and responsible AI

### 7.1 Explainability
[Show one example intent, the detected category, matched regex and exact matched text. Explain how unknown inputs fall back.]

### 7.2 Privacy and consent
[Describe the initial privacy notice, explicit I AGREE flow, /decline behaviour, /delete_my_data, stored data types, retention and who can access the admin environment.]

### 7.3 Security
[Discuss Telegram webhook secret validation, admin API key, ticket ownership enforcement, random attachment filenames, file-size checks, secret management and GitHub exclusions.]

### 7.4 Fairness and transparency
[Discuss language variation, spelling mistakes, abbreviations, accents/transliteration, different user descriptions and the risk that a strict rule engine recognises some phrasing better than others.]

### 7.5 Failure cases and limitations
[Document unknown questions, overlapping rules, incorrect category detection, Telegram API failures, host downtime, lost connectivity, media limits and the need for a human for unresolved requests.]

## 8. Testing

- Automated command: `pytest -q`
- Result: [number passed / failed]
- Manual Telegram tests: [list actual tests]
- Integration tests: [webhook, database, media, admin]
- Test date: [date]

Attach relevant test output with secrets and private data hidden.

## 9. Team contribution

| Member | Files/features owned | Tests completed | What they can explain in viva |
|---|---|---|---|
| Member 1 | [details] | [details] | [details] |
| Member 2 | [details] | [details] | [details] |
| Member 3 | [details] | [details] | [details] |

## 10. Conclusion and future improvements

[Summarise what works and measured limitations. Future improvements may include multiple authorised admins, role-based permissions, PostgreSQL, an attachment object store, multilingual intent handling, ticket priority, status notification and an audit dashboard.]

## References

- Telegram Bot API documentation: https://core.telegram.org/bots/api
- FastAPI documentation: https://fastapi.tiangolo.com/
- Python sqlite3 documentation: https://docs.python.org/3/library/sqlite3.html
- Railway deployment and volume documentation: https://docs.railway.com/
- [Any additional factual sources used for real hostel procedures or FAQs]
