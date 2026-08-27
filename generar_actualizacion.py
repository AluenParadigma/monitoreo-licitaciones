import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from openai import OpenAI


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "actualizacion_monitoreo.json"
HISTORY = ROOT / "historial_ids.json"
META_OUT = ROOT / "metadata.json"

URLS = {
    "bid_data": "https://raw.githubusercontent.com/AluenParadigma/bid-data-public/main/latest.json",
    "bid_meta": "https://raw.githubusercontent.com/AluenParadigma/bid-data-public/main/metadata.json",
    "sicop_data": "https://raw.githubusercontent.com/AluenParadigma/sicop-data-public/main/latest.json",
    "sicop_meta": "https://raw.githubusercontent.com/AluenParadigma/sicop-data-public/main/metadata.json",
    "wb_data": "https://raw.githubusercontent.com/AluenParadigma/worldbank-data-public/main/data/latest.json",
    "wb_meta": "https://raw.githubusercontent.com/AluenParadigma/worldbank-data-public/main/data/metadata.json",
}

AFFINITIES = {"Muy alta", "Alta", "Media-Alta", "Media", "Baja"}
RELEVANT = {"Muy alta", "Alta", "Media-Alta", "Media"}


def get_json(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def validate_bid(data, meta):
    rows = data.get("data", [])
    total = int(meta.get("total_records", -1))

    ok = (
        meta.get("coverage_complete") is True
        and meta.get("validation_status") == "OK"
        and len(rows) == total
    )

    return ok, total, rows


def validate_sicop(data, meta):
    # SICOP guarda el universo en "content".
    # Se dejan alternativas por compatibilidad.
    rows = data.get(
        "content",
        data.get(
            "data",
            data if isinstance(data, list) else []
        )
    )

    total_elements = int(meta.get("total_elements", -1))
    records_extracted = int(meta.get("records_extracted", -2))
    csv_records = int(meta.get("csv_records", -3))

    ok = (
        total_elements == records_extracted == csv_records
        and meta.get("coverage_complete") is True
        and meta.get("validation_status") == "OK"
        and len(rows) == total_elements
    )

    return ok, total_elements, rows


def validate_wb(data, meta):
    rows = data.get("data", [])

    current = int(meta.get("current_consulting_en_es", -1))
    json_records = int(meta.get("json_records", -2))
    csv_records = int(meta.get("csv_records", -3))

    controls = meta.get("language_controls", {})

    language_controls_ok = True

    if isinstance(controls, dict):
        values = controls.values()
    elif isinstance(controls, list):
        values = controls
    else:
        values = []
        language_controls_ok = False

    for control in values:
        downloaded = int(control.get("records_downloaded", -1))
        api_total = int(control.get("api_total", -2))

        if (
            control.get("coverage_complete") is not True
            or downloaded != api_total
        ):
            language_controls_ok = False

    ok = (
        language_controls_ok
        and current == json_records == csv_records
        and len(rows) == current
        and meta.get("coverage_complete") is True
        and meta.get("validation_status") == "OK"
    )

    return ok, current, rows


def compact_record(source, r):
    if source == "BID":
        return {
            "source": source,
            "id": str(
                r.get("id")
                or r.get("tender_id")
                or r.get("reference")
                or r.get("reference_no")
                or ""
            ),
            "title": r.get("title"),
            "institution": (
                r.get("organization")
                or r.get("institution")
                or r.get("agency")
            ),
            "country": r.get("country"),
            "amount": r.get("amount"),
            "publication_date": r.get("date_created"),
            "deadline": r.get("date_close"),
            "url": (
                r.get("url")
                or r.get("link")
                or r.get("source_url")
            ),
            "text": (
                r.get("description")
                or r.get("content")
                or r.get("scope")
                or ""
            ),
            "raw_id_candidates": {
                k: r.get(k)
                for k in [
                    "id",
                    "tender_id",
                    "project_id",
                    "reference_no",
                    "slug",
                ]
                if r.get(k) is not None
            },
        }

    if source == "SICOP":
        return {
            "source": source,
            "id": str(r.get("instCartelNo") or ""),
            "title": (
                r.get("cartelNm")
                or r.get("title")
                or r.get("objeto")
                or r.get("description")
            ),
            "institution": r.get("cartelInstNm"),
            "country": "Costa Rica",
            "amount": None,
            "publication_date": (
                r.get("publication_date")
                or r.get("regDt")
            ),
            "deadline": r.get("openbidDt"),
            "url": r.get("url") or r.get("link"),
            "text": (
                r.get("cartelNm")
                or r.get("description")
                or ""
            ),
            "raw_id_candidates": {
                "instCartelNo": r.get("instCartelNo")
            },
        }

    # Banco Mundial
    return {
        "source": source,
        "id": str(
            r.get("notice_id")
            or r.get("reference_no")
            or ""
        ),
        "title": r.get("title"),
        "institution": (
            r.get("institution")
            or r.get("project_name")
        ),
        "country": r.get("country"),
        "amount": None,
        "publication_date": r.get("publication_date"),
        "deadline": r.get("deadline"),
        "url": r.get("source_url"),
        "text": r.get("notice_text") or "",
        "project_id": r.get("project_id"),
        "reference_no": r.get("reference_no"),
        "language": r.get("language"),
        "procurement_method": r.get("procurement_method"),
    }


def classify_batch(client, batch):
    prompt = """
Sos un analista senior de oportunidades comerciales para una consultora
de gestión y tecnología llamada Paradigma.

Evaluá SEMÁNTICAMENTE cada registro, sin exigir palabras clave.

La afinidad debe evaluarse con relación a:
- consultoría de gestión
- estrategia
- transformación digital
- procesos
- eficiencia
- modelos operativos
- gestión del cambio
- PMO
- gestión de proyectos, programas y carteras
- implementación
- tecnología
- datos
- fortalecimiento institucional
- diseño de metodologías
- gobernanza
- capacitación
- servicios profesionales relacionados

Afinidad permitida exclusivamente:
Muy alta
Alta
Media-Alta
Media
Baja

Para cada registro devolvé:
- source_key
- affinity
- summary

Copiá source_key exactamente como viene en cada registro.

summary debe tener entre 1 y 3 oraciones concretas sobre el objeto
y el alcance.

No inventes montos, fechas ni alcance.

Respondé únicamente JSON con este formato:

{
  "items": [
    {
      "source_key": "...",
      "affinity": "...",
      "summary": "..."
    }
  ]
}

Registros:
""" + json.dumps(batch, ensure_ascii=False)

    response = client.responses.create(
        model="gpt-5-mini",
        input=prompt,
        text={
            "format": {
                "type": "json_object"
            }
        },
    )

    obj = json.loads(response.output_text)

    return obj.get("items", [])


def load_history():
    if HISTORY.exists():
        try:
            content = HISTORY.read_text(encoding="utf-8")
            return set(json.loads(content).get("ids", []))
        except Exception:
            return set()

    return set()


def key(source, ident):
    return f"{source}|{ident}".strip()


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Falta el secret OPENAI_API_KEY",
            file=sys.stderr,
        )
        sys.exit(2)

    print("Descargando snapshots...")

    bid_data = get_json(URLS["bid_data"])
    bid_meta = get_json(URLS["bid_meta"])

    sicop_data = get_json(URLS["sicop_data"])
    sicop_meta = get_json(URLS["sicop_meta"])

    wb_data = get_json(URLS["wb_data"])
    wb_meta = get_json(URLS["wb_meta"])

    print("Validando cobertura...")

    bid_ok, bid_total, bid_rows = validate_bid(
        bid_data,
        bid_meta,
    )

    sicop_ok, sicop_total, sicop_rows = validate_sicop(
        sicop_data,
        sicop_meta,
    )

    wb_ok, wb_total, wb_rows = validate_wb(
        wb_data,
        wb_meta,
    )

    print(
        f"BID: {len(bid_rows)}/{bid_total} - OK={bid_ok}"
    )

    print(
        f"SICOP: {len(sicop_rows)}/{sicop_total} - OK={sicop_ok}"
    )

    print(
        f"Banco Mundial: {len(wb_rows)}/{wb_total} - OK={wb_ok}"
    )

    if not (bid_ok and sicop_ok and wb_ok):
        print(
            (
                "Cobertura invalida. "
                f"BID={bid_ok} ({len(bid_rows)}/{bid_total}) "
                f"SICOP={sicop_ok} ({len(sicop_rows)}/{sicop_total}) "
                f"WB={wb_ok} ({len(wb_rows)}/{wb_total})"
            ),
            file=sys.stderr,
        )
        sys.exit(3)

    all_records = []

    sources = [
        ("BID", bid_rows),
        ("SICOP", sicop_rows),
        ("Banco Mundial", wb_rows),
    ]

    for source_name, rows in sources:
        for raw in rows:
            rec = compact_record(
                source_name,
                raw,
            )

            rec["source_key"] = key(
                source_name,
                rec["id"],
            )

            all_records.append(rec)

    print(
        f"Total de registros a analizar semanticamente: "
        f"{len(all_records)}"
    )

    client = OpenAI()

    classified = {}

    # Se analiza el 100% del universo.
    # No existe prefiltrado por palabras clave.
    batch_size = 25

    total_batches = (
        len(all_records) + batch_size - 1
    ) // batch_size

    for i in range(
        0,
        len(all_records),
        batch_size,
    ):
        batch_number = (
            i // batch_size
        ) + 1

        print(
            f"Analizando lote "
            f"{batch_number}/{total_batches}..."
        )

        batch = all_records[
            i:i + batch_size
        ]

        result = classify_batch(
            client,
            batch,
        )

        for item in result:
            source_key = str(
                item.get(
                    "source_key",
                    "",
                )
            )

            classified[source_key] = item

    history = load_history()

    novedades = []

    all_seen = set(history)

    for record in all_records:
        classification = classified.get(
            record["source_key"],
            {},
        )

        affinity = classification.get(
            "affinity",
            "Baja",
        )

        if affinity not in AFFINITIES:
            affinity = "Baja"

        record_key = key(
            record["source"],
            record["id"],
        )

        all_seen.add(record_key)

        if (
            affinity in RELEVANT
            and record_key not in history
        ):
            amount = record.get("amount")

            if record["source"] == "BID":
                if amount not in (
                    None,
                    "",
                    0,
                ):
                    monto = f"USD {amount}"
                else:
                    monto = "No informado"
            else:
                monto = "No informado"

            novedades.append(
                {
                    "Fuente": record["source"],
                    "Identificador": record["id"],
                    "Oportunidad": (
                        record.get("title")
                        or ""
                    ),
                    "Institución/Entidad": (
                        record.get("institution")
                        or ""
                    ),
                    "País": (
                        record.get("country")
                        or ""
                    ),
                    "Monto": monto,
                    "Fecha de publicación": (
                        record.get(
                            "publication_date"
                        )
                        or "No informado"
                    ),
                    "Cierre": (
                        record.get("deadline")
                        or "No informado"
                    ),
                    "Afinidad": affinity,
                    "Resumen de lo que piden": (
                        classification.get(
                            "summary"
                        )
                        or ""
                    ),
                    "Fuente/Enlace": (
                        record.get("url")
                        or ""
                    ),
                }
            )

    generated = datetime.now(
        timezone.utc
    ).isoformat()

    payload = {
        "generated_at": generated,
        "coverage": {
            "BID": (
                f"{bid_total}/{bid_total}"
            ),
            "SICOP": (
                f"{sicop_total}/{sicop_total}"
            ),
            "Banco Mundial": (
                f"{wb_total}/{wb_total}"
            ),
        },
        "novedades": novedades,
    }

    OUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    HISTORY.write_text(
        json.dumps(
            {
                "ids": sorted(all_seen)
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    META_OUT.write_text(
        json.dumps(
            {
                "generated_at": generated,
                "coverage_complete": True,
                "validation_status": "OK",
                "bid_records": bid_total,
                "sicop_records": sicop_total,
                "worldbank_records": wb_total,
                "new_records": len(novedades),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(
        f"OK: {len(novedades)} novedades."
    )

    print(
        (
            f"Cobertura BID "
            f"{bid_total}/{bid_total}, "
            f"SICOP "
            f"{sicop_total}/{sicop_total}, "
            f"WB "
            f"{wb_total}/{wb_total}"
        )
    )


if __name__ == "__main__":
    main()
