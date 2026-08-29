# Lio Procurement Intake

A browser-based Django application that turns vendor quote PDFs into structured,
editable procurement requests. Employees review the extracted result before
submission; procurement users manage incoming requests and status updates.

## What is included

- PDF validation, secure per-request storage, and authenticated document access
- OpenAI PDF extraction into request fields and order lines
- Automatic classification into the 50 supplied commodity groups
- Editable drafts with server-side submission validation
- Employee and procurement dashboards
- Guest upload with browser-session isolation, plus Django group-based authorization and demo users
- Immutable request status history
- Upload, submission, and status-change email notifications
- PostgreSQL and Mailpit containers
- Responsive, accessible server-rendered interface

## Prerequisites

- Python 3.12 or newer
- Docker Desktop
- An OpenAI API key for automatic PDF extraction

## First-time setup

Create and activate a virtual environment:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create the local configuration:

```powershell
Copy-Item .env.example .env
```

Add your key to `.env`:

```text
OPENAI_API_KEY=your-key-here
```

Start PostgreSQL and the local mail server:

```powershell
docker compose up -d
```

Prepare the database and demo accounts:

```powershell
python manage.py migrate
python manage.py seed_commodity_groups
python manage.py create_demo_users
```

Start Django locally:

```powershell
python manage.py runserver
```

Open:

- Application: http://127.0.0.1:8000
- Mailpit inbox: http://127.0.0.1:8026
- Django Admin: http://127.0.0.1:8000/admin/

## Demo users

| Role | Username | Password | Email |
|---|---|---|---|
| Employee | `employee` | `employee123` | `employee@example.com` |
| Procurement | `procurement` | `procurement123` | `procurement@example.com` |
| Administrator | `admin` | `admin123` | `admin@example.com` |

The demo-user command sets a password only when it creates an account, so rerunning
it will not unexpectedly reset an existing password.

## User workflow

1. A guest or signed-in employee uploads a vendor quote PDF. Guests provide their name and email.
2. Django stores the original document and sends it to OpenAI for extraction.
3. The employee receives an upload email and reviews all extracted fields.
4. The employee can add, edit, or delete order lines and correct any field.
5. Only a valid request can be submitted to procurement.
6. Procurement sees submitted requests, changes their status, and adds a note.
7. Every status change is recorded and emailed to the employee.

If `OPENAI_API_KEY` is empty or extraction fails, the PDF is still saved and the
employee can complete every field manually.

## Architecture

```text
Browser -> Django (local :8000) -> PostgreSQL (Docker :5434)
                 |              -> Mailpit SMTP (Docker :1026)
                 |              -> local media/ storage
                 +--------------> OpenAI Responses API (HTTPS)
```

The app intentionally performs extraction synchronously to keep local deployment
simple. A task queue can be introduced later if document processing time or traffic
requires it.

## Tests

```powershell
python manage.py check
python manage.py test
```

Tests use SQLite, an in-memory email backend, synthetic PDFs, and no real OpenAI
calls. They cover upload validation, ownership boundaries, role permissions,
procurement visibility, submission, status history, and emails.

## Project structure

```text
config/                         Django settings and root URLs
requests_app/
  management/commands/         Commodity seed and demo users
  migrations/                  Database schema
  templates/requests_app/      Employee and procurement pages
  forms.py                     Upload, request, formset and status forms
  models.py                    Requests, lines, groups, history and email logs
  services.py                  OpenAI extraction, validation and email
  views.py                     Authenticated workflows
static/css/                    Visual design
static/js/                     Dynamic order-line behavior
templates/emails/              HTML and plain-text notifications
templates/registration/        Login page
media/                         Local PDFs; uploaded files are ignored by Git
compose.yaml                   PostgreSQL and Mailpit only
```

## Production notes

Before production deployment, replace the demo secret and passwords, use company
SSO, set `DJANGO_DEBUG=False`, use managed object storage, enable HTTPS, add malware
scanning, configure backups and retention, and review whether vendor documents may
be sent to the configured AI provider.
