# Lio Procurement Intake

A browser-based Django application for turning vendor quote PDFs into structured
procurement requests. Employees and guests can upload a quote, review the extracted
data, and submit it to procurement. Procurement users can sign in, edit requests,
and update their status.

## Features

- PDF text extraction with `pypdf`
- OCR fallback with OCRmyPDF and Tesseract for scanned documents
- Fully local structured extraction with no paid API dependency
- Editable request details and order lines before submission
- Guest uploads and authenticated employee accounts
- Procurement dashboard with role-based access
- Upload, submission, and status-change email notifications
- PostgreSQL and Mailpit development containers
- Responsive, server-rendered Django interface

## Quick start (recommended for Windows)

### 1. Install the prerequisites

Install:

- [Python 3.12 or newer](https://www.python.org/downloads/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Git](https://git-scm.com/downloads)

Docker Desktop must be running before continuing.

### 2. Clone the repository

```powershell
git clone <repository-url>
cd lio-procurement-app
```

Replace `<repository-url>` with the HTTPS URL shown on the GitHub repository page.

### 3. Create the Python environment

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks activation, run this once in the current terminal and then
activate the environment again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

On macOS or Linux, activate the environment with `source .venv/bin/activate`.

### 4. Create the configuration

```powershell
Copy-Item .env.example .env
```

The default values work with the included PostgreSQL and Mailpit containers. The
application does not require an API key or paid extraction service. Never commit
the `.env` file or real credentials; `.env` is excluded by `.gitignore`.

### 5. Start PostgreSQL and Mailpit

```powershell
docker compose up -d db mailpit
```

| Service | Address | Purpose |
|---|---|---|
| PostgreSQL | `localhost:5434` | Application database |
| Mailpit SMTP | `localhost:1026` | Captures development email |
| Mailpit interface | http://127.0.0.1:8026 | Displays captured email |

### 6. Prepare the application

```powershell
python manage.py migrate
python manage.py seed_commodity_groups
python manage.py create_demo_users
```

### 7. Start Django

```powershell
python manage.py runserver
```

Open http://127.0.0.1:8000. The normal login page includes a link for uploading a
PDF as a guest.

## Demo accounts

These accounts are created by `python manage.py create_demo_users`:

| Role | Username | Password | Email |
|---|---|---|---|
| Employee | `employee` | `employee123` | `employee@example.com` |
| Procurement | `procurement` | `procurement123` | `procurement@example.com` |
| Administrator | `admin` | `admin123` | `admin@example.com` |

The administrator can also access http://127.0.0.1:8000/admin/. These credentials
are for local development only.

## Run everything with Docker

The full Docker profile includes Django and the native OCR dependencies:

```powershell
docker compose --profile full up --build
```

It automatically runs migrations, seeds commodity groups, creates demo users, and
starts Django. Do not run the local Django server simultaneously because both use
port 8000. Stop the stack with:

```powershell
docker compose --profile full down
```

Database and uploaded-file Docker volumes remain when the containers stop.

## PDF extraction and OCR

PDFs containing embedded text can be parsed in the local Windows setup. Scanned,
image-only PDFs require OCRmyPDF, Tesseract with German and English language data,
and Ghostscript. These are installed in the full Docker image, making the full
Docker profile the easiest way to test scanned documents.

All document processing remains local. OCRmyPDF and Tesseract recognize scanned
text, while the built-in rules-based parser extracts vendor, VAT, date, total,
order-line, and category values. Users can review and correct permitted fields
before submission.

## Email testing

The default configuration uses Mailpit. Mailpit captures messages locally but does
not deliver them to external inboxes. Submit a request or send a test message:

```powershell
python manage.py send_test_email --to employee@example.com
```

Open http://127.0.0.1:8026. Mailpit has one shared development inbox; the `To`
header shows which user would have received each message.

To deliver real email while Django runs locally, replace the email values in
`.env` with credentials from an SMTP provider:

```text
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-smtp-user
EMAIL_HOST_PASSWORD=your-app-password
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=procurement@your-company.com
PROCUREMENT_EMAIL=procurement-team@your-company.com
```

Restart Django after changing `.env`. Use an application password when supported
and test only with addresses you own or are authorized to contact. The full Docker
profile uses Mailpit by default.

## User workflow

1. A guest or signed-in employee uploads a quote. A guest also enters their name
   and email address.
2. The application extracts the PDF content and creates a draft.
3. The employee receives an upload notification and reviews the extracted fields.
4. The employee corrects editable details and manages order lines.
5. The employee submits the request to procurement.
6. Procurement users view submitted requests, edit allowed data, and update status.
7. Status changes are recorded and emailed to the employee or guest.

## Useful commands

```powershell
python manage.py check
python manage.py test
python manage.py migrate
python manage.py seed_commodity_groups
python manage.py create_demo_users
docker compose ps
docker compose logs -f
```

## Troubleshooting

### Django cannot connect to PostgreSQL

Confirm Docker Desktop is running:

```powershell
docker compose up -d db
docker compose ps
```

For local Django, `POSTGRES_HOST` must be `localhost` and `POSTGRES_PORT` must
be `5434`, as provided in `.env.example`.

### A database column is missing

```powershell
python manage.py migrate
```

### Email does not appear in Mailpit

Confirm Mailpit is running and that `.env` contains `EMAIL_HOST=localhost` and
`EMAIL_PORT=1026`. Restart Django after changing configuration.

### A required port is already in use

Stop the older program or container using port 8000, 5434, or 8026.
`docker compose ps` shows containers belonging to this project.

### OCR is unavailable on Windows

```powershell
docker compose --profile full up --build
```

The full image contains the required native OCR packages.

## Project structure

```text
config/                         Django configuration and root URLs
requests_app/
  management/commands/         Demo data and test-email commands
  migrations/                  Database schema migrations
  templates/requests_app/      Employee and procurement pages
  forms.py                     Upload, request, line, and status forms
  models.py                    Requests, order lines, groups, and history
  services.py                  Extraction, validation, and email services
  views.py                     Guest, employee, and procurement workflows
static/css/                    Application styling
static/js/                     Dynamic order-line behavior
templates/emails/              HTML and plain-text email templates
templates/registration/        Login page
media/                         Local uploaded PDFs (ignored by Git)
compose.yaml                   PostgreSQL, Mailpit, and full app services
Dockerfile                     Django image with OCR dependencies
manage.py                      Django command entry point
```

## Architecture

```text
Browser -> Django (:8000) -> PostgreSQL (:5434 locally)
                 |        -> Mailpit SMTP (:1026 locally)
                 +--------> media storage
```

Extraction currently runs synchronously to keep local deployment simple.

## Production checklist

Before deployment, replace the development secret and demo passwords, disable
debug mode, configure company authentication or SSO, enable HTTPS, use a production
SMTP provider and managed database/object storage, add malware scanning, establish
backup and retention policies, and establish appropriate document privacy and
retention controls.
