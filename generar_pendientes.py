import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
PEND = ROOT / 'pendientes_clasificacion.json'
HISTORY = ROOT / 'historial_ids.json'
META_OUT = ROOT / 'metadata.json'

URLS = {
    'bid_data': 'https://raw.githubusercontent.com/AluenParadigma/bid-data-public/main/latest.json',
    'bid_meta': 'https://raw.githubusercontent.com/AluenParadigma/bid-data-public/main/metadata.json',
    'sicop_data': 'https://raw.githubusercontent.com/AluenParadigma/sicop-data-public/main/latest.json',
    'sicop_meta': 'https://raw.githubusercontent.com/AluenParadigma/sicop-data-public/main/metadata.json',
    'wb_data': 'https://raw.githubusercontent.com/AluenParadigma/worldbank-data-public/main/data/latest.json',
    'wb_meta': 'https://raw.githubusercontent.com/AluenParadigma/worldbank-data-public/main/data/metadata.json',
}

def get_json(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()

def validate_bid(data, meta):
    rows = data.get('data', [])
    total = int(meta.get('total_records', -1))
    ok = meta.get('coverage_complete') is True and meta.get('validation_status') == 'OK' and len(rows) == total
    return ok, total, rows

def validate_sicop(data, meta):
    rows = data.get('content', data.get('data', data if isinstance(data, list) else []))
    total = int(meta.get('total_elements', -1))
    ext = int(meta.get('records_extracted', -2))
    csv = int(meta.get('csv_records', -3))
    ok = total == ext == csv and meta.get('coverage_complete') is True and meta.get('validation_status') == 'OK' and len(rows) == total
    return ok, total, rows

def validate_wb(data, meta):
    rows = data.get('data', [])
    current = int(meta.get('current_consulting_en_es', -1))
    json_records = int(meta.get('json_records', -2))
    csv_records = int(meta.get('csv_records', -3))
    controls = meta.get('language_controls', {})
    vals = controls.values() if isinstance(controls, dict) else controls if isinstance(controls, list) else []
    controls_ok = True
    for c in vals:
        if c.get('coverage_complete') is not True or int(c.get('records_downloaded', -1)) != int(c.get('api_total', -2)):
            controls_ok = False
    ok = controls_ok and current == json_records == csv_records and len(rows) == current and meta.get('coverage_complete') is True and meta.get('validation_status') == 'OK'
    return ok, current, rows

def load_history():
    if not HISTORY.exists():
        return set()
    try:
        return set(json.loads(HISTORY.read_text(encoding='utf-8')).get('ids', []))
    except Exception:
        return set()

def key(source, ident):
    return f'{source}|{ident}'.strip()

def compact_record(source, r):
    if source == 'BID':
        ident = str(r.get('id') or r.get('tender_id') or r.get('reference') or r.get('reference_no') or '')
        return {
            'Fuente': source,
            'Identificador': ident,
            'Oportunidad': r.get('title') or '',
            'Institución/Entidad': r.get('organization') or r.get('institution') or r.get('agency') or '',
            'País': r.get('country') or '',
            'Monto': f"USD {r.get('amount')}" if r.get('amount') not in (None, '', 0) else 'No informado',
            'Fecha de publicación': r.get('date_created') or 'No informado',
            'Cierre': r.get('date_close') or 'No informado',
            'Fuente/Enlace': r.get('url') or r.get('link') or r.get('source_url') or '',
            'Texto': r.get('description') or r.get('content') or r.get('scope') or '',
        }
    if source == 'SICOP':
        ident = str(r.get('instCartelNo') or '')
        return {
            'Fuente': source,
            'Identificador': ident,
            'Oportunidad': r.get('cartelNm') or r.get('title') or r.get('objeto') or r.get('description') or '',
            'Institución/Entidad': r.get('cartelInstNm') or '',
            'País': 'Costa Rica',
            'Monto': 'No informado',
            'Fecha de publicación': r.get('publication_date') or r.get('regDt') or 'No informado',
            'Cierre': r.get('openbidDt') or 'No informado',
            'Fuente/Enlace': r.get('url') or r.get('link') or '',
            'Texto': r.get('cartelNm') or r.get('description') or '',
        }
    ident = str(r.get('notice_id') or r.get('reference_no') or '')
    return {
        'Fuente': source,
        'Identificador': ident,
        'Oportunidad': r.get('title') or '',
        'Institución/Entidad': r.get('institution') or r.get('project_name') or '',
        'País': r.get('country') or '',
        'Monto': 'No informado',
        'Fecha de publicación': r.get('publication_date') or 'No informado',
        'Cierre': r.get('deadline') or 'No informado',
        'Fuente/Enlace': r.get('source_url') or '',
        'Texto': r.get('notice_text') or '',
        'project_id': r.get('project_id'),
        'reference_no': r.get('reference_no'),
        'language': r.get('language'),
        'procurement_method': r.get('procurement_method'),
    }

def main():
    bid_data, bid_meta = get_json(URLS['bid_data']), get_json(URLS['bid_meta'])
    sicop_data, sicop_meta = get_json(URLS['sicop_data']), get_json(URLS['sicop_meta'])
    wb_data, wb_meta = get_json(URLS['wb_data']), get_json(URLS['wb_meta'])

    bid_ok, bid_total, bid_rows = validate_bid(bid_data, bid_meta)
    sicop_ok, sicop_total, sicop_rows = validate_sicop(sicop_data, sicop_meta)
    wb_ok, wb_total, wb_rows = validate_wb(wb_data, wb_meta)

    print(f'BID: {len(bid_rows)}/{bid_total} OK={bid_ok}')
    print(f'SICOP: {len(sicop_rows)}/{sicop_total} OK={sicop_ok}')
    print(f'Banco Mundial: {len(wb_rows)}/{wb_total} OK={wb_ok}')

    if not (bid_ok and sicop_ok and wb_ok):
        print('Cobertura invalida. No se publica salida parcial.', file=sys.stderr)
        sys.exit(3)

    history = load_history()
    nuevos = []
    for source, rows in [('BID', bid_rows), ('SICOP', sicop_rows), ('Banco Mundial', wb_rows)]:
        for r in rows:
            rec = compact_record(source, r)
            k = key(source, rec['Identificador'])
            if rec['Identificador'] and k not in history:
                rec['source_key'] = k
                nuevos.append(rec)

    generated = datetime.now(timezone.utc).isoformat()
    payload = {
        'generated_at': generated,
        'coverage': {
            'BID': f'{bid_total}/{bid_total}',
            'SICOP': f'{sicop_total}/{sicop_total}',
            'Banco Mundial': f'{wb_total}/{wb_total}',
        },
        'pendientes': nuevos,
        'nota': 'Archivo tecnico sin clasificacion semantica. Debe ser clasificado en ChatGPT antes de alimentar actualizacion_monitoreo.json.'
    }
    PEND.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    META_OUT.write_text(json.dumps({
        'generated_at': generated,
        'coverage_complete': True,
        'validation_status': 'OK',
        'bid_records': bid_total,
        'sicop_records': sicop_total,
        'worldbank_records': wb_total,
        'pending_new_ids': len(nuevos),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'OK: {len(nuevos)} IDs nuevos pendientes de clasificacion.')

if __name__ == '__main__':
    main()
