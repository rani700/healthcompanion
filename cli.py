"""HealthCompanion CLI — drive the RAG core end to end.

    python cli.py add-patient --name "Jane Doe"
    python cli.py ingest <patient_id> ./report.pdf --type lab --date 2026-05-01
    python cli.py ask <patient_id> "What was prescribed?" --role patient
    python cli.py list-patients
    python cli.py list-docs <patient_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the src/ package importable when run as `python cli.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import typer

app = typer.Typer(help="Per-patient medical RAG core.", add_completion=False)


@app.command("add-patient")
def add_patient(name: str = typer.Option(..., "--name", "-n", help="Patient name")):
    """Register a new patient."""
    from healthcompanion import patients

    pid = patients.create_patient(name)
    typer.echo(f"Created patient '{name}' with id: {pid}")


@app.command()
def ingest(
    patient_id: str = typer.Argument(..., help="Patient id"),
    path: Path = typer.Argument(..., exists=True, help="Document file"),
    doc_type: str = typer.Option("other", "--type", "-t", help="rx | lab | note | other"),
    date: str = typer.Option(None, "--date", "-d", help="Document date, e.g. 2026-05-01"),
):
    """Ingest a medical document for a patient."""
    from healthcompanion.ingest import ingest_document

    typer.echo(f"Extracting and embedding {path.name} ...")
    result = ingest_document(patient_id, path, doc_type=doc_type, doc_date=date)
    typer.echo(
        f"Stored {result['n_chunks']} chunk(s) "
        f"(doc_id={result['doc_id']}, type={result['doc_type']})."
    )


@app.command()
def ask(
    patient_id: str = typer.Argument(..., help="Patient id"),
    question: str = typer.Argument(..., help="Your question"),
    role: str = typer.Option("patient", "--role", "-r", help="patient | doctor"),
):
    """Ask a question grounded in a patient's records."""
    from healthcompanion.rag import ask as rag_ask

    result = rag_ask(patient_id, question, role=role)
    typer.echo("\n" + result["answer"] + "\n")
    if result["sources"]:
        typer.echo("Sources:")
        for s in result["sources"]:
            date = s["doc_date"] or "undated"
            typer.echo(f"  - {s['filename']} ({s['doc_type']}, {date})")


@app.command("list-patients")
def list_patients():
    """List all patients."""
    from healthcompanion import patients

    rows = patients.list_patients()
    if not rows:
        typer.echo("No patients yet.")
        return
    for r in rows:
        typer.echo(f"  {r['id']}  {r['name']}  (created {r['created_at']})")


@app.command("list-docs")
def list_docs(patient_id: str = typer.Argument(..., help="Patient id")):
    """List documents ingested for a patient."""
    from healthcompanion import patients

    rows = patients.list_documents(patient_id)
    if not rows:
        typer.echo("No documents for this patient.")
        return
    for r in rows:
        date = r["doc_date"] or "undated"
        typer.echo(
            f"  {r['id']}  {r['filename']}  [{r['doc_type']}, {date}]  "
            f"{r['n_chunks']} chunk(s)"
        )


if __name__ == "__main__":
    app()
